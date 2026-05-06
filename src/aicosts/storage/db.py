import sqlite3
from contextlib import contextmanager
from pathlib import Path

from aicosts.paths import db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    bucket_start TEXT NOT NULL,
    bucket_end TEXT NOT NULL,
    granularity TEXT NOT NULL,
    -- Dimension columns are NOT NULL with default '' so the UNIQUE constraint
    -- treats absence as a single canonical value (SQLite considers NULL distinct in UNIQUE).
    project_id TEXT NOT NULL DEFAULT '',
    api_key_id TEXT NOT NULL DEFAULT '',
    workspace_id TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    cost_usd REAL NOT NULL,
    cost_estimated INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cached_input_tokens INTEGER,
    cache_creation_tokens INTEGER,
    raw_ref TEXT,
    UNIQUE(provider, bucket_start, granularity, project_id, api_key_id, workspace_id, model, cost_estimated)
);

CREATE INDEX IF NOT EXISTS idx_usage_provider_start ON usage_events(provider, bucket_start);
CREATE INDEX IF NOT EXISTS idx_usage_start ON usage_events(bucket_start);

CREATE TABLE IF NOT EXISTS budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    scope_value TEXT,
    period TEXT NOT NULL,
    limit_usd REAL NOT NULL,
    UNIQUE(scope, scope_value, period)
);

CREATE TABLE IF NOT EXISTS pull_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    pulled_at TEXT NOT NULL,
    since TEXT,
    until TEXT,
    rows_inserted INTEGER NOT NULL,
    rows_updated INTEGER NOT NULL,
    note TEXT
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or db_path()
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def session(path: Path | None = None):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_usage_event(
    conn: sqlite3.Connection,
    *,
    provider: str,
    bucket_start: str,
    bucket_end: str,
    granularity: str,
    cost_usd: float,
    cost_estimated: bool = False,
    project_id: str | None = None,
    api_key_id: str | None = None,
    workspace_id: str | None = None,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cached_input_tokens: int | None = None,
    cache_creation_tokens: int | None = None,
    raw_ref: str | None = None,
) -> tuple[int, int]:
    """Insert-or-replace a usage event. Returns (inserted, updated)."""
    project_id = project_id or ""
    api_key_id = api_key_id or ""
    workspace_id = workspace_id or ""
    model = model or ""
    bucket_start = bucket_start[:10]
    bucket_end = bucket_end[:10]

    existed = conn.execute(
        """
        SELECT 1 FROM usage_events
        WHERE provider = ? AND bucket_start = ? AND granularity = ?
          AND project_id = ? AND api_key_id = ? AND workspace_id = ?
          AND model = ? AND cost_estimated = ?
        """,
        (
            provider, bucket_start, granularity,
            project_id, api_key_id, workspace_id, model,
            1 if cost_estimated else 0,
        ),
    ).fetchone() is not None

    conn.execute(
        """
        INSERT INTO usage_events (
            provider, bucket_start, bucket_end, granularity,
            project_id, api_key_id, workspace_id, model,
            cost_usd, cost_estimated,
            input_tokens, output_tokens, cached_input_tokens, cache_creation_tokens,
            raw_ref
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider, bucket_start, granularity, project_id, api_key_id, workspace_id, model, cost_estimated)
        DO UPDATE SET
            cost_usd = excluded.cost_usd,
            input_tokens = excluded.input_tokens,
            output_tokens = excluded.output_tokens,
            cached_input_tokens = excluded.cached_input_tokens,
            cache_creation_tokens = excluded.cache_creation_tokens,
            raw_ref = excluded.raw_ref
        """,
        (
            provider, bucket_start, bucket_end, granularity,
            project_id, api_key_id, workspace_id, model,
            cost_usd, 1 if cost_estimated else 0,
            input_tokens, output_tokens, cached_input_tokens, cache_creation_tokens,
            raw_ref,
        ),
    )
    return (0, 1) if existed else (1, 0)


def log_pull(
    conn: sqlite3.Connection,
    *,
    provider: str,
    pulled_at: str,
    since: str | None,
    until: str | None,
    rows_inserted: int,
    rows_updated: int,
    note: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO pull_log (provider, pulled_at, since, until, rows_inserted, rows_updated, note)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (provider, pulled_at, since, until, rows_inserted, rows_updated, note),
    )
