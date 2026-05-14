"""Knowledge Store connection helper.

WAL mode is used so multiple readers (apps, dashboard) can run concurrently
with a single writer (the ingest scheduler). This mirrors the AirOS pattern.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional, Union

from financeos.os.storage.schema import ALL_DDL

DEFAULT_DB_PATH = Path("data/budgets/knowledge.sqlite")


def connect(path: Optional[Union[Path, str]] = None) -> sqlite3.Connection:
    """Open a connection to the Knowledge Store. Creates parent dirs if needed."""
    p = Path(path) if path else DEFAULT_DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Idempotently create all tables and indices."""
    for ddl in ALL_DDL:
        conn.execute(ddl)
