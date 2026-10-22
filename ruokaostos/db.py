"""SQLite-yhteyden apufunktiot."""

import sqlite3
from pathlib import Path

from ruokaostos import config


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Avaa yhteyden viiteavaimet päällä ja rivit nimettyinä sarakkeina."""
    conn = sqlite3.connect(path or config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # SQLiten LOWER() ja LIKE ymmärtävät kirjainkoon vain ASCII-merkeille (ei ä/ö/å).
    conn.create_function("casefold", 1, lambda s: s.casefold() if s else s, deterministic=True)
    return conn


def init_schema(conn: sqlite3.Connection, schema_path: Path = config.SCHEMA_PATH) -> None:
    conn.executescript(schema_path.read_text(encoding="utf-8"))
