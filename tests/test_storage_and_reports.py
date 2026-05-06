"""Exercise storage + reports without hitting external APIs.

We bypass the user-data-dir paths by pointing the DB and projects.toml at a tmp
directory through monkeypatching aicosts.paths.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from aicosts import paths, reports
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
    )
    rows = reports.summarize("today", "project")
    labels = [r.label for r in rows]
    assert "openclaw-agent" in labels
    assert "voice-calls" in labels


def test_status_line(tmp_paths):
    today = date.today()
    _seed(today)
    line = reports.status_line()
    assert "$6.70" in line  # 2.50 + 4.20
    assert "anthropic" in line
    assert "openai" in line


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
