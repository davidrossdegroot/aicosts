"""Exercise storage + reports without hitting external APIs.

We bypass the user-data-dir paths by pointing the DB and projects.toml at a tmp
directory through monkeypatching aicosts.paths.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from click.testing import CliRunner

from aicosts import paths, reports
from aicosts.cli import main
from aicosts.storage import db


@pytest.fixture
def tmp_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(paths, "db_path", lambda: tmp_path / "db.sqlite")
    monkeypatch.setattr(paths, "projects_toml", lambda: tmp_path / "projects.toml")
    yield tmp_path


def _seed(today: date) -> None:
    yesterday = (today - timedelta(days=1)).isoformat()
    today_iso = today.isoformat()
    with db.session() as conn:
        # Anthropic finalized (yesterday)
        db.upsert_usage_event(
            conn,
            provider="anthropic",
            bucket_start=yesterday,
            bucket_end=yesterday,
            granularity="1d",
            workspace_id="wrkspc_default",
            model="claude-opus-4-7",
            cost_usd=12.34,
            cost_estimated=False,
        )
        # Anthropic estimated (today)
        db.upsert_usage_event(
            conn,
            provider="anthropic",
            bucket_start=today_iso,
            bucket_end=today_iso,
            granularity="1d",
            workspace_id="wrkspc_openclaw",
            model="claude-sonnet-4-6",
            cost_usd=2.50,
            cost_estimated=True,
            input_tokens=500_000,
            output_tokens=80_000,
        )
        # OpenAI finalized (today)
        db.upsert_usage_event(
            conn,
            provider="openai",
            bucket_start=today_iso,
            bucket_end=today_iso,
            granularity="1d",
            project_id="proj_voice",
            model="gpt-realtime",
            cost_usd=4.20,
            cost_estimated=False,
        )
        # GCP finalized (today)
        db.upsert_usage_event(
            conn,
            provider="gcp",
            bucket_start=today_iso,
            bucket_end=today_iso,
            granularity="1d",
            project_id="gcp_voice",
            model="Cloud Text-to-Speech API",
            cost_usd=0.51,
            cost_estimated=False,
        )


def test_summarize_by_provider_today(tmp_paths):
    today = date.today()
    _seed(today)
    rows = reports.summarize("today", "provider")
    by_label = {r.label: r for r in rows}
    assert by_label["openai"].cost_usd == pytest.approx(4.20)
    assert by_label["anthropic"].cost_usd == pytest.approx(2.50)
    assert by_label["anthropic"].estimated is True
    assert by_label["openai"].estimated is False


def test_summarize_by_project_uses_local_mapping(tmp_paths):
    today = date.today()
    _seed(today)
    (tmp_paths / "projects.toml").write_text(
        '[[project]]\n'
        'label = "openclaw-agent"\n'
        'anthropic_workspace_ids = ["wrkspc_openclaw"]\n'
        '[[project]]\n'
        'label = "voice-calls"\n'
        'openai_project_ids = ["proj_voice"]\n'
        'gcp_project_ids = ["gcp_voice"]\n'
    )
    rows = reports.summarize("today", "project")
    labels = [r.label for r in rows]
    assert "openclaw-agent" in labels
    assert "voice-calls" in labels


def test_report_drops_zero_cost_rows(tmp_paths):
    """Issue #10 — rows that round to $0.00 are hidden from the report table."""
    today = date.today().isoformat()
    with db.session() as conn:
        db.upsert_usage_event(
            conn, provider="openai", bucket_start=today, bucket_end=today,
            granularity="1d", model="alpha-model", cost_usd=4.20,
        )
        db.upsert_usage_event(
            conn, provider="openai", bucket_start=today, bucket_end=today,
            granularity="1d", model="zero-model", cost_usd=0.0004,
        )
    runner = CliRunner()
    result = runner.invoke(main, ["report", "--period", "today", "--by", "model"])
    assert result.exit_code == 0
    assert "alpha-model" in result.output
    assert "zero-model" not in result.output


def test_status_line(tmp_paths):
    today = date.today()
    _seed(today)
    line = reports.status_line()
    assert "$7.21" in line  # 2.50 + 4.20 + 0.51
    assert "anthropic" in line
    assert "openai" in line
    assert "gcp" in line


def test_projects_command_shows_unmapped(tmp_paths):
    today = date.today()
    _seed(today)
    runner = CliRunner()
    result = runner.invoke(main, ["projects"])
    assert result.exit_code == 0
    assert "wrkspc_openclaw" in result.output
    assert "proj_voice" in result.output
    assert "gcp_voice" in result.output
    assert "Unmapped" in result.output


