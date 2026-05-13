"""Codex CLI subscription quota via the app-server JSON-RPC interface.

Spawns `codex app-server` (part of the Codex CLI) and calls
`account/rateLimits/read` over JSON-RPC 2.0 on stdin/stdout.
Auth is handled automatically by the app-server from ~/.codex/auth.json.

Response exposes usedPercent + resetsAt for:
  primary   — 5-hour rolling window
  secondary — 7-day weekly window

No absolute used/limit values are exposed; only percentages.
"""
from __future__ import annotations

import json
import select
import shutil
import subprocess
from datetime import UTC, datetime, timedelta

from aicosts.providers.base import PullResult
from aicosts.storage import db

PROVIDER = "codex"

_WINDOWS = {
    "primary":   ("fiveHour", None),
    "secondary": ("weekly",   timedelta(days=7)),
}


def pull(since=None, until=None) -> PullResult:
    """Fetch Codex subscription quota windows via app-server and store snapshots."""
    if not shutil.which("codex"):
        raise SystemExit(
            "codex CLI not found. Install it first: https://github.com/openai/codex"
        )

    proc = subprocess.Popen(
        ["codex", "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    try:
        rate_limits = _rpc_rate_limits(proc)
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    pulled_at = datetime.now(UTC).isoformat()

    with db.session() as conn:
        for key, (window_name, fallback_dur) in _WINDOWS.items():
            entry = rate_limits.get(key)
            if not entry:
                continue
            pct = entry.get("usedPercent")
            resets_unix = entry.get("resetsAt")
            reset_at = datetime.fromtimestamp(resets_unix, tz=UTC) if resets_unix else None
            reset_at_str = reset_at.isoformat() if reset_at else None

            window_mins = entry.get("windowDurationMins")
            if window_mins and reset_at:
                window_start = reset_at - timedelta(minutes=window_mins)
            elif fallback_dur and reset_at:
                window_start = reset_at - fallback_dur
            else:
                window_start = None

            db.insert_window_snapshot(
                conn,
                provider=PROVIDER,
                window=window_name,
                unit="percent",
                pulled_at=pulled_at,
                window_start_at=window_start.isoformat() if window_start else pulled_at,
                reset_at=reset_at_str,
                used=0.0,
                limit=None,
                remaining=None,
                percent_used=float(pct) if pct is not None else None,
            )

    return PullResult(provider=PROVIDER)


def _rpc_rate_limits(proc: subprocess.Popen, timeout: float = 10.0) -> dict:
    """Run the initialize handshake then call account/rateLimits/read."""

    def _send(msg: dict) -> None:
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()

    def _readline() -> dict:
        ready, _, _ = select.select([proc.stdout], [], [], timeout)
        if not ready:
            raise TimeoutError("codex app-server did not respond within timeout")
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("codex app-server closed unexpectedly")
        return json.loads(line)

    def _read_until_id(target_id: int) -> dict:
        while True:
            msg = _readline()
            if msg.get("id") == target_id:
                return msg

    _send({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
        "clientInfo": {"name": "aicosts", "title": "aicosts", "version": "0.1.0"},
    }})
    _read_until_id(0)
    _send({"jsonrpc": "2.0", "method": "initialized", "params": {}})

    _send({"jsonrpc": "2.0", "id": 1, "method": "account/rateLimits/read", "params": {}})
    resp = _read_until_id(1)

    if "error" in resp:
        raise RuntimeError(f"account/rateLimits/read failed: {resp['error']}")
    return resp.get("result", {}).get("rateLimits", {})
