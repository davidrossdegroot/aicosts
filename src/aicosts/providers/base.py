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
