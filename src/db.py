"""Database helpers: connection handling and schema creation."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path: str) -> sqlite3.Connection:
    """Open a connection with foreign keys on and row access by name."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    """Build the schema from schema.sql. Drops existing tables first."""
    sql = SCHEMA_PATH.read_text()
    conn.executescript(sql)
    conn.commit()
    log.info("schema created from %s", SCHEMA_PATH.name)


def configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
