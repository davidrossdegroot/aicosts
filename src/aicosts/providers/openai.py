"""OpenAI Admin API: organization/costs (dollars, per-day, groupable by project)."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx

from aicosts.config import require_secret
from aicosts.providers.base import PullResult
from aicosts.storage import db, raw

PROVIDER = "openai"
BASE = "https://api.openai.com/v1/organization"


def _client() -> httpx.Client:
    key = require_secret(
        "openai-admin-key",
        "OpenAI Admin API key. Create at https://platform.openai.com/settings/organization/admin-keys",
    )
    return httpx.Client(
        base_url=BASE,
        headers={"Authorization": f"Bearer {key}"},
        timeout=30.0,
    )


def _fetch_paginated(client: httpx.Client, path: str, params: dict) -> list[dict]:
    rows: list[dict] = []
    after: str | None = None
    while True:
        q = dict(params)
        if after:
            q["page"] = after
        resp = client.get(path, params=q)
        resp.raise_for_status()
        body = resp.json()
        raw.append(PROVIDER, {"path": path, "params": q, "response": body})
        rows.extend(body.get("data", []))
        if not body.get("has_more"):
            break
        after = body.get("next_page")
        if not after:
            break
    return rows


def pull(since: date, until: date | None = None) -> PullResult:
    """Pull /organization/costs broken down by project_id."""
    until = until or date.today()
    inserted = updated = 0
    pulled_at = datetime.now(UTC).isoformat()

    start_unix = int(datetime.combine(since, datetime.min.time(), tzinfo=UTC).timestamp())
    end_unix = int(datetime.combine(until + timedelta(days=1), datetime.min.time(), tzinfo=UTC).timestamp())

    with _client() as client, db.session() as conn:
        rows = _fetch_paginated(
            client,
            "/costs",
            {
                "start_time": start_unix,
                "end_time": end_unix,
                "bucket_width": "1d",
                "group_by": ["project_id", "line_item"],
                "limit": 180,
            },
        )

        for bucket in rows:
            start_ts = bucket["start_time"]
            end_ts = bucket["end_time"]
            start_iso = datetime.fromtimestamp(start_ts, tz=UTC).date().isoformat()
            end_iso = datetime.fromtimestamp(end_ts, tz=UTC).date().isoformat()
            for result in bucket.get("results", []):
                amount = result.get("amount", {})
                cost_usd = float(amount.get("value", 0))
                project_id = result.get("project_id")
                line_item = result.get("line_item")
                ins, upd = db.upsert_usage_event(
                    conn,
                    provider=PROVIDER,
                    bucket_start=start_iso,
                    bucket_end=end_iso,
                    granularity="1d",
                    project_id=project_id,
                    model=line_item,
                    cost_usd=cost_usd,
                    cost_estimated=False,
                    raw_ref=f"costs:{start_iso}",
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
