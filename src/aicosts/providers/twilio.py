"""Twilio Usage Records API: daily cost data per category."""
from __future__ import annotations

from datetime import UTC, date, datetime

import httpx

from aicosts.config import require_secret
from aicosts.providers.base import PullResult
from aicosts.storage import db, raw

PROVIDER = "twilio"
BASE = "https://api.twilio.com"


def _client() -> tuple[httpx.Client, str]:
    account_sid = require_secret(
        "twilio-account-sid",
        "Twilio Account SID (ACxxx...). Find at https://console.twilio.com — top of the Console dashboard.",
    )
    if not account_sid.startswith("AC"):
        raise SystemExit(
            f"twilio-account-sid looks wrong (got '{account_sid[:6]}...'). "
            "It must be the Account SID starting with 'AC', not an API Key SID (SK...). "
            "Run: aicosts keys set twilio-account-sid"
        )
    api_key = require_secret(
        "twilio-api-key",
        "Twilio API Key SID (SKxxx...). Create at https://console.twilio.com/us1/account/keys-credentials/api-keys",
    )
    api_secret = require_secret(
        "twilio-api-secret",
        "Twilio API Key Secret. Shown once when creating the API key.",
    )
    client = httpx.Client(
        base_url=BASE,
        auth=(api_key, api_secret),
        timeout=30.0,
    )
    return client, account_sid


def _fetch_paginated(client: httpx.Client, path: str, params: dict) -> list[dict]:
    rows: list[dict] = []
    url: str | None = path
    current_params: dict | None = params
    while url:
        if current_params is not None:
            resp = client.get(url, params=current_params)
        else:
            resp = client.get(url)
        resp.raise_for_status()
        body = resp.json()
        raw.append(
            PROVIDER,
            {
                "path": url,
                "request_url": str(resp.request.url),
                "response": body,
            },
        )
        rows.extend(body.get("usage_records", []))
        next_uri = body.get("next_page_uri")
        if next_uri:
            # next_page_uri is a full path on the same host; drop params since they're baked in
            url = next_uri
            current_params = None
        else:
            break
    return rows


def pull(since: date, until: date | None = None) -> PullResult:
    """Pull daily usage records from the Twilio Usage Records API."""
    until = until or date.today()
    inserted = updated = 0
    pulled_at = datetime.now(UTC).isoformat()

    client, account_sid = _client()
    with client, db.session() as conn:
        records = _fetch_paginated(
            client,
            f"/2010-04-01/Accounts/{account_sid}/Usage/Records/Daily.json",
            {
                "StartDate": since.isoformat(),
                "EndDate": until.isoformat(),
                "PageSize": 1000,
            },
        )

        for record in records:
            start = record.get("start_date", "")[:10]
            end = record.get("end_date", "")[:10]
            if not start:
                continue
            price_str = record.get("price") or "0"
            try:
                cost_usd = float(price_str)
            except ValueError:
                cost_usd = 0.0
            if cost_usd == 0.0:
                continue
            category = record.get("category") or ""
            sid = record.get("account_sid") or account_sid
            ins, upd = db.upsert_usage_event(
                conn,
                provider=PROVIDER,
                bucket_start=start,
                bucket_end=end,
                granularity="1d",
                workspace_id=sid,
                model=category,
                cost_usd=cost_usd,
                cost_estimated=False,
                raw_ref=f"usage_records_daily:{start}",
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
