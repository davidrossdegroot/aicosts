from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class PullResult:
    provider: str
    rows_inserted: int = 0
    rows_updated: int = 0
    note: str | None = None
    since: date | None = None
    until: date | None = None


@dataclass
class WindowSnapshot:
    provider: str
    window: str        # 'today', 'weekly'
    unit: str          # 'tokens', 'usd'
    pulled_at: str     # ISO 8601 UTC
    window_start_at: str
    reset_at: str | None
    used: float
    limit: float | None
    remaining: float | None
    percent_used: float | None
    error: str | None = None
