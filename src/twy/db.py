"""SQLite connection, DB path resolution and schema migrations (PRAGMA user_version)."""

import os
import sqlite3
from pathlib import Path

from platformdirs import user_data_dir

# The reflection prompts a new database starts with (v3 inserts them; a factory reset puts them back).
DEFAULT_PROMPTS: list[tuple[str, str, str]] = [  # (scope, label, kind), in order within each scope
    ("daily", "What went well?", "text"),
    ("daily", "What got in the way?", "text"),
    ("daily", "Top tactics for tomorrow", "text"),
    ("weekly", "What got in the way?", "text"),
    ("weekly", "One fix for next week", "text"),
]

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
    # v2: time blocks (admin, chores, ...) and weekly slots for tactics and blocks
    """
    CREATE TABLE block (
        id       INTEGER PRIMARY KEY,
        cycle_id INTEGER NOT NULL REFERENCES cycle(id) ON DELETE CASCADE,
        position INTEGER NOT NULL DEFAULT 0,
        title    TEXT NOT NULL,
        kind     TEXT NOT NULL DEFAULT 'other'
                 CHECK (kind IN ('admin', 'chores', 'breakout', 'review', 'other'))
    );
    CREATE TABLE slot (
        id           INTEGER PRIMARY KEY,
        tactic_id    INTEGER REFERENCES tactic(id) ON DELETE CASCADE,
        block_id     INTEGER REFERENCES block(id) ON DELETE CASCADE,
        weekday      INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),  -- 0 = Monday
        start_min    INTEGER NOT NULL CHECK (start_min BETWEEN 0 AND 1439),
        duration_min INTEGER NOT NULL CHECK (duration_min BETWEEN 5 AND 720),
        CHECK ((tactic_id IS NULL) != (block_id IS NULL))
    );
    CREATE INDEX slot_tactic ON slot (tactic_id);
    CREATE INDEX slot_block ON slot (block_id);
    """,
    # v3: daily tactic logs, daily reviews and configurable reflection prompts.
    # week_review.obstacles/fix are copied into answers and no longer used.
    """
    CREATE TABLE tactic_log (
        tactic_id INTEGER NOT NULL REFERENCES tactic(id) ON DELETE CASCADE,
        day       TEXT NOT NULL,
        count     INTEGER NOT NULL CHECK (count >= 0),
        PRIMARY KEY (tactic_id, day)
    );
    CREATE TABLE prompt (
        id       INTEGER PRIMARY KEY,
        scope    TEXT NOT NULL CHECK (scope IN ('daily', 'weekly')),
        position INTEGER NOT NULL DEFAULT 0,
        label    TEXT NOT NULL,
        kind     TEXT NOT NULL DEFAULT 'text' CHECK (kind IN ('text', 'rating', 'yesno')),
        active   INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE day_review (
        cycle_id    INTEGER NOT NULL REFERENCES cycle(id) ON DELETE CASCADE,
        day         TEXT NOT NULL,
        reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (cycle_id, day)
    );
    -- label and kind are a snapshot, so editing or deleting a prompt never rewrites past reviews.
    CREATE TABLE answer (
        id        INTEGER PRIMARY KEY,
        cycle_id  INTEGER NOT NULL REFERENCES cycle(id) ON DELETE CASCADE,
        scope     TEXT NOT NULL CHECK (scope IN ('daily', 'weekly')),
        period    TEXT NOT NULL,  -- ISO date (daily) or week number (weekly)
        prompt_id INTEGER REFERENCES prompt(id) ON DELETE SET NULL,
        position  INTEGER NOT NULL DEFAULT 0,
        label     TEXT NOT NULL,
        kind      TEXT NOT NULL,
        value     TEXT NOT NULL
    );
    CREATE INDEX answer_period ON answer (cycle_id, scope, period);

    INSERT INTO prompt (scope, position, label, kind) VALUES
        ('daily', 0, 'What went well?', 'text'),
        ('daily', 1, 'What got in the way?', 'text'),
        ('daily', 2, 'Top tactics for tomorrow', 'text'),
        ('weekly', 0, 'What got in the way?', 'text'),
        ('weekly', 1, 'One fix for next week', 'text');
    INSERT INTO answer (cycle_id, scope, period, prompt_id, position, label, kind, value)
        SELECT cycle_id, 'weekly', CAST(week_no AS TEXT),
               (SELECT id FROM prompt WHERE scope = 'weekly' AND position = 0), 0, 'What got in the way?', 'text', obstacles
        FROM week_review WHERE obstacles != '';
    INSERT INTO answer (cycle_id, scope, period, prompt_id, position, label, kind, value)
        SELECT cycle_id, 'weekly', CAST(week_no AS TEXT),
               (SELECT id FROM prompt WHERE scope = 'weekly' AND position = 1), 1, 'One fix for next week', 'text', fix
        FROM week_review WHERE fix != '';
    """,
    # v4: scorecard entries keep their tactic's title and survive the tactic being deleted,
    # so editing the plan never rewrites the score of a week already reviewed.
    """
    CREATE TABLE week_entry_v4 (
        id        INTEGER PRIMARY KEY,
        cycle_id  INTEGER NOT NULL REFERENCES cycle(id) ON DELETE CASCADE,
        week_no   INTEGER NOT NULL,
        tactic_id INTEGER REFERENCES tactic(id) ON DELETE SET NULL,
        title     TEXT NOT NULL DEFAULT '',
        planned   INTEGER NOT NULL,
        done      INTEGER NOT NULL
    );
    INSERT INTO week_entry_v4 (cycle_id, week_no, tactic_id, title, planned, done)
        SELECT e.cycle_id, e.week_no, e.tactic_id, COALESCE(t.title, ''), e.planned, e.done
        FROM week_entry e LEFT JOIN tactic t ON t.id = e.tactic_id;
    DROP TABLE week_entry;
    ALTER TABLE week_entry_v4 RENAME TO week_entry;
    CREATE UNIQUE INDEX week_entry_tactic ON week_entry (cycle_id, week_no, tactic_id);
    """,
    # v5: tactics are planned week by week. Weekly tactic slots go (slot becomes block-only), and each
    # week's plan, and later what actually happened, is stored as dated items of that week.
    """
    CREATE TABLE slot_v5 (
        id           INTEGER PRIMARY KEY,
        block_id     INTEGER NOT NULL REFERENCES block(id) ON DELETE CASCADE,
        weekday      INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),  -- 0 = Monday
        start_min    INTEGER NOT NULL CHECK (start_min BETWEEN 0 AND 1439),
        duration_min INTEGER NOT NULL CHECK (duration_min BETWEEN 5 AND 720)
    );
    INSERT INTO slot_v5 (id, block_id, weekday, start_min, duration_min)
        SELECT id, block_id, weekday, start_min, duration_min FROM slot WHERE block_id IS NOT NULL;
    DROP TABLE slot;
    ALTER TABLE slot_v5 RENAME TO slot;
    CREATE INDEX slot_block ON slot (block_id);

    CREATE TABLE week_status (
        cycle_id    INTEGER NOT NULL REFERENCES cycle(id) ON DELETE CASCADE,
        week_no     INTEGER NOT NULL CHECK (week_no BETWEEN 1 AND 13),
        planned_at  TEXT,  -- set by saving the week's plan, even with nothing placed
        recorded_at TEXT,  -- set by recording what happened; NULL = assumed as planned
        PRIMARY KEY (cycle_id, week_no)
    );
    -- title is a snapshot, so a past week still reads well after its tactic or block is deleted.
    CREATE TABLE week_item (
        id             INTEGER PRIMARY KEY,
        cycle_id       INTEGER NOT NULL REFERENCES cycle(id) ON DELETE CASCADE,
        week_no        INTEGER NOT NULL CHECK (week_no BETWEEN 1 AND 13),
        layer          TEXT NOT NULL CHECK (layer IN ('plan', 'actual')),
        kind           TEXT NOT NULL CHECK (kind IN ('tactic', 'block', 'event')),
        tactic_id      INTEGER REFERENCES tactic(id) ON DELETE SET NULL,
        block_id       INTEGER REFERENCES block(id) ON DELETE SET NULL,
        plan_item_id   INTEGER REFERENCES week_item(id) ON DELETE SET NULL,  -- actual row: the plan row it records
        title          TEXT NOT NULL DEFAULT '',
        weekday        INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
        start_min      INTEGER NOT NULL CHECK (start_min BETWEEN 0 AND 1439),
        duration_min   INTEGER NOT NULL CHECK (duration_min BETWEEN 5 AND 720),
        skipped        INTEGER NOT NULL DEFAULT 0,  -- a block occurrence left out this week
        custom         INTEGER NOT NULL DEFAULT 0,  -- a block occurrence skipped or moved this week
        orig_weekday   INTEGER,  -- block rows: the weekly slot they came from
        orig_start_min INTEGER
    );
    CREATE INDEX week_item_week ON week_item (cycle_id, week_no, layer);
    CREATE INDEX week_item_tactic ON week_item (tactic_id);
    CREATE INDEX week_item_block ON week_item (block_id);
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
        # executescript() commits first and then runs in autocommit mode, so `with conn:` can't
        # make it atomic; the script carries its own transaction, including the version bump.
        try:
            conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {i};\nCOMMIT;")
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise


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
        # WAL + a busy timeout let gunicorn's threads write the same file safely.
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
