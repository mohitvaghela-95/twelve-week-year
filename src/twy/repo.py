"""All SQL lives here. Screens and domain code talk to the database only through Repo."""

import secrets
import sqlite3
from datetime import date, datetime

from twy.domain.guardrails import DEFAULT_MAX_WEEKLY_ITEMS
from twy.domain.scoring import execution_score
from twy.models import Block, Cycle, CycleReview, Goal, Slot, Tactic, WeekEntry, WeekReview


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


def _owner(column: str) -> str:
    if column not in ("tactic_id", "block_id"):
        raise ValueError(column)
    return column


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
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
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
        """Copy goals, tactics, blocks and their slots (not results) into another cycle."""
        with self.conn:
            for goal in self.list_goals(from_cycle_id):
                cur = self.conn.execute(
                    "INSERT INTO goal (cycle_id, position, title, why, unit, baseline, target) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (to_cycle_id, goal.position, goal.title, goal.why, goal.unit, goal.baseline, goal.target),
                )
                for t in self.list_tactics_for_goal(goal.id):
                    tcur = self.conn.execute(
                        "INSERT INTO tactic (goal_id, position, title, times_per_week, start_week, end_week) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (cur.lastrowid, t.position, t.title, t.times_per_week, t.start_week, t.end_week),
                    )
                    self._insert_slots("tactic_id", tcur.lastrowid, self.list_slots("tactic_id", t.id))
            for b in self.list_blocks(from_cycle_id):
                bcur = self.conn.execute(
                    "INSERT INTO block (cycle_id, position, title, kind) VALUES (?, ?, ?, ?)",
                    (to_cycle_id, b.position, b.title, b.kind),
                )
                self._insert_slots("block_id", bcur.lastrowid, b.slots)

    # -- goals ------------------------------------------------------------------

    def list_goals(self, cycle_id: int) -> list[Goal]:
        rows = self.conn.execute(
            "SELECT * FROM goal WHERE cycle_id = ? ORDER BY position, id", (cycle_id,)
        )
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
        rows = self.conn.execute(
            "SELECT * FROM tactic WHERE goal_id = ? ORDER BY position, id", (goal_id,)
        )
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

    def list_slots(self, owner: str, owner_id: int) -> list[Slot]:
        """Slots of one tactic or block; `owner` is "tactic_id" or "block_id"."""
        rows = self.conn.execute(
            f"SELECT * FROM slot WHERE {_owner(owner)} = ? ORDER BY weekday, start_min, id", (owner_id,)
        )
        return [_slot(r) for r in rows]

    def tactic_slots(self, cycle_id: int) -> dict[int, list[Slot]]:
        """Slots of every tactic in a cycle, keyed by tactic id (tactics without slots are absent)."""
        rows = self.conn.execute(
            "SELECT s.* FROM slot s JOIN tactic t ON t.id = s.tactic_id JOIN goal g ON g.id = t.goal_id "
            "WHERE g.cycle_id = ? ORDER BY s.weekday, s.start_min, s.id",
            (cycle_id,),
        )
        slots: dict[int, list[Slot]] = {}
        for r in rows:
            slots.setdefault(r["tactic_id"], []).append(_slot(r))
        return slots

    def replace_tactic_slots(self, tactic_id: int, slots: list[Slot]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM slot WHERE tactic_id = ?", (tactic_id,))
            self._insert_slots("tactic_id", tactic_id, slots)

    def _insert_slots(self, owner: str, owner_id: int, slots: list[Slot]) -> None:
        """Insert inside the caller's transaction."""
        self.conn.executemany(
            f"INSERT INTO slot ({_owner(owner)}, weekday, start_min, duration_min) VALUES (?, ?, ?, ?)",
            [(owner_id, s.weekday, s.start_min, s.duration_min) for s in slots],
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
            slots=self.list_slots("block_id", row["id"]),
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
            self._insert_slots("block_id", block.id, block.slots)
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

    # -- weekly reviews ---------------------------------------------------------

    def get_week_review(self, cycle_id: int, week_no: int) -> WeekReview | None:
        row = self.conn.execute(
            "SELECT * FROM week_review WHERE cycle_id = ? AND week_no = ?", (cycle_id, week_no)
        ).fetchone()
        if row is None:
            return None
        entries = [
            WeekEntry(r["cycle_id"], r["week_no"], r["tactic_id"], r["planned"], r["done"])
            for r in self.conn.execute(
                "SELECT * FROM week_entry WHERE cycle_id = ? AND week_no = ?", (cycle_id, week_no)
            )
        ]
        return WeekReview(
            cycle_id=cycle_id,
            week_no=week_no,
            obstacles=row["obstacles"],
            fix=row["fix"],
            reviewed_at=_dt(row["reviewed_at"]),
            entries=entries,
        )

    def reviewed_weeks(self, cycle_id: int) -> set[int]:
        rows = self.conn.execute("SELECT week_no FROM week_review WHERE cycle_id = ?", (cycle_id,))
        return {r["week_no"] for r in rows}

    def save_week_review(
        self,
        review: WeekReview,
        progress: dict[int, float | None] | None = None,
    ) -> None:
        """Write the scorecard, reflections and lag readings in one transaction.

        Entries are a snapshot of what was planned, replacing any earlier snapshot for this week.
        """
        with self.conn:
            self.conn.execute(
                "DELETE FROM week_entry WHERE cycle_id = ? AND week_no = ?", (review.cycle_id, review.week_no)
            )
            self.conn.executemany(
                "INSERT INTO week_entry (cycle_id, week_no, tactic_id, planned, done) VALUES (?, ?, ?, ?, ?)",
                [
                    (review.cycle_id, review.week_no, e.tactic_id, e.planned, max(0, min(e.done, e.planned)))
                    for e in review.entries
                ],
            )
            self.conn.execute(
                "INSERT INTO week_review (cycle_id, week_no, obstacles, fix) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(cycle_id, week_no) DO UPDATE SET obstacles = excluded.obstacles, "
                "fix = excluded.fix, reviewed_at = CURRENT_TIMESTAMP",
                (review.cycle_id, review.week_no, review.obstacles, review.fix),
            )
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
        """Execution score for each reviewed week."""
        scores: dict[int, float | None] = {}
        for week_no in sorted(self.reviewed_weeks(cycle_id)):
            review = self.get_week_review(cycle_id, week_no)
            scores[week_no] = execution_score(review.entries) if review else None
        return scores

    def goal_progress(self, goal_id: int) -> dict[int, float]:
        rows = self.conn.execute(
            "SELECT week_no, value FROM goal_progress WHERE goal_id = ? ORDER BY week_no", (goal_id,)
        )
        return {r["week_no"]: r["value"] for r in rows}

    def latest_progress(self, goal_id: int) -> float | None:
        progress = self.goal_progress(goal_id)
        return progress[max(progress)] if progress else None

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