def test_projects_command_shows_mapped(tmp_paths):
    today = date.today()
    _seed(today)
    (tmp_paths / "projects.toml").write_text(
        '[[project]]\n'
        'label = "openclaw-agent"\n'
        'anthropic_workspace_ids = ["wrkspc_openclaw", "wrkspc_default"]\n'
        '[[project]]\n'
        'label = "voice-calls"\n'
        'openai_project_ids = ["proj_voice"]\n'
        'gcp_project_ids = ["gcp_voice"]\n'
    )
    runner = CliRunner()
    result = runner.invoke(main, ["projects"])
    assert result.exit_code == 0
    assert "All usage IDs are mapped" in result.output


def test_projects_add_creates_mapping(tmp_paths):
    today = date.today()
    _seed(today)
    runner = CliRunner()

    result = runner.invoke(main, ["projects", "add", "openclaw-agent",
                                  "--anthropic-workspace", "wrkspc_openclaw",
                                  "--anthropic-workspace", "wrkspc_default"])
    assert result.exit_code == 0
    assert "openclaw-agent" in result.output

    result = runner.invoke(main, ["projects", "add", "voice-calls",
                                  "--openai-project", "proj_voice",
                                  "--gcp-project", "gcp_voice"])
    assert result.exit_code == 0

    rows = reports.summarize("today", "project")
    labels = [r.label for r in rows]
    assert "openclaw-agent" in labels
    assert "voice-calls" in labels


def test_projects_add_merges_existing(tmp_paths):
    runner = CliRunner()
    runner.invoke(main, ["projects", "add", "my-agent",
                         "--anthropic-workspace", "wrkspc_1"])
    runner.invoke(main, ["projects", "add", "my-agent",
                         "--anthropic-workspace", "wrkspc_2",
                         "--openai-project", "proj_1",
                         "--gcp-project", "gcp_1"])

    import tomlkit
    doc = tomlkit.parse((tmp_paths / "projects.toml").read_text())
    entry = doc["project"][0]
    assert "wrkspc_1" in entry["anthropic_workspace_ids"]
    assert "wrkspc_2" in entry["anthropic_workspace_ids"]
    assert "proj_1" in entry["openai_project_ids"]
    assert "gcp_1" in entry["gcp_project_ids"]


def test_projects_add_requires_at_least_one_id(tmp_paths):
    runner = CliRunner()
    result = runner.invoke(main, ["projects", "add", "my-agent"])
    assert result.exit_code != 0


def test_gcp_excluded_from_default_pull(tmp_paths, monkeypatch):
    """Issue #19 — default pull skips gcp and prints the billing-console hint."""
    import aicosts.cli as cli_mod

    pulled: list[str] = []

    class _FakeMod:
        def __init__(self, name):
            self.name = name

        def pull(self, since, until):
            from aicosts.providers.base import PullResult
            pulled.append(self.name)
            return PullResult(provider=self.name)

    monkeypatch.setattr(cli_mod, "import_module",
                        lambda path: _FakeMod(path.rsplit(".", 1)[-1]))

    runner = CliRunner()
    result = runner.invoke(main, ["pull"])
    assert result.exit_code == 0
    assert "gcp" not in pulled
    assert "anthropic" in pulled
    assert "subscriptions" in pulled
    assert "console.cloud.google.com" in result.output
    assert "--provider gcp" in result.output


def test_gcp_pulled_on_demand(tmp_paths, monkeypatch):
    """Issue #19 — gcp still pulls when explicitly requested."""
    import aicosts.cli as cli_mod

    pulled: list[str] = []

    class _FakeMod:
        def pull(self, since, until):
            from aicosts.providers.base import PullResult
            pulled.append("gcp")
            return PullResult(provider="gcp")

    monkeypatch.setattr(cli_mod, "import_module", lambda path: _FakeMod())

    runner = CliRunner()
    result = runner.invoke(main, ["pull", "--provider", "gcp"])
    assert result.exit_code == 0
    assert pulled == ["gcp"]
    # No nag hint when the user explicitly asked for gcp.
    assert "--provider gcp" not in result.output


def test_usage_command_no_data(tmp_paths):
    runner = CliRunner()
    result = runner.invoke(main, ["usage"])
    assert result.exit_code != 0
    assert "aicosts pull" in result.output


