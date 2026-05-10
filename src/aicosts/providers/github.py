"""GitHub Enhanced Billing API: daily cost data per product/SKU/repo.

Requires the account to have transitioned to GitHub's metered billing platform.
See: https://docs.github.com/en/billing/tutorials/automate-usage-reporting

Uses grossAmount (pre-discount) so free-tier consumption is visible.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx

from aicosts.config import require_secret
from aicosts.providers.base import PullResult
from aicosts.storage import db, raw

PROVIDER = "github"
BASE = "https://api.github.com"
API_VERSION = "2026-03-10"


def _client() -> tuple[httpx.Client, str]:
    token = require_secret(
        "github-token",
        "GitHub fine-grained PAT with 'Plan' (read) permission. "
        "Create at https://github.com/settings/tokens?type=beta",
    )
    account = require_secret(
        "github-org",
        "GitHub user or organization name. Set GITHUB_ORG env var or: aicosts keys set github-org",
    )
    client = httpx.Client(
        base_url=BASE,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        },
        timeout=30.0,
    )
    return client, account


def _billing_path(client: httpx.Client, account: str) -> str:
    r = client.get(f"/users/{account}")
    if r.is_success and r.json().get("type") == "Organization":
        return f"/orgs/{account}/settings/billing/usage"
    return f"/users/{account}/settings/billing/usage"


def _months_in_range(since: date, until: date) -> list[tuple[int, int]]:
    months = []
    d = since.replace(day=1)
    end = until.replace(day=1)
    while d <= end:
        months.append((d.year, d.month))
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months


def pull(since: date, until: date | None = None) -> PullResult:
    """Pull billing usage from the GitHub Enhanced Billing API."""
    until = until or date.today()
    inserted = updated = 0
    pulled_at = datetime.now(UTC).isoformat()

    client, account = _client()
    with client, db.session() as conn:
        path = _billing_path(client, account)

        for year, month in _months_in_range(since, until):
            resp = client.get(path, params={"year": year, "month": month})
            if resp.status_code == 404:
                raise SystemExit(
                    f"GitHub billing API returned 404 for {account}. "
                    "The account must be transitioned to GitHub's metered billing platform. "
                    "See: https://docs.github.com/en/billing/tutorials/automate-usage-reporting"
                )
            resp.raise_for_status()
            body = resp.json()
            raw.append(PROVIDER, {"account": account, "year": year, "month": month, "response": body})

            # Aggregate by (date, repo, sku) before upserting — multiple events can
            # share the same key within a month.
            aggregated: dict[tuple[str, str, str], float] = {}
            for item in body.get("usageItems", []):
                gross = float(item.get("grossAmount") or 0)
                if gross == 0.0:
                    continue
                bucket_start = (item.get("date") or "")[:10]
                if not bucket_start:
                    continue
                repo = item.get("repositoryName") or ""
                sku = item.get("sku") or item.get("product") or ""
                key = (bucket_start, repo, sku)
                aggregated[key] = aggregated.get(key, 0.0) + gross

            for (bucket_start, repo, sku), cost_usd in aggregated.items():
                bucket_end = (date.fromisoformat(bucket_start) + timedelta(days=1)).isoformat()
                ins, upd = db.upsert_usage_event(
                    conn,
                    provider=PROVIDER,
                    bucket_start=bucket_start,
                    bucket_end=bucket_end,
                    granularity="1d",
                    workspace_id=account,
                    project_id=repo,
                    model=sku,
                    cost_usd=cost_usd,
                    cost_estimated=False,
                    raw_ref=f"billing_usage:{year}-{month:02d}",
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
