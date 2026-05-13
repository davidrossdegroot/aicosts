"""Claude Pro subscription quota via the undocumented oauth/usage endpoint.

Reads the OAuth access token from ~/.claude/.credentials.json (written by
Claude Code). No manual login required if Claude Code is already installed
and authenticated.

Endpoint: GET https://api.anthropic.com/api/oauth/usage
Auth: Bearer <accessToken> + anthropic-beta: oauth-2025-04-20

Returns utilization percentages (0-100) per window; no absolute used/limit
values are exposed by this endpoint.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from aicosts.providers.base import PullResult
from aicosts.storage import db

PROVIDER = "claude"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
BETA_HEADER = "oauth-2025-04-20"
CREDS_PATH = Path.home() / ".claude" / ".credentials.json"

_WINDOWS = {
    "five_hour": ("fiveHour", timedelta(hours=5)),
    "seven_day": ("weekly",   timedelta(days=7)),
}


def _token() -> str:
    if not CREDS_PATH.exists():
        raise SystemExit(
            f"{CREDS_PATH} not found. Authenticate Claude Code first (claude login)."
        )
    data = json.loads(CREDS_PATH.read_text())
    token = data.get("accessToken") or data.get("access_token")
    if not token:
        raise SystemExit(f"No accessToken in {CREDS_PATH}. Re-run: claude login")
    return token


def pull(since=None, until=None) -> PullResult:
    """Fetch Claude Pro subscription quota windows and store snapshots."""
    token = _token()

    resp = httpx.get(
        USAGE_URL,
        headers={"Authorization": f"Bearer {token}", "anthropic-beta": BETA_HEADER},
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()

    pulled_at = datetime.now(UTC).isoformat()

    with db.session() as conn:
        for api_key, (window_name, duration) in _WINDOWS.items():
            entry = data.get(api_key)
            if not entry:
                continue
            pct = entry.get("utilization")
            reset_at_str = entry.get("resets_at")
            reset_at = datetime.fromisoformat(reset_at_str) if reset_at_str else None
            window_start_at = (reset_at - duration).isoformat() if reset_at else pulled_at

            db.insert_window_snapshot(
                conn,
                provider=PROVIDER,
                window=window_name,
                unit="percent",
                pulled_at=pulled_at,
                window_start_at=window_start_at,
                reset_at=reset_at_str,
                used=0.0,
                limit=None,
                remaining=None,
                percent_used=float(pct) if pct is not None else None,
            )

    return PullResult(provider=PROVIDER)
