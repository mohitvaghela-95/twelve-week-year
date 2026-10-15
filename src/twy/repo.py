"""All SQL lives here. Screens and domain code talk to the database only through Repo."""

import secrets
import sqlite3
from dataclasses import replace
from datetime import date, datetime

from twy.domain.conflicts import DEFAULT_DAY_WINDOW
from twy.domain.guardrails import DEFAULT_MAX_WEEKLY_ITEMS
from twy.models import (
    Answer,
    Block,
    Cycle,
    CycleReview,
    DayReview,
    Goal,
    Prompt,
    Slot,
    Tactic,
    WeekEntry,
    WeekItem,
    WeekReview,
)


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _cycle(row: sqlite3.Row) -> Cycle:
    return Cycle(
        id=row["id"],
        name=row["name"],
        start_date=date.fromisoformat(row["start_date"]),
        week_start=row["week_start"],
        status=row["status"],
        created_at=_dt(row["created_at"]),
    )


def _goal(row: sqlite3.Row) -> Goal:
    return Goal(
        id=row["id"],
        cycle_id=row["cycle_id"],
        title=row["title"],
        why=row["why"],
        unit=row["unit"],
        baseline=row["baseline"],
        target=row["target"],
        final_value=row["final_value"],
        outcome=row["outcome"],
        position=row["position"],
    )


def _tactic(row: sqlite3.Row) -> Tactic:
    return Tactic(
        id=row["id"],
        goal_id=row["goal_id"],
        title=row["title"],
        times_per_week=row["times_per_week"],
        start_week=row["start_week"],
        end_week=row["end_week"],
        position=row["position"],
    )


def _slot(row: sqlite3.Row) -> Slot:
    return Slot(weekday=row["weekday"], start_min=row["start_min"], duration_min=row["duration_min"], id=row["id"])


def _prompt(row: sqlite3.Row) -> Prompt:
    return Prompt(
        id=row["id"],
        scope=row["scope"],
        label=row["label"],
        kind=row["kind"],
        active=bool(row["active"]),
        position=row["position"],
    )


def _week_item(row: sqlite3.Row) -> WeekItem:
    return WeekItem(
        cycle_id=row["cycle_id"],
        week_no=row["week_no"],
        kind=row["kind"],
        weekday=row["weekday"],
        start_min=row["start_min"],
        duration_min=row["duration_min"],
        layer=row["layer"],
        tactic_id=row["tactic_id"],
        block_id=row["block_id"],
        title=row["title"],
        skipped=bool(row["skipped"]),
        custom=bool(row["custom"]),
        orig_weekday=row["orig_weekday"],
        orig_start_min=row["orig_start_min"],
        plan_item_id=row["plan_item_id"],
        id=row["id"],
    )


DEFAULT_ALERT_MINUTES = 10


