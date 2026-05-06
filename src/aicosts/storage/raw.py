import json
from datetime import date
from pathlib import Path
from typing import Any

from aicosts.paths import raw_dir


def append(provider: str, payload: Any, *, day: date | None = None) -> Path:
    """Append a raw API response payload to today's JSONL file. Returns the file path."""
    d = day or date.today()
    path = raw_dir(provider) / f"{d.isoformat()}.jsonl"
    with path.open("a") as f:
        f.write(json.dumps(payload, default=str))
        f.write("\n")
    return path
