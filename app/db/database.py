import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.core.config import settings


BASE_DIR = Path(__file__).resolve().parents[2]
DATABASE_PATH = BASE_DIR / settings.database_path
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def get_connection() -> sqlite3.Connection:
    """Create and configure a SQLite database connection."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    return connection


def initialize_database() -> None:
    """Create the database schema only when the database is new."""
    with get_connection() as connection:
        table_exists = connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'businesses'
            """
        ).fetchone()

        if table_exists is None:
            schema = SCHEMA_PATH.read_text(encoding="utf-8")
            connection.executescript(schema)


def close_connection(connection: sqlite3.Connection) -> None:
    """Close a SQLite database connection."""
    connection.close()


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Provide one short write transaction for an application service.

    Database-specific locking stays here, so callers only depend on the
    transaction boundary rather than on SQLite commands.
    """
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()
