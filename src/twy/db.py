"""SQLite connection, DB path resolution and schema migrations (PRAGMA user_version)."""

import os
import sqlite3
from pathlib import Path

from platformdirs import user_data_dir

MIGRATIONS: list[str] = [
    # v1: initial schema
    """
    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE vision (
        id         INTEGER PRIMARY KEY,
        horizon    TEXT NOT NULL UNIQUE CHECK (horizon IN ('long_term', 'three_year')),
        body       TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE cycle (
        id         INTEGER PRIMARY KEY,
        name       TEXT NOT NULL,
        start_date TEXT NOT NULL,
        week_start INTEGER NOT NULL CHECK (week_start BETWEEN 0 AND 6),
        status     TEXT NOT NULL DEFAULT 'planning'
                   CHECK (status IN ('planning', 'active', 'review', 'closed')),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE goal (
        id          INTEGER PRIMARY KEY,
        cycle_id    INTEGER NOT NULL REFERENCES cycle(id) ON DELETE CASCADE,
        position    INTEGER NOT NULL DEFAULT 0,
        title       TEXT NOT NULL,
        why         TEXT NOT NULL DEFAULT '',
        unit        TEXT,
        baseline    REAL,
        target      REAL,
        final_value REAL,
        outcome     TEXT CHECK (outcome IN ('hit', 'missed'))
    );
    CREATE TABLE tactic (
        id             INTEGER PRIMARY KEY,
        goal_id        INTEGER NOT NULL REFERENCES goal(id) ON DELETE CASCADE,
        position       INTEGER NOT NULL DEFAULT 0,
        title          TEXT NOT NULL,
        times_per_week INTEGER NOT NULL DEFAULT 1 CHECK (times_per_week >= 1),
        start_week     INTEGER NOT NULL DEFAULT 1,
        end_week       INTEGER NOT NULL DEFAULT 12
    );
    CREATE TABLE week_entry (
        cycle_id  INTEGER NOT NULL REFERENCES cycle(id) ON DELETE CASCADE,
        week_no   INTEGER NOT NULL,
        tactic_id INTEGER NOT NULL REFERENCES tactic(id) ON DELETE CASCADE,
        planned   INTEGER NOT NULL,
        done      INTEGER NOT NULL,
        PRIMARY KEY (cycle_id, week_no, tactic_id)
    );
    CREATE TABLE week_review (
        cycle_id    INTEGER NOT NULL REFERENCES cycle(id) ON DELETE CASCADE,
        week_no     INTEGER NOT NULL,
        obstacles   TEXT NOT NULL DEFAULT '',
        fix         TEXT NOT NULL DEFAULT '',
        reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (cycle_id, week_no)
    );
    CREATE TABLE goal_progress (
        goal_id INTEGER NOT NULL REFERENCES goal(id) ON DELETE CASCADE,
        week_no INTEGER NOT NULL,
        value   REAL NOT NULL,
        PRIMARY KEY (goal_id, week_no)
    );
    CREATE TABLE cycle_review (
        cycle_id    INTEGER PRIMARY KEY REFERENCES cycle(id) ON DELETE CASCADE,
        lessons     TEXT NOT NULL DEFAULT '',
        celebration TEXT NOT NULL DEFAULT '',
        reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
]


def default_db_path() -> Path:
    """`TWY_DB` if set, else the platform data dir (~/Library/Application Support/twy on macOS)."""
    override = os.environ.get("TWY_DB")
    if override:
        return Path(override).expanduser()
    return Path(user_data_dir("twy", appauthor=False)) / "twy.db"


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
        with conn:
            conn.executescript(script)
            conn.execute(f"PRAGMA user_version = {i}")


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open (creating if needed) and migrate the database. Pass ':memory:' for tests."""
    if path is None:
        path = default_db_path()
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        # WAL + a busy timeout let the web app and a terminal session write the same file.
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


def backup_to(conn: sqlite3.Connection, path: Path | str) -> None:
    """Write a consistent single-file copy of the database (safe while it is in use)."""
    dest = sqlite3.connect(str(path))
    try:
        conn.backup(dest)
        dest.execute("PRAGMA journal_mode = DELETE")
    finally:
        dest.close()
