"""Claude Pro subscription quota via the undocumented oauth/usage endpoint.

Token lookup order (first match wins):
  1. CLAUDE_CODE_OAUTH_TOKEN env var (CI / headless)
  2. ~/.claude/.credentials.json  (Linux default; macOS SSH fallback)
     JSON path: .claudeAiOauth.accessToken  (Claude Code 2.x)
                .accessToken                (pre-2.x legacy)
  3. macOS Keychain  service="Claude Code-credentials"  account=$(whoami)
     JSON path: same as above

Endpoint: GET https://api.anthropic.com/api/oauth/usage
Auth: Bearer <accessToken> + anthropic-beta: oauth-2025-04-20

Returns utilization percentages (0-100) per window; no absolute used/limit
values are exposed by this endpoint.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
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


def _extract_token(data: dict) -> str | None:
    """Pull accessToken from either the 2.x or legacy credential JSON."""
    if oauth := data.get("claudeAiOauth"):
        return oauth.get("accessToken")
    return data.get("accessToken") or data.get("access_token")


def _token() -> str:
    # 1. Env var (CI / headless / setup-token workflow)
    if token := os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return token

    # 2. Credentials file (Linux default; macOS SSH sessions)
    if CREDS_PATH.exists():
        data = json.loads(CREDS_PATH.read_text())
        if token := _extract_token(data):
            return token

    # 3. macOS Keychain  (service "Claude Code-credentials", account=$(whoami))
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                [
                    "security", "find-generic-password",
                    "-s", "Claude Code-credentials",
                    "-a", os.environ.get("USER", ""),
                    "-w",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            data = json.loads(result.stdout.strip())
            if token := _extract_token(data):
                return token
        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError):
            pass

    raise SystemExit(
        "Claude OAuth token not found. Tried:\n"
        "  CLAUDE_CODE_OAUTH_TOKEN env var\n"
        f"  {CREDS_PATH}\n"
        '  macOS Keychain service "Claude Code-credentials"\n'
        "Authenticate with `claude login` or set CLAUDE_CODE_OAUTH_TOKEN."
    )


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
