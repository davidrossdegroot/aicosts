"""Manually-tracked recurring subscriptions (issue #6).

Subscriptions are flat recurring charges the provider APIs don't report — e.g. an
ElevenLabs, GitHub, Codex, or Claude monthly plan. They're entered by hand via
`aicosts subscriptions add` and stored in the `subscriptions` table.

`pull` materializes each subscription's billing occurrences that fall within the
requested window into `usage_events` (provider="subscriptions", model=<name>), so
they show up in reports alongside metered usage. The full charge lands on each
billing date — a $6/mo plan is one $6 event per month, not prorated.
"""
from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, timedelta

from aicosts.providers.base import PullResult
from aicosts.storage import db

PROVIDER = "subscriptions"
FREQUENCIES = ("daily", "weekly", "monthly", "yearly")


def _add_months(d: date, n: int) -> date:
    month = d.month - 1 + n
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _add_years(d: date, n: int) -> date:
    try:
        return d.replace(year=d.year + n)
    except ValueError:  # Feb 29 in a non-leap year
        return d.replace(year=d.year + n, day=28)


def _next_occurrence(d: date, frequency: str) -> date:
    if frequency == "daily":
        return d + timedelta(days=1)
    if frequency == "weekly":
        return d + timedelta(weeks=1)
    if frequency == "monthly":
        return _add_months(d, 1)
    if frequency == "yearly":
        return _add_years(d, 1)
    raise ValueError(f"unknown frequency: {frequency}")


def _occurrence(anchor: date, frequency: str, k: int) -> date:
    """The k-th billing date after `anchor` (k=0 is the anchor itself).

    Always computed from `anchor` rather than chained, so monthly/yearly charges
    stay pinned to the anchor's day even after a short month clamps them.
    """
    if frequency == "daily":
        return anchor + timedelta(days=k)
    if frequency == "weekly":
        return anchor + timedelta(weeks=k)
    if frequency == "monthly":
        return _add_months(anchor, k)
    if frequency == "yearly":
        return _add_years(anchor, k)
    raise ValueError(f"unknown frequency: {frequency}")


def billing_dates(
    anchor: date, frequency: str, lo: date, hi: date, sub_end: date | None
) -> list[date]:
    """Occurrences on/after `anchor`, within [lo, hi], not past `sub_end`."""
    upper = min(hi, sub_end) if sub_end else hi
    if upper < anchor or upper < lo:
        return []

    # Jump to the period containing `lo` (always at/before lo) so we don't walk
    # one period at a time from a distant anchor. The prior occurrence sits in the
    # prior period, strictly before lo, so this never skips a valid date.
    if frequency == "daily":
        k = max(0, (lo - anchor).days)
    elif frequency == "weekly":
        k = max(0, (lo - anchor).days // 7)
    elif frequency == "monthly":
        k = max(0, (lo.year - anchor.year) * 12 + (lo.month - anchor.month))
    elif frequency == "yearly":
        k = max(0, lo.year - anchor.year)
    else:
        raise ValueError(f"unknown frequency: {frequency}")

    dates: list[date] = []
    occ = _occurrence(anchor, frequency, k)
    while occ <= upper:
        if occ >= lo:
            dates.append(occ)
        k += 1
        occ = _occurrence(anchor, frequency, k)
    return dates


def pull(since: date, until: date | None = None) -> PullResult:
    """Materialize subscription billing occurrences in [since, until]."""
    until = until or date.today()
    inserted = updated = 0
    pulled_at = datetime.now(UTC).isoformat()

    with db.session() as conn:
        subs = db.list_subscriptions(conn)
        for sub in subs:
            anchor = date.fromisoformat(sub["start_date"])
            sub_end = date.fromisoformat(sub["end_date"]) if sub["end_date"] else None
            frequency = sub["frequency"]
            for occ in billing_dates(anchor, frequency, since, until, sub_end):
                bucket_end = _next_occurrence(occ, frequency).isoformat()
                ins, upd = db.upsert_usage_event(
                    conn,
                    provider=PROVIDER,
                    bucket_start=occ.isoformat(),
                    bucket_end=bucket_end,
                    granularity=frequency,
                    model=sub["name"],
                    cost_usd=float(sub["cost_usd"]),
                    cost_estimated=False,
                    raw_ref=f"subscription:{sub['name']}:{occ.isoformat()}",
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