def test_usage_command_returns_json(tmp_paths):
    today = date.today()
    _seed(today)

    # Manually insert window snapshots for both providers
    with db.session() as conn:
        db.insert_window_snapshot(
            conn,
            provider="anthropic",
            window="today",
            unit="usd",
            pulled_at="2026-01-01T00:00:00+00:00",
            window_start_at="2026-01-01T00:00:00+00:00",
            reset_at="2026-01-02T00:00:00+00:00",
            used=2.50,
            limit=None,
            remaining=None,
            percent_used=None,
        )
        db.insert_window_snapshot(
            conn,
            provider="anthropic",
            window="weekly",
            unit="usd",
            pulled_at="2026-01-01T00:00:00+00:00",
            window_start_at="2025-12-29T00:00:00+00:00",
            reset_at="2026-01-05T00:00:00+00:00",
            used=14.84,
            limit=None,
            remaining=None,
            percent_used=None,
        )
        db.insert_window_snapshot(
            conn,
            provider="openai",
            window="today",
            unit="usd",
            pulled_at="2026-01-01T00:00:00+00:00",
            window_start_at="2026-01-01T00:00:00+00:00",
            reset_at="2026-01-02T00:00:00+00:00",
            used=4.20,
            limit=None,
            remaining=None,
            percent_used=None,
        )

    runner = CliRunner()
    result = runner.invoke(main, ["usage", "--json"])
    assert result.exit_code == 0

    data = json.loads(result.output)
    assert "tools" in data
    assert "anthropic" in data["tools"]
    assert "openai" in data["tools"]

    ant = data["tools"]["anthropic"]["windows"]
    assert ant["today"]["used"] == pytest.approx(2.50)
    assert ant["today"]["unit"] == "usd"
    assert ant["today"]["limit"] is None
    assert ant["weekly"]["used"] == pytest.approx(14.84)

    oai = data["tools"]["openai"]["windows"]
    assert oai["today"]["used"] == pytest.approx(4.20)


def test_usage_command_visual_display(tmp_paths):
    with db.session() as conn:
        db.insert_window_snapshot(
            conn,
            provider="anthropic",
            window="today",
            unit="usd",
            pulled_at="2026-01-01T00:00:00+00:00",
            window_start_at="2026-01-01T00:00:00+00:00",
            reset_at="2026-01-02T00:00:00+00:00",
            used=2.50,
            limit=None,
            remaining=None,
            percent_used=None,
        )
        db.insert_window_snapshot(
            conn,
            provider="anthropic",
            window="weekly",
            unit="usd",
            pulled_at="2026-01-01T00:00:00+00:00",
            window_start_at="2025-12-29T00:00:00+00:00",
            reset_at="2026-01-05T00:00:00+00:00",
            used=14.84,
            limit=10.0,
            remaining=None,
            percent_used=18.0,
        )

    runner = CliRunner()
    result = runner.invoke(main, ["usage"])
    assert result.exit_code == 0
    assert "anthropic" in result.output
    assert "today" in result.output
    assert "$2.50" in result.output       # no limit → show amount
    assert "18% used" in result.output    # limit known → show percent
    assert "weekly" in result.output


def test_latest_window_snapshots_returns_most_recent(tmp_paths):
    with db.session() as conn:
        # Insert older snapshot
        db.insert_window_snapshot(
            conn,
            provider="anthropic",
            window="today",
            unit="usd",
            pulled_at="2026-01-01T00:00:00+00:00",
            window_start_at="2026-01-01T00:00:00+00:00",
            reset_at="2026-01-02T00:00:00+00:00",
            used=1.00,
            limit=None,
            remaining=None,
            percent_used=None,
        )
        # Insert newer snapshot for the same window
        db.insert_window_snapshot(
            conn,
            provider="anthropic",
            window="today",
            unit="usd",
            pulled_at="2026-01-01T12:00:00+00:00",
            window_start_at="2026-01-01T00:00:00+00:00",
            reset_at="2026-01-02T00:00:00+00:00",
            used=5.00,
            limit=None,
            remaining=None,
            percent_used=None,
        )

    with db.session() as conn:
        rows = db.latest_window_snapshots(conn)

    assert len(rows) == 1
    assert rows[0]["used"] == pytest.approx(5.00)


def test_upsert_replaces_estimate_with_finalized(tmp_paths):
    today = date.today()
    iso = today.isoformat()
    with db.session() as conn:
        ins, upd = db.upsert_usage_event(
            conn,
            provider="anthropic",
            bucket_start=iso,
            bucket_end=iso,
            granularity="1d",
            workspace_id="wrkspc_x",
            model="claude-opus-4-7",
            cost_usd=10.0,
            cost_estimated=True,
        )
        assert (ins, upd) == (1, 0)
    # Re-pulling estimate should update, not duplicate
    with db.session() as conn:
        ins, upd = db.upsert_usage_event(
            conn,
            provider="anthropic",
            bucket_start=iso,
            bucket_end=iso,
            granularity="1d",
            workspace_id="wrkspc_x",
            model="claude-opus-4-7",
            cost_usd=11.0,
            cost_estimated=True,
        )
        assert (ins, upd) == (0, 1)
    # Finalized cost is a separate row (cost_estimated differs) — both can coexist briefly
    with db.session() as conn:
        ins, upd = db.upsert_usage_event(
            conn,
            provider="anthropic",
            bucket_start=iso,
            bucket_end=iso,
            granularity="1d",
            workspace_id="wrkspc_x",
            model="claude-opus-4-7",
            cost_usd=10.50,
            cost_estimated=False,
        )
        assert (ins, upd) == (1, 0)
