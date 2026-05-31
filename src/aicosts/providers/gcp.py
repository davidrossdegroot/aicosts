"""Google Cloud Billing: query BigQuery standard billing export for daily cost data.

Requires:
- GCP_SERVICE_ACCOUNT_KEY  — service account JSON (full key blob)
- GCP_BILLING_DATASET      — BigQuery dataset name (default: gcp_costs)
"""
from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta

from google.cloud import bigquery
from google.oauth2 import service_account

from aicosts.config import require_secret
from aicosts.providers.base import PullResult
from aicosts.storage import db

PROVIDER = "gcp"

# Querying the BigQuery billing export is itself billable, so gcp is excluded from
# the default `aicosts pull` and only runs on demand (`--provider gcp`). For a free
# at-a-glance view we point at the billing console instead (issue #19).
DEFAULT_CONSOLE_URL = (
    "https://console.cloud.google.com/billing/01797E-2401BE-C9BFE6?project=saints-podcast"
)


def console_url() -> str:
    """Billing-console URL to view GCP costs without running a billable query."""
    return os.environ.get("GCP_BILLING_CONSOLE_URL", DEFAULT_CONSOLE_URL)


def _client() -> tuple[bigquery.Client, str]:
    raw = require_secret(
        "gcp-service-account-key",
        "GCP service account key JSON blob. Set GCP_SERVICE_ACCOUNT_KEY env var.",
    )
    key = json.loads(raw)
    creds = service_account.Credentials.from_service_account_info(
        key, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    return bigquery.Client(credentials=creds, project=key["project_id"]), key["project_id"]


def _billing_table(client: bigquery.Client, project_id: str) -> str:
    dataset = os.environ.get("GCP_BILLING_DATASET", "gcp_costs")
    tables = [
        t.table_id
        for t in client.list_tables(dataset)
        if t.table_id.startswith("gcp_billing_export_v1_")
    ]
    if not tables:
        raise SystemExit(
            f"No billing export table found in {project_id}.{dataset}. "
            "GCP billing export may not be configured or data hasn't arrived yet (can take up to 24h)."
        )
    return f"`{project_id}.{dataset}.{tables[0]}`"


def pull(since: date, until: date | None = None) -> PullResult:
    until = until or date.today()
    inserted = updated = 0
    pulled_at = datetime.now(UTC).isoformat()

    client, project_id = _client()
    table_ref = _billing_table(client, project_id)

    query = f"""
        SELECT
          DATE(usage_start_time) AS usage_date,
          project.id             AS project_id,
          service.description    AS service,
          SUM(cost) + SUM(IFNULL(
            (SELECT SUM(c.amount) FROM UNNEST(credits) AS c), 0
          )) AS net_cost_usd
        FROM {table_ref}
        WHERE DATE(usage_start_time) BETWEEN @since AND @until
        GROUP BY 1, 2, 3
        ORDER BY 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("since", "DATE", since.isoformat()),
            bigquery.ScalarQueryParameter("until", "DATE", until.isoformat()),
        ]
    )

    rows = list(client.query(query, job_config=job_config).result())

    with db.session() as conn:
        for row in rows:
            cost_usd = float(row["net_cost_usd"] or 0)
            if cost_usd == 0:
                continue
            usage_date = row["usage_date"].isoformat()
            next_date = (row["usage_date"] + timedelta(days=1)).isoformat()
            ins, upd = db.upsert_usage_event(
                conn,
                provider=PROVIDER,
                bucket_start=usage_date,
                bucket_end=next_date,
                granularity="1d",
                project_id=row["project_id"] or "",
                model=row["service"] or "",
                cost_usd=cost_usd,
                cost_estimated=False,
                raw_ref=f"bq:{usage_date}",
            )
            inserted += ins
            updated += upd

        db.log_pull(
            conn,
            provider=PROVIDER,
            pulled_at=pulled_at,
            since=since.isoformat(),
            until=until.isoformat(),
            rows_inserted=inserted,
            rows_updated=updated,
        )

    return PullResult(
        provider=PROVIDER,
        rows_inserted=inserted,
        rows_updated=updated,
        since=since,
        until=until,
    )
