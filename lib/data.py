"""Shared helpers for the DataTech Vietnam SQLite database."""
from __future__ import annotations

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "datatech.db")


def get_db(path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def get_schema(path: str = DB_PATH) -> str:
    """Return a human-readable schema string for every table in the DB."""
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in c.fetchall()]
    parts: list[str] = []
    for tbl in tables:
        c.execute(f"PRAGMA table_info({tbl})")
        cols = c.fetchall()
        col_lines = []
        for col in cols:
            pk = " PRIMARY KEY" if col[5] else ""
            nn = " NOT NULL" if col[3] else ""
            col_lines.append(f"    {col[1]:20s} {col[2]}{pk}{nn}")
        c.execute(f"SELECT COUNT(*) FROM {tbl}")
        row_count = c.fetchone()[0]
        parts.append(f"TABLE {tbl} ({row_count} rows):\n" + "\n".join(col_lines))
    conn.close()
    return "\n\n".join(parts)


# Tables that public agents may never read from
RESTRICTED_TABLES = {"internal_config"}

# Columns that should be masked for non-admin roles
PII_COLUMNS = {"email", "phone"}
