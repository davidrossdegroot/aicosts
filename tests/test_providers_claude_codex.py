"""Tests for the claude and codex subscription-quota providers.

Both providers read from external processes/files, so we test error paths
with fakes and the happy path with monkeypatched I/O.
"""
from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from aicosts import paths
from aicosts.storage import db


@pytest.fixture
def tmp_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(paths, "db_path", lambda: tmp_path / "db.sqlite")
    monkeypatch.setattr(paths, "projects_toml", lambda: tmp_path / "projects.toml")
    yield tmp_path


# ---------------------------------------------------------------------------
# claude provider
# ---------------------------------------------------------------------------

class TestClaudeProvider:
    def test_missing_credentials_file_raises(self, tmp_paths, monkeypatch):
        import aicosts.providers.claude as claude_mod
        monkeypatch.setattr(claude_mod, "CREDS_PATH", tmp_paths / "no_creds.json")
        with pytest.raises(SystemExit, match="not found"):
            claude_mod.pull()

    def test_missing_access_token_raises(self, tmp_paths, monkeypatch):
        import aicosts.providers.claude as claude_mod
        creds = tmp_paths / ".credentials.json"
        creds.write_text(json.dumps({"something_else": "value"}))
        monkeypatch.setattr(claude_mod, "CREDS_PATH", creds)
        with pytest.raises(SystemExit, match="accessToken"):
            claude_mod.pull()

    def test_happy_path_stores_snapshots(self, tmp_paths, monkeypatch):
        import aicosts.providers.claude as claude_mod

        creds = tmp_paths / ".credentials.json"
        creds.write_text(json.dumps({"accessToken": "tok_test"}))
        monkeypatch.setattr(claude_mod, "CREDS_PATH", creds)

        fake_response = {
            "five_hour": {"utilization": 42, "resets_at": "2026-05-13T17:00:00+00:00"},
            "seven_day": {"utilization": 61, "resets_at": "2026-05-20T08:00:00+00:00"},
        }

        def fake_get(url, *, headers, timeout):
            resp = MagicMock()
            resp.json.return_value = fake_response
            resp.raise_for_status = MagicMock()
            return resp

        monkeypatch.setattr(httpx, "get", fake_get)

        result = claude_mod.pull()
        assert result.provider == "claude"

        with db.session() as conn:
            rows = db.latest_window_snapshots(conn)

        by_window = {r["window"]: r for r in rows if r["provider"] == "claude"}
        assert by_window["fiveHour"]["percent_used"] == pytest.approx(42)
        assert by_window["weekly"]["percent_used"] == pytest.approx(61)
        assert by_window["fiveHour"]["unit"] == "percent"
        assert by_window["weekly"]["reset_at"] == "2026-05-20T08:00:00+00:00"


# ---------------------------------------------------------------------------
# codex provider
# ---------------------------------------------------------------------------

class TestCodecProvider:
    def test_missing_cli_raises(self, tmp_paths, monkeypatch):
        import aicosts.providers.codex as codex_mod
        monkeypatch.setattr(codex_mod.shutil, "which", lambda _: None)
        with pytest.raises(SystemExit, match="codex CLI not found"):
            codex_mod.pull()

    def test_rpc_rate_limits_parses_response(self):
        """Unit-test _rpc_rate_limits with a fake subprocess that speaks JSON-RPC."""
        import aicosts.providers.codex as codex_mod

        now_unix = int(datetime.now(UTC).timestamp())
        init_resp = json.dumps({"jsonrpc": "2.0", "id": 0, "result": {"protocolVersion": "2025-03-26"}})
        rate_resp = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"rateLimits": {
            "primary":   {"usedPercent": 5,  "windowDurationMins": 300,   "resetsAt": now_unix + 3600},
            "secondary": {"usedPercent": 18, "windowDurationMins": 10080, "resetsAt": now_unix + 86400},
        }}})

        lines = iter([init_resp + "\n", rate_resp + "\n"])

        fake_proc = MagicMock()
        fake_proc.stdout = MagicMock()
        fake_proc.stdout.readline.side_effect = lambda: next(lines)
        fake_proc.stdin = MagicMock()

        # Patch select.select to always report ready
        with patch("aicosts.providers.codex.select.select", return_value=([fake_proc.stdout], [], [])):
            result = codex_mod._rpc_rate_limits(fake_proc, timeout=1.0)

        assert result["primary"]["usedPercent"] == 5
        assert result["secondary"]["usedPercent"] == 18

    def test_happy_path_stores_snapshots(self, tmp_paths, monkeypatch):
        import aicosts.providers.codex as codex_mod

        monkeypatch.setattr(codex_mod.shutil, "which", lambda _: "/usr/bin/codex")

        now_unix = int(datetime.now(UTC).timestamp())
        fake_limits = {
            "primary":   {"usedPercent": 5,  "windowDurationMins": 300,   "resetsAt": now_unix + 3600},
            "secondary": {"usedPercent": 18, "windowDurationMins": 10080, "resetsAt": now_unix + 86400},
        }

        monkeypatch.setattr(codex_mod, "_rpc_rate_limits", lambda proc, **kw: fake_limits)

        fake_proc = MagicMock()
        fake_proc.stdin = MagicMock()
        fake_proc.wait = MagicMock()

        with patch("aicosts.providers.codex.subprocess.Popen", return_value=fake_proc):
            result = codex_mod.pull()

        assert result.provider == "codex"

        with db.session() as conn:
            rows = db.latest_window_snapshots(conn)

        by_window = {r["window"]: r for r in rows if r["provider"] == "codex"}
        assert by_window["fiveHour"]["percent_used"] == pytest.approx(5)
        assert by_window["weekly"]["percent_used"] == pytest.approx(18)
