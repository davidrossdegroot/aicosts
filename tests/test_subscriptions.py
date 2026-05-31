"""Tests for the manually-tracked subscriptions feature (issue #6)."""
from __future__ import annotations

from datetime import date

import pytest
from click.testing import CliRunner

from aicosts import paths, reports
from aicosts.cli import main
from aicosts.providers import subscriptions
from aicosts.storage import db


@pytest.fixture
def tmp_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(paths, "db_path", lambda: tmp_path / "db.sqlite")
    monkeypatch.setattr(paths, "projects_toml", lambda: tmp_path / "projects.toml")
    yield tmp_path


# ---------------------------------------------------------------------------
# billing_dates
# ---------------------------------------------------------------------------

def test_billing_dates_monthly_anchors_on_day_of_month():
    anchor = date(2026, 1, 15)
    dates = subscriptions.billing_dates(anchor, "monthly", date(2026, 3, 1), date(2026, 5, 31), None)
    assert dates == [date(2026, 3, 15), date(2026, 4, 15), date(2026, 5, 15)]


def test_billing_dates_monthly_clamps_short_months():
    anchor = date(2026, 1, 31)
    dates = subscriptions.billing_dates(anchor, "monthly", date(2026, 2, 1), date(2026, 3, 31), None)
    assert dates == [date(2026, 2, 28), date(2026, 3, 31)]


def test_billing_dates_respects_start_and_end():
    anchor = date(2026, 4, 10)
    dates = subscriptions.billing_dates(anchor, "monthly", date(2026, 1, 1), date(2026, 12, 31), date(2026, 6, 30))
    assert dates == [date(2026, 4, 10), date(2026, 5, 10), date(2026, 6, 10)]


def test_billing_dates_weekly_and_yearly():
    weekly = subscriptions.billing_dates(date(2026, 5, 1), "weekly", date(2026, 5, 1), date(2026, 5, 31), None)
    assert weekly == [date(2026, 5, 1), date(2026, 5, 8), date(2026, 5, 15), date(2026, 5, 22), date(2026, 5, 29)]
    yearly = subscriptions.billing_dates(date(2024, 2, 29), "yearly", date(2024, 1, 1), date(2027, 1, 1), None)
    # 2025/2026 are non-leap → clamp to Feb 28
    assert yearly == [date(2024, 2, 29), date(2025, 2, 28), date(2026, 2, 28)]


# ---------------------------------------------------------------------------
# CLI + materialization
# ---------------------------------------------------------------------------

def test_subscriptions_add_list_remove(tmp_paths):
    runner = CliRunner()

    result = runner.invoke(main, ["subscriptions", "add", "elevenlabs", "--cost", "6", "--frequency", "monthly"])
    assert result.exit_code == 0
    assert "elevenlabs" in result.output

    result = runner.invoke(main, ["subscriptions", "list"])
    assert result.exit_code == 0
    assert "elevenlabs" in result.output
    assert "$6.00" in result.output

    result = runner.invoke(main, ["subscriptions", "remove", "elevenlabs"])
    assert result.exit_code == 0

    result = runner.invoke(main, ["subscriptions", "remove", "elevenlabs"])
    assert result.exit_code != 0


def test_subscriptions_add_updates_existing(tmp_paths):
    runner = CliRunner()
    runner.invoke(main, ["subscriptions", "add", "el", "--cost", "6"])
    result = runner.invoke(main, ["subscriptions", "add", "el", "--cost", "11"])
    assert "updated" in result.output
    with db.session() as conn:
        rows = db.list_subscriptions(conn)
    assert len(rows) == 1
    assert rows[0]["cost_usd"] == pytest.approx(11.0)


def test_pull_materializes_into_reports(tmp_paths):
    start = date.today().replace(day=1)
    with db.session() as conn:
        db.upsert_subscription(
            conn, name="elevenlabs", cost_usd=6.0, frequency="monthly",
            start_date=start.isoformat(), end_date=None, note=None,
        )

    subscriptions.pull(start, date.today())

    rows = reports.summarize("month", "provider")
    by_label = {r.label: r for r in rows}
    assert by_label["subscriptions"].cost_usd == pytest.approx(6.0)

    # By project: shows under the subscription name, not "(unmapped)".
    proj = {r.label: r for r in reports.summarize("month", "project")}
    assert "elevenlabs" in proj
    assert proj["elevenlabs"].cost_usd == pytest.approx(6.0)


def test_pull_is_idempotent(tmp_paths):
    start = date.today().replace(day=1)
    with db.session() as conn:
        db.upsert_subscription(
            conn, name="el", cost_usd=6.0, frequency="monthly",
            start_date=start.isoformat(),
        )
    subscriptions.pull(start, date.today())
    subscriptions.pull(start, date.today())
    rows = reports.summarize("month", "provider")
    by_label = {r.label: r for r in rows}
    assert by_label["subscriptions"].cost_usd == pytest.approx(6.0)