class Repo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # -- settings ---------------------------------------------------------------

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    @property
    def week_start(self) -> int:
        return int(self.get_setting("week_start", "0"))

    @property
    def max_weekly_items(self) -> int:
        return int(self.get_setting("max_weekly_items", str(DEFAULT_MAX_WEEKLY_ITEMS)))

    @property
    def alert_minutes(self) -> int:
        return int(self.get_setting("alert_minutes", str(DEFAULT_ALERT_MINUTES)))

    @property
    def day_window(self) -> tuple[int, int]:
        """When clash suggestions may start and must end, in minutes after midnight."""
        start, end = DEFAULT_DAY_WINDOW
        return int(self.get_setting("day_start", str(start))), int(self.get_setting("day_end", str(end)))

    @property
    def import_urls(self) -> list[str]:
        """Other calendars' ICS links, shown while planning (see twy.calendars)."""
        return [u for u in (self.get_setting("import_urls") or "").splitlines() if u]

    @property
    def calendar_token(self) -> str:
        """Secret for the calendar feed URL, created on first use."""
        return self.get_setting("calendar_token") or self.rotate_calendar_token()

    def rotate_calendar_token(self) -> str:
        token = secrets.token_urlsafe(24)
        self.set_setting("calendar_token", token)
        return token

    @property
    def is_configured(self) -> bool:
        return self.get_setting("week_start") is not None

    # -- vision -----------------------------------------------------------------

    def get_vision(self, horizon: str) -> str:
        row = self.conn.execute("SELECT body FROM vision WHERE horizon = ?", (horizon,)).fetchone()
        return row["body"] if row else ""

    def set_vision(self, horizon: str, body: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO vision (horizon, body) VALUES (?, ?) "
                "ON CONFLICT(horizon) DO UPDATE SET body = excluded.body, "
                "updated_at = CURRENT_TIMESTAMP",
                (horizon, body),
            )

    # -- cycles -----------------------------------------------------------------

    def create_cycle(self, name: str, start_date: date, week_start: int) -> Cycle:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO cycle (name, start_date, week_start) VALUES (?, ?, ?)",
                (name, start_date.isoformat(), week_start),
            )
        return self.get_cycle(cur.lastrowid)

    def get_cycle(self, cycle_id: int) -> Cycle:
        row = self.conn.execute("SELECT * FROM cycle WHERE id = ?", (cycle_id,)).fetchone()
        if row is None:
            raise KeyError(cycle_id)
        return _cycle(row)

    def list_cycles(self) -> list[Cycle]:
        rows = self.conn.execute("SELECT * FROM cycle ORDER BY start_date DESC, id DESC")
        return [_cycle(r) for r in rows]

    def current_cycle(self) -> Cycle | None:
        """The most recent cycle that is not closed."""
        row = self.conn.execute(
            "SELECT * FROM cycle WHERE status != 'closed' ORDER BY start_date DESC, id DESC LIMIT 1"
        ).fetchone()
        return _cycle(row) if row else None

    def update_cycle(self, cycle: Cycle) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE cycle SET name = ?, start_date = ?, status = ? WHERE id = ?",
                (cycle.name, cycle.start_date.isoformat(), cycle.status, cycle.id),
            )

    def set_cycle_status(self, cycle_id: int, status: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE cycle SET status = ? WHERE id = ?", (status, cycle_id))

    def copy_plan(self, from_cycle_id: int, to_cycle_id: int) -> None:
        """Copy goals, tactics, blocks and their slots (not results or week plans) into another cycle."""
        with self.conn:
            for goal in self.list_goals(from_cycle_id):
                cur = self.conn.execute(
                    "INSERT INTO goal (cycle_id, position, title, why, unit, baseline, target) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (to_cycle_id, goal.position, goal.title, goal.why, goal.unit, goal.baseline, goal.target),
                )
                self.conn.executemany(
                    "INSERT INTO tactic (goal_id, position, title, times_per_week, start_week, end_week) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (cur.lastrowid, t.position, t.title, t.times_per_week, t.start_week, t.end_week)
                        for t in self.list_tactics_for_goal(goal.id)
                    ],
                )
            for b in self.list_blocks(from_cycle_id):
                bcur = self.conn.execute(
                    "INSERT INTO block (cycle_id, position, title, kind) VALUES (?, ?, ?, ?)",
                    (to_cycle_id, b.position, b.title, b.kind),
                )
                self._insert_slots(bcur.lastrowid, b.slots)

    # -- goals ------------------------------------------------------------------

    def list_goals(self, cycle_id: int) -> list[Goal]:
        rows = self.conn.execute("SELECT * FROM goal WHERE cycle_id = ? ORDER BY position, id", (cycle_id,))
        return [_goal(r) for r in rows]

    def get_goal(self, goal_id: int) -> Goal:
        row = self.conn.execute("SELECT * FROM goal WHERE id = ?", (goal_id,)).fetchone()
        if row is None:
            raise KeyError(goal_id)
        return _goal(row)

    def save_goal(self, goal: Goal) -> Goal:
        fields = (goal.title, goal.why, goal.unit, goal.baseline, goal.target, goal.final_value, goal.outcome)
        with self.conn:
            if goal.id is None:
                pos = self.conn.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 FROM goal WHERE cycle_id = ?", (goal.cycle_id,)
                ).fetchone()[0]
                cur = self.conn.execute(
                    "INSERT INTO goal (title, why, unit, baseline, target, final_value, outcome, cycle_id, position) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (*fields, goal.cycle_id, pos),
                )
                goal.id, goal.position = cur.lastrowid, pos
            else:
                self.conn.execute(
                    "UPDATE goal SET title = ?, why = ?, unit = ?, baseline = ?, target = ?, "
                    "final_value = ?, outcome = ? WHERE id = ?",
                    (*fields, goal.id),
                )
        return goal

    def delete_goal(self, goal_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM goal WHERE id = ?", (goal_id,))

    def move_goal(self, goal_id: int, delta: int) -> None:
        goal = self.get_goal(goal_id)
        self._swap(self.list_goals(goal.cycle_id), goal_id, delta, "goal")

    # -- tactics ----------------------------------------------------------------

    def list_tactics(self, cycle_id: int) -> list[Tactic]:
        rows = self.conn.execute(
            "SELECT t.* FROM tactic t JOIN goal g ON g.id = t.goal_id "
            "WHERE g.cycle_id = ? ORDER BY g.position, g.id, t.position, t.id",
            (cycle_id,),
        )
        return [_tactic(r) for r in rows]

    def list_tactics_for_goal(self, goal_id: int) -> list[Tactic]:
        rows = self.conn.execute("SELECT * FROM tactic WHERE goal_id = ? ORDER BY position, id", (goal_id,))
        return [_tactic(r) for r in rows]

    def get_tactic(self, tactic_id: int) -> Tactic:
        row = self.conn.execute("SELECT * FROM tactic WHERE id = ?", (tactic_id,)).fetchone()
        if row is None:
            raise KeyError(tactic_id)
        return _tactic(row)

    def save_tactic(self, tactic: Tactic) -> Tactic:
        fields = (tactic.title, tactic.times_per_week, tactic.start_week, tactic.end_week)
        with self.conn:
            if tactic.id is None:
                pos = self.conn.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 FROM tactic WHERE goal_id = ?", (tactic.goal_id,)
                ).fetchone()[0]
                cur = self.conn.execute(
                    "INSERT INTO tactic (title, times_per_week, start_week, end_week, goal_id, position) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (*fields, tactic.goal_id, pos),
                )
                tactic.id, tactic.position = cur.lastrowid, pos
            else:
                self.conn.execute(
                    "UPDATE tactic SET title = ?, times_per_week = ?, start_week = ?, end_week = ? WHERE id = ?",
                    (*fields, tactic.id),
                )
        return tactic

    def delete_tactic(self, tactic_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM tactic WHERE id = ?", (tactic_id,))

    def move_tactic(self, tactic_id: int, delta: int) -> None:
        tactic = self.get_tactic(tactic_id)
        self._swap(self.list_tactics_for_goal(tactic.goal_id), tactic_id, delta, "tactic")

    # -- slots ------------------------------------------------------------------

    def list_slots(self, block_id: int) -> list[Slot]:
        """A block's weekly slots. (Tactics have no weekly times: they're planned week by week.)"""
        rows = self.conn.execute("SELECT * FROM slot WHERE block_id = ? ORDER BY weekday, start_min, id", (block_id,))
        return [_slot(r) for r in rows]

    def _insert_slots(self, block_id: int, slots: list[Slot]) -> None:
        """Insert inside the caller's transaction."""
        self.conn.executemany(
            "INSERT INTO slot (block_id, weekday, start_min, duration_min) VALUES (?, ?, ?, ?)",
            [(block_id, s.weekday, s.start_min, s.duration_min) for s in slots],
        )

    # -- blocks -----------------------------------------------------------------

    def list_blocks(self, cycle_id: int) -> list[Block]:
        rows = self.conn.execute("SELECT * FROM block WHERE cycle_id = ? ORDER BY position, id", (cycle_id,))
        return [self._block(r) for r in rows]

    def get_block(self, block_id: int) -> Block:
        row = self.conn.execute("SELECT * FROM block WHERE id = ?", (block_id,)).fetchone()
        if row is None:
            raise KeyError(block_id)
        return self._block(row)

    def _block(self, row: sqlite3.Row) -> Block:
        return Block(
            id=row["id"],
            cycle_id=row["cycle_id"],
            title=row["title"],
            kind=row["kind"],
            position=row["position"],
            slots=self.list_slots(row["id"]),
        )

    def save_block(self, block: Block) -> Block:
        """Insert or update a block and replace its slots, in one transaction."""
        with self.conn:
            if block.id is None:
                pos = self.conn.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 FROM block WHERE cycle_id = ?", (block.cycle_id,)
                ).fetchone()[0]
                cur = self.conn.execute(
                    "INSERT INTO block (cycle_id, position, title, kind) VALUES (?, ?, ?, ?)",
                    (block.cycle_id, pos, block.title, block.kind),
                )
                block.id, block.position = cur.lastrowid, pos
            else:
                self.conn.execute(
                    "UPDATE block SET title = ?, kind = ? WHERE id = ?", (block.title, block.kind, block.id)
                )
                self.conn.execute("DELETE FROM slot WHERE block_id = ?", (block.id,))
            self._insert_slots(block.id, block.slots)
        return block

    def delete_block(self, block_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM block WHERE id = ?", (block_id,))

    def move_block(self, block_id: int, delta: int) -> None:
        block = self.get_block(block_id)
        self._swap(self.list_blocks(block.cycle_id), block_id, delta, "block")

    def _swap(self, items: list, item_id: int, delta: int, table: str) -> None:
        ids = [i.id for i in items]
        idx = ids.index(item_id)
        other = idx + delta
        if not 0 <= other < len(ids):
            return
        ids[idx], ids[other] = ids[other], ids[idx]
        with self.conn:
            for pos, i in enumerate(ids):
                self.conn.execute(f"UPDATE {table} SET position = ? WHERE id = ?", (pos, i))

    # -- week plans -------------------------------------------------------------

    def week_items(self, cycle_id: int, week_no: int, layer: str = "plan") -> list[WeekItem]:
        """One layer of a week, in time order. Titles follow the tactic or block while it exists."""
        rows = self.conn.execute(
            "SELECT w.*, COALESCE(t.title, b.title, w.title) AS live_title FROM week_item w "
            "LEFT JOIN tactic t ON t.id = w.tactic_id LEFT JOIN block b ON b.id = w.block_id "
            "WHERE w.cycle_id = ? AND w.week_no = ? AND w.layer = ? ORDER BY w.weekday, w.start_min, w.id",
            (cycle_id, week_no, layer),
        )
        return [replace(_week_item(r), title=r["live_title"]) for r in rows]

    def replace_week_items(self, cycle_id: int, week_no: int, layer: str, items: list[WeekItem]) -> None:
        """Replace one layer of a week and mark the week planned (plan) or recorded (actual)."""
        stamp = "planned_at" if layer == "plan" else "recorded_at"
        with self.conn:
            self.conn.execute(
                "DELETE FROM week_item WHERE cycle_id = ? AND week_no = ? AND layer = ?", (cycle_id, week_no, layer)
            )
            self._insert_week_items(cycle_id, week_no, layer, items)
            self.conn.execute(
                f"INSERT INTO week_status (cycle_id, week_no, {stamp}) VALUES (?, ?, CURRENT_TIMESTAMP) "
                f"ON CONFLICT(cycle_id, week_no) DO UPDATE SET {stamp} = CURRENT_TIMESTAMP",
                (cycle_id, week_no),
            )

    def _insert_week_items(self, cycle_id: int, week_no: int, layer: str, items: list[WeekItem]) -> None:
        """Insert inside the caller's transaction. A blank title is filled in from the tactic or block."""
        self.conn.executemany(
            "INSERT INTO week_item (cycle_id, week_no, layer, kind, tactic_id, block_id, plan_item_id, title, "
            "weekday, start_min, duration_min, skipped, custom, orig_weekday, orig_start_min) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE(NULLIF(?, ''), (SELECT title FROM tactic WHERE id = ?), "
            "(SELECT title FROM block WHERE id = ?), ''), ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    cycle_id,
                    week_no,
                    layer,
                    i.kind,
                    i.tactic_id,
                    i.block_id,
                    i.plan_item_id,
                    i.title,
                    i.tactic_id,
                    i.block_id,
                    i.weekday,
                    i.start_min,
                    i.duration_min,
                    int(i.skipped),
                    int(i.custom),
                    i.orig_weekday,
                    i.orig_start_min,
                )
                for i in items
            ],
        )

    def planned_weeks(self, cycle_id: int) -> set[int]:
        rows = self.conn.execute(
            "SELECT week_no FROM week_status WHERE cycle_id = ? AND planned_at IS NOT NULL", (cycle_id,)
        )
        return {r["week_no"] for r in rows}

    def resync_block_rows(self, block: Block, weeks: list[int]) -> None:
        """After a block's weekly slots change, refresh its occurrences in the given (planned) weeks.

        Occurrences skipped or moved for one week are kept, and their slot isn't added back.
        """
        with self.conn:
            for week_no in weeks:
                self.conn.execute(
                    "DELETE FROM week_item WHERE cycle_id = ? AND week_no = ? AND layer = 'plan' "
                    "AND block_id = ? AND custom = 0",
                    (block.cycle_id, week_no, block.id),
                )
                kept = {
                    (r["orig_weekday"], r["orig_start_min"])
                    for r in self.conn.execute(
                        "SELECT orig_weekday, orig_start_min FROM week_item "
                        "WHERE cycle_id = ? AND week_no = ? AND layer = 'plan' AND block_id = ?",
                        (block.cycle_id, week_no, block.id),
                    )
                }
                fresh = [
                    WeekItem(
                        block.cycle_id,
                        week_no,
                        "block",
                        s.weekday,
                        s.start_min,
                        s.duration_min,
                        block_id=block.id,
                        title=block.title,
                        orig_weekday=s.weekday,
                        orig_start_min=s.start_min,
                    )
                    for s in block.slots
                    if (s.weekday, s.start_min) not in kept
                ]
                self._insert_week_items(block.cycle_id, week_no, "plan", fresh)

    def prune_plan_rows(self, cycle_id: int, kind: str, owner_id: int, weeks: list[int]) -> None:
        """Remove a tactic's or block's planned rows from the given weeks (e.g. ones it no longer runs in)."""
        column = {"tactic": "tactic_id", "block": "block_id"}[kind]
        with self.conn:
            self.conn.executemany(
                f"DELETE FROM week_item WHERE cycle_id = ? AND week_no = ? AND layer = 'plan' AND {column} = ?",
                [(cycle_id, w, owner_id) for w in weeks],
            )

    # -- weekly reviews ---------------------------------------------------------

    def get_week_review(self, cycle_id: int, week_no: int) -> WeekReview | None:
        row = self.conn.execute(
            "SELECT * FROM week_review WHERE cycle_id = ? AND week_no = ?", (cycle_id, week_no)
        ).fetchone()
        if row is None:
            return None
        entries = [
            WeekEntry(r["cycle_id"], r["week_no"], r["tactic_id"], r["planned"], r["done"], r["title"])
            for r in self.conn.execute(
                "SELECT * FROM week_entry WHERE cycle_id = ? AND week_no = ? ORDER BY id", (cycle_id, week_no)
            )
        ]
        return WeekReview(
            cycle_id=cycle_id,
            week_no=week_no,
            reviewed_at=_dt(row["reviewed_at"]),
            entries=entries,
            answers=self.get_answers(cycle_id, "weekly", str(week_no)),
        )

    def reviewed_weeks(self, cycle_id: int) -> set[int]:
        rows = self.conn.execute("SELECT week_no FROM week_review WHERE cycle_id = ?", (cycle_id,))
        return {r["week_no"] for r in rows}

    def save_week_review(
        self,
        review: WeekReview,
        progress: dict[int, float | None] | None = None,
    ) -> None:
        """Write the scorecard, reflection answers and lag readings in one transaction.

        Entries are a snapshot of what was planned, replacing any earlier snapshot for this week.
        A blank entry title is filled in from the tactic, so the line still reads well once it's deleted.
        """
        with self.conn:
            self.conn.execute(
                "DELETE FROM week_entry WHERE cycle_id = ? AND week_no = ?", (review.cycle_id, review.week_no)
            )
            self.conn.executemany(
                "INSERT INTO week_entry (cycle_id, week_no, tactic_id, title, planned, done) "
                "VALUES (?, ?, ?, COALESCE(NULLIF(?, ''), (SELECT title FROM tactic WHERE id = ?), ''), ?, ?)",
                [
                    (
                        review.cycle_id,
                        review.week_no,
                        e.tactic_id,
                        e.title,
                        e.tactic_id,
                        e.planned,
                        max(0, min(e.done, e.planned)),
                    )
                    for e in review.entries
                ],
            )
            self.conn.execute(
                "INSERT INTO week_review (cycle_id, week_no) VALUES (?, ?) "
                "ON CONFLICT(cycle_id, week_no) DO UPDATE SET reviewed_at = CURRENT_TIMESTAMP",
                (review.cycle_id, review.week_no),
            )
            self._replace_answers(review.cycle_id, "weekly", str(review.week_no), review.answers)
            for goal_id, value in (progress or {}).items():
                if value is None:
                    self.conn.execute(
                        "DELETE FROM goal_progress WHERE goal_id = ? AND week_no = ?", (goal_id, review.week_no)
                    )
                else:
                    self.conn.execute(
                        "INSERT INTO goal_progress (goal_id, week_no, value) VALUES (?, ?, ?) "
                        "ON CONFLICT(goal_id, week_no) DO UPDATE SET value = excluded.value",
                        (goal_id, review.week_no, value),
                    )

    def weekly_scores(self, cycle_id: int) -> dict[int, float | None]:
        """Execution score for each reviewed week, in one query (same rule as scoring.execution_score)."""
        rows = self.conn.execute(
            "SELECT r.week_no, SUM(e.planned) AS planned, SUM(MAX(0, MIN(e.done, e.planned))) AS done "
            "FROM week_review r LEFT JOIN week_entry e ON e.cycle_id = r.cycle_id AND e.week_no = r.week_no "
            "WHERE r.cycle_id = ? GROUP BY r.week_no ORDER BY r.week_no",
            (cycle_id,),
        )
        return {r["week_no"]: r["done"] / r["planned"] if r["planned"] else None for r in rows}

    def goal_progress(self, goal_id: int) -> dict[int, float]:
        rows = self.conn.execute(
            "SELECT week_no, value FROM goal_progress WHERE goal_id = ? ORDER BY week_no", (goal_id,)
        )
        return {r["week_no"]: r["value"] for r in rows}

    def latest_progress(self, goal_id: int) -> float | None:
        progress = self.goal_progress(goal_id)
        return progress[max(progress)] if progress else None

    # -- daily tactic logs ------------------------------------------------------

    def log_tactic(self, tactic_id: int, day: date, delta: int) -> int:
        """Add `delta` to a tactic's count for a day (never below 0). Returns the new count.

        One upsert does the read and the write, so two quick taps on different threads both count.
        """
        with self.conn:
            count = self.conn.execute(
                "INSERT INTO tactic_log (tactic_id, day, count) VALUES (?, ?, MAX(0, ?)) "
                "ON CONFLICT(tactic_id, day) DO UPDATE SET count = MAX(0, count + ?) RETURNING count",
                (tactic_id, day.isoformat(), delta, delta),
            ).fetchone()["count"]
            if not count:
                self.conn.execute(
                    "DELETE FROM tactic_log WHERE tactic_id = ? AND day = ?", (tactic_id, day.isoformat())
                )
        return count

    def logged_on(self, tactic_id: int, day: date) -> int:
        row = self.conn.execute(
            "SELECT count FROM tactic_log WHERE tactic_id = ? AND day = ?", (tactic_id, day.isoformat())
        ).fetchone()
        return row["count"] if row else 0

    def logged_between(self, cycle_id: int, first: date, last: date) -> dict[int, int]:
        """Total logged per tactic over `first`..`last` inclusive (tactics with nothing logged are absent)."""
        rows = self.conn.execute(
            "SELECT l.tactic_id, SUM(l.count) AS total FROM tactic_log l "
            "JOIN tactic t ON t.id = l.tactic_id JOIN goal g ON g.id = t.goal_id "
            "WHERE g.cycle_id = ? AND l.day BETWEEN ? AND ? GROUP BY l.tactic_id",
            (cycle_id, first.isoformat(), last.isoformat()),
        )
        return {r["tactic_id"]: r["total"] for r in rows}

    # -- reflection prompts -----------------------------------------------------

    def list_prompts(self, scope: str, active_only: bool = False) -> list[Prompt]:
        sql = "SELECT * FROM prompt WHERE scope = ?" + (" AND active = 1" if active_only else "")
        return [_prompt(r) for r in self.conn.execute(sql + " ORDER BY position, id", (scope,))]

    def get_prompt(self, prompt_id: int) -> Prompt:
        row = self.conn.execute("SELECT * FROM prompt WHERE id = ?", (prompt_id,)).fetchone()
        if row is None:
            raise KeyError(prompt_id)
        return _prompt(row)

    def save_prompt(self, prompt: Prompt) -> Prompt:
        with self.conn:
            if prompt.id is None:
                pos = self.conn.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 FROM prompt WHERE scope = ?", (prompt.scope,)
                ).fetchone()[0]
                cur = self.conn.execute(
                    "INSERT INTO prompt (scope, position, label, kind, active) VALUES (?, ?, ?, ?, ?)",
                    (prompt.scope, pos, prompt.label, prompt.kind, int(prompt.active)),
                )
                prompt.id, prompt.position = cur.lastrowid, pos
            else:
                self.conn.execute(
                    "UPDATE prompt SET label = ?, kind = ?, active = ? WHERE id = ?",
                    (prompt.label, prompt.kind, int(prompt.active), prompt.id),
                )
        return prompt

    def delete_prompt(self, prompt_id: int) -> None:
        """Past answers keep their copied label; only the link to the prompt is cleared."""
        with self.conn:
            self.conn.execute("DELETE FROM prompt WHERE id = ?", (prompt_id,))

    def move_prompt(self, prompt_id: int, delta: int) -> None:
        prompt = self.get_prompt(prompt_id)
        self._swap(self.list_prompts(prompt.scope), prompt_id, delta, "prompt")

    # -- reflection answers -----------------------------------------------------

    def get_answers(self, cycle_id: int, scope: str, period: str) -> list[Answer]:
        rows = self.conn.execute(
            "SELECT * FROM answer WHERE cycle_id = ? AND scope = ? AND period = ? ORDER BY position, id",
            (cycle_id, scope, period),
        )
        return [Answer(r["prompt_id"], r["label"], r["kind"], r["value"], r["position"]) for r in rows]

    def weekly_answers(self, cycle_id: int) -> dict[int, list[Answer]]:
        """Every weekly review's answers in a cycle, keyed by week number."""
        rows = self.conn.execute(
            "SELECT * FROM answer WHERE cycle_id = ? AND scope = 'weekly' ORDER BY position, id", (cycle_id,)
        )
        answers: dict[int, list[Answer]] = {}
        for r in rows:
            answers.setdefault(int(r["period"]), []).append(
                Answer(r["prompt_id"], r["label"], r["kind"], r["value"], r["position"])
            )
        return answers

    def _replace_answers(self, cycle_id: int, scope: str, period: str, answers: list[Answer]) -> None:
        """Replace a period's answers, inside the caller's transaction."""
        self.conn.execute(
            "DELETE FROM answer WHERE cycle_id = ? AND scope = ? AND period = ?", (cycle_id, scope, period)
        )
        self.conn.executemany(
            "INSERT INTO answer (cycle_id, scope, period, prompt_id, position, label, kind, value) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(cycle_id, scope, period, a.prompt_id, i, a.label, a.kind, a.value) for i, a in enumerate(answers)],
        )

    # -- daily reviews ----------------------------------------------------------

    def get_day_review(self, cycle_id: int, day: date) -> DayReview | None:
        row = self.conn.execute(
            "SELECT * FROM day_review WHERE cycle_id = ? AND day = ?", (cycle_id, day.isoformat())
        ).fetchone()
        if row is None:
            return None
        return DayReview(cycle_id, day, _dt(row["reviewed_at"]), self.get_answers(cycle_id, "daily", day.isoformat()))

    def save_day_review(self, review: DayReview) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO day_review (cycle_id, day) VALUES (?, ?) "
                "ON CONFLICT(cycle_id, day) DO UPDATE SET reviewed_at = CURRENT_TIMESTAMP",
                (review.cycle_id, review.day.isoformat()),
            )
            self._replace_answers(review.cycle_id, "daily", review.day.isoformat(), review.answers)

    def reviewed_days(self, cycle_id: int, first: date, last: date) -> set[date]:
        rows = self.conn.execute(
            "SELECT day FROM day_review WHERE cycle_id = ? AND day BETWEEN ? AND ?",
            (cycle_id, first.isoformat(), last.isoformat()),
        )
        return {date.fromisoformat(r["day"]) for r in rows}

    # -- cycle review (week 13) -------------------------------------------------

    def get_cycle_review(self, cycle_id: int) -> CycleReview | None:
        row = self.conn.execute("SELECT * FROM cycle_review WHERE cycle_id = ?", (cycle_id,)).fetchone()
        if row is None:
            return None
        return CycleReview(cycle_id, row["lessons"], row["celebration"], _dt(row["reviewed_at"]))

    def save_cycle_review(self, review: CycleReview) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO cycle_review (cycle_id, lessons, celebration) VALUES (?, ?, ?) "
                "ON CONFLICT(cycle_id) DO UPDATE SET lessons = excluded.lessons, "
                "celebration = excluded.celebration, reviewed_at = CURRENT_TIMESTAMP",
                (review.cycle_id, review.lessons, review.celebration),
            )
