from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from aicosts.config import load_projects, project_label_for
from aicosts.storage import db


@dataclass
class Row:
    label: str
    cost_usd: float
    estimated: bool
    detail: str = ""


def _window(period: str) -> tuple[date, date]:
    today = date.today()
    if period == "today":
        return (today, today)
    if period == "yesterday":
        return (today - timedelta(days=1), today - timedelta(days=1))
    if period == "week":
        return (today - timedelta(days=today.weekday()), today)
    if period == "month":
        return (today.replace(day=1), today)
    if period == "30d":
        return (today - timedelta(days=29), today)
    raise ValueError(f"unknown period: {period}")


def summarize(period: str, by: str) -> list[Row]:
    """Aggregate usage_events for the requested period grouped by `by`.

    by ∈ {provider, project, key, model}
    """
    start, end = _window(period)
    projects_doc = load_projects()

    sql = """
        SELECT provider, project_id, api_key_id, workspace_id, model,
               SUM(cost_usd) AS cost_usd,
               MAX(cost_estimated) AS any_estimated
        FROM usage_events
        WHERE bucket_start >= ? AND bucket_start <= ?
        GROUP BY provider, project_id, api_key_id, workspace_id, model
    """
    with db.session() as conn:
        rows = conn.execute(sql, (start.isoformat(), end.isoformat())).fetchall()

    aggregates: dict[str, Row] = {}
    for r in rows:
        provider = r["provider"]
        cost = r["cost_usd"] or 0.0
        estimated = bool(r["any_estimated"])
        if by == "provider":
            key = provider
        elif by == "model":
            key = f"{provider}/{r['model'] or '(none)'}"
        elif by == "key":
            key = f"{provider}/{r['api_key_id'] or r['workspace_id'] or '(none)'}"
        elif by in ("project", "project-model"):
            label = project_label_for(
                projects_doc,
                provider=provider,
                workspace_id=r["workspace_id"],
                project_id=r["project_id"],
                api_key_id=r["api_key_id"],
            )
            project_key = label or f"{provider}/(unmapped)"
            key = f"{project_key}/{r['model']}" if by == "project-model" else project_key
        else:
            raise ValueError(f"unknown grouping: {by}")

        existing = aggregates.get(key)
        if existing:
            existing.cost_usd += cost
            existing.estimated = existing.estimated or estimated
        else:
            aggregates[key] = Row(label=key, cost_usd=cost, estimated=estimated)

    return sorted(aggregates.values(), key=lambda r: r.cost_usd, reverse=True)


def status_line() -> str:
    """One-line summary suitable for daily briefings."""
    rows = summarize("today", "provider")
    total = sum(r.cost_usd for r in rows)
    if not rows:
        return "today: no usage data pulled"
    parts = [f"{r.label} ${r.cost_usd:.2f}" + ("*" if r.estimated else "") for r in rows]
    return f"today: ${total:.2f} ({', '.join(parts)})"
