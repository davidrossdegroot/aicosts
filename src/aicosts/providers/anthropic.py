"""Anthropic Admin API: usage_report (token counts) + cost_report (dollars).

Strategy:
- For finalized days (D-2 and earlier), pull cost_report — authoritative dollars.
- For today and yesterday, pull usage_report — token counts; cost is estimated via a
  static pricing table. These rows are flagged cost_estimated=1 and replaced when
  cost_report catches up.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx

from aicosts.config import require_secret
from aicosts.providers.base import PullResult
from aicosts.storage import db, raw

PROVIDER = "anthropic"
BASE = "https://api.anthropic.com/v1/organizations"
API_VERSION = "2023-06-01"

# Per-million-token prices (USD). Used only when cost_report data isn't available yet.
# Update when Anthropic ships new models or pricing changes.
PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-7": {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.50},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.0, "cache_write": 1.0, "cache_read": 0.08},
}


def _client() -> httpx.Client:
    key = require_secret(
        "anthropic-admin-key",
        "Anthropic Admin API key (sk-ant-admin-...). Create at https://console.anthropic.com/settings/admin-keys",
    )
    return httpx.Client(
        base_url=BASE,
        headers={
            "x-api-key": key,
            "anthropic-version": API_VERSION,
        },
        timeout=30.0,
    )


def _estimate_cost(model: str, tokens: dict[str, int]) -> float:
    pricing = PRICING.get(model)
    if not pricing:
        # Unknown model — return 0 and flag elsewhere; keeps the row queryable.
        return 0.0
    return (
        tokens.get("input", 0) * pricing["input"] / 1_000_000
        + tokens.get("output", 0) * pricing["output"] / 1_000_000
        + tokens.get("cache_creation", 0) * pricing["cache_write"] / 1_000_000
        + tokens.get("cached_input", 0) * pricing["cache_read"] / 1_000_000
    )


def _fetch_paginated(client: httpx.Client, path: str, params: dict) -> list[dict]:
    """Yield all pages from a paginated Anthropic admin endpoint."""
    rows: list[dict] = []
    page_token: str | None = None
    while True:
        q = dict(params)
        if page_token:
            q["page"] = page_token
        resp = client.get(path, params=q)
        resp.raise_for_status()
        body = resp.json()
        raw.append(PROVIDER, {"path": path, "params": q, "response": body})
        rows.extend(body.get("data", []))
        page_token = body.get("next_page")
        if not page_token:
            break
    return rows


def pull(since: date, until: date | None = None) -> PullResult:
    """Pull cost_report for finalized days, usage_report for the recent edge."""
    until = until or date.today()
    today = date.today()
    finalized_cutoff = today - timedelta(days=2)

    inserted = updated = 0
    pulled_at = datetime.now(UTC).isoformat()

    with _client() as client, db.session() as conn:
        # 1. cost_report for the finalized window. Returns dollars.
        if since <= finalized_cutoff:
            cost_until = min(until, finalized_cutoff + timedelta(days=1))
            rows = _fetch_paginated(
                client,
                "/cost_report",
                {
                    "starting_at": since.isoformat(),
                    "ending_at": cost_until.isoformat(),
                    "bucket_width": "1d",
                    "group_by[]": ["workspace_id", "description"],
                },
            )
            for bucket in rows:
                start = bucket["starting_at"]
                end = bucket["ending_at"]
                for result in bucket.get("results", []):
                    cost_usd = float(result.get("amount", 0)) / 100.0  # API returns cents
                    workspace_id = result.get("workspace_id")
                    description = result.get("description") or ""
                    ins, upd = db.upsert_usage_event(
                        conn,
                        provider=PROVIDER,
                        bucket_start=start,
                        bucket_end=end,
                        granularity="1d",
                        workspace_id=workspace_id,
                        model=description,
                        cost_usd=cost_usd,
                        cost_estimated=False,
                        raw_ref=f"cost_report:{start}",
                    )
                    inserted += ins
                    updated += upd

        # 2. usage_report for the recent edge (today + yesterday). Token-based; we estimate cost.
        edge_start = max(since, finalized_cutoff + timedelta(days=1))
        if edge_start <= until:
            rows = _fetch_paginated(
                client,
                "/usage_report/messages",
                {
                    "starting_at": edge_start.isoformat(),
                    "ending_at": (until + timedelta(days=1)).isoformat(),
                    "bucket_width": "1d",
                    "group_by[]": ["workspace_id", "model"],
                },
            )
            for bucket in rows:
                start = bucket["starting_at"]
                end = bucket["ending_at"]
                for result in bucket.get("results", []):
                    workspace_id = result.get("workspace_id")
                    model = result.get("model") or ""
                    tokens = {
                        "input": result.get("uncached_input_tokens", 0),
                        "output": result.get("output_tokens", 0),
                        "cached_input": result.get("cache_read_input_tokens", 0),
                        "cache_creation": result.get("cache_creation_input_tokens", 0),
                    }
                    cost_usd = _estimate_cost(model, tokens)
                    ins, upd = db.upsert_usage_event(
                        conn,
                        provider=PROVIDER,
                        bucket_start=start,
                        bucket_end=end,
                        granularity="1d",
                        workspace_id=workspace_id,
                        model=model,
                        cost_usd=cost_usd,
                        cost_estimated=True,
                        input_tokens=tokens["input"],
                        output_tokens=tokens["output"],
                        cached_input_tokens=tokens["cached_input"],
                        cache_creation_tokens=tokens["cache_creation"],
                        raw_ref=f"usage_report:{start}",
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
