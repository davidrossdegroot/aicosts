"""Claude Pro subscription quota via the undocumented oauth/usage endpoint.

Reads the OAuth access token from one of Claude Code's supported local auth
sources. Older Claude Code builds wrote ~/.claude/.credentials.json; newer
macOS native builds may keep the token in Keychain instead.

Endpoint: GET https://api.anthropic.com/api/oauth/usage
Auth: Bearer <accessToken> + anthropic-beta: oauth-2025-04-20

Returns utilization percentages (0-100) per window; no absolute used/limit
values are exposed by this endpoint.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from aicosts.providers.base import PullResult
from aicosts.storage import db

PROVIDER = "claude"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
BETA_HEADER = "oauth-2025-04-20"
CREDS_PATH = Path.home() / ".claude" / ".credentials.json"
ENV_TOKEN_NAMES = ("CLAUDE_CODE_OAUTH_TOKEN", "AICOSTS_CLAUDE_OAUTH_TOKEN")
ENV_CREDS_PATH = "AICOSTS_CLAUDE_CREDENTIALS_PATH"
ENV_KEYCHAIN_SERVICE = "AICOSTS_CLAUDE_KEYCHAIN_SERVICE"
KEYCHAIN_SERVICES = (
    "Claude Code-credentials",
    "Claude Code",
    "Claude Code Credentials",
    "Claude Code OAuth",
    "claude-code",
    "com.anthropic.claude-code",
)

_WINDOWS = {
    "five_hour": ("fiveHour", timedelta(hours=5)),
    "seven_day": ("weekly",   timedelta(days=7)),
}


def _extract_token(raw: str) -> str | None:
    """Return an OAuth access token from raw JSON or a plain token string."""
    value = raw.strip()
    if not value:
        return None

    if value.startswith("{"):
        data = json.loads(value)
        token = (
            data.get("accessToken")
            or data.get("access_token")
            or data.get("claudeAiOauth", {}).get("accessToken")
            or data.get("claudeAiOauth", {}).get("access_token")
        )
        return str(token).strip() if token else None

    return value


def _token_from_file(path: Path) -> str | None:
    if not path.exists():
        return None
    return _extract_token(path.read_text())


def _token_from_env() -> str | None:
    for name in ENV_TOKEN_NAMES:
        value = os.environ.get(name)
        if value:
            return _extract_token(value)
    return None


def _token_from_keychain() -> str | None:
    if platform.system() != "Darwin":
        return None

    services = []
    configured = os.environ.get(ENV_KEYCHAIN_SERVICE)
    if configured:
        services.append(configured)
    services.extend(KEYCHAIN_SERVICES)

    for service in dict.fromkeys(services):
        proc = subprocess.run(
            ["security", "find-generic-password", "-w", "-s", service],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            continue
        token = _extract_token(proc.stdout)
        if token:
            return token
    return None


def _token() -> str:
    token = _token_from_env()
    if token:
        return token

    creds_path = Path(os.environ.get(ENV_CREDS_PATH, CREDS_PATH))
    token = _token_from_file(creds_path)
    if token:
        return token

    token = _token_from_keychain()
    if token:
        return token

    raise SystemExit(
        "Claude OAuth token not found. Looked in "
        f"{', '.join(ENV_TOKEN_NAMES)}, {creds_path}, and macOS Keychain. "
        "Authenticate Claude Code first (`claude auth login`) or provide "
        f"{ENV_TOKEN_NAMES[0]} / {ENV_CREDS_PATH}."
    )


def pull(since=None, until=None) -> PullResult:
    """Fetch Claude Pro subscription quota windows and store snapshots."""
    token = _token()

    resp = httpx.get(
        USAGE_URL,
        headers={"Authorization": f"Bearer {token}", "anthropic-beta": BETA_HEADER},
        timeout=15.0,
    )
    if resp.status_code in (401, 403):
        # The OAuth token expired or was revoked. Surface the one-liner fix instead
        # of an opaque HTTPStatusError traceback (issue #18). pull() catches
        # SystemExit per-provider, so other providers still complete.
        raise SystemExit(
            f"Claude OAuth token rejected ({resp.status_code}). Your Claude Code "
            "session has expired. Re-authenticate, then re-run `aicosts pull`:\n"
            "    claude auth login --claudeai"
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
