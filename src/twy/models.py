"""Plain dataclasses mirroring the SQLite tables."""

from dataclasses import dataclass, field
from datetime import date, datetime

WEEKDAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def hhmm(minutes: int) -> str:
    """Minutes after midnight as 24-hour HH:MM (1440 shows as 24:00)."""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@dataclass
class Cycle:
    id: int | None
    name: str
    start_date: date
    week_start: int
    status: str = "planning"  # planning | active | review | closed
    created_at: datetime | None = None


@dataclass
class Goal:
    id: int | None
    cycle_id: int
    title: str
    why: str = ""
    unit: str | None = None
    baseline: float | None = None
    target: float | None = None
    final_value: float | None = None
    outcome: str | None = None  # hit | missed | None
    position: int = 0

    @property
    def measurable(self) -> bool:
        return self.target is not None


@dataclass
class Tactic:
    id: int | None
    goal_id: int
    title: str
    times_per_week: int = 1
    start_week: int = 1
    end_week: int = 12
    position: int = 0

    def active_in(self, week_no: int) -> bool:
        return self.start_week <= week_no <= self.end_week


@dataclass
class Slot:
    """A weekly time for a tactic or block: weekday (0 = Monday), start and length in minutes."""

    weekday: int
    start_min: int
    duration_min: int
    id: int | None = None

    @property
    def end_min(self) -> int:
        return self.start_min + self.duration_min

    @property
    def time_label(self) -> str:
        return f"{hhmm(self.start_min)}–{hhmm(self.end_min)}"

    @property
    def label(self) -> str:
        return f"{WEEKDAY_SHORT[self.weekday]} {self.time_label}"


BLOCK_KINDS = {
    "admin": "Admin",
    "chores": "Chores",
    "breakout": "Breakout",
    "review": "Weekly review",
    "other": "Other",
}


@dataclass
class Block:
    """Time set aside that isn't a tactic: admin, chores, breakout, the weekly review."""

    id: int | None
    cycle_id: int
    title: str
    kind: str = "other"
    position: int = 0
    slots: list[Slot] = field(default_factory=list)


@dataclass
class WeekEntry:
    """One tactic's line on a reviewed week's scorecard: a snapshot that outlives the tactic."""

    cycle_id: int
    week_no: int
    tactic_id: int | None  # None once the tactic has been deleted
    planned: int
    done: int
    title: str = ""  # the tactic's title when the week was reviewed


PROMPT_KINDS = {"text": "Text", "rating": "Rating 1–5", "yesno": "Yes / no"}
PROMPT_SCOPES = ("daily", "weekly")


@dataclass
class Prompt:
    """A reflection question asked in every daily or weekly review."""

    id: int | None
    scope: str  # daily | weekly
    label: str
    kind: str = "text"  # text | rating | yesno
    active: bool = True
    position: int = 0


@dataclass
class Answer:
    """One reflection answer. label and kind are copied from the prompt when saved."""

    prompt_id: int | None
    label: str
    kind: str
    value: str
    position: int = 0


@dataclass
class WeekReview:
    cycle_id: int
    week_no: int
    reviewed_at: datetime | None = None
    entries: list[WeekEntry] = field(default_factory=list)
    answers: list[Answer] = field(default_factory=list)


@dataclass
class DayReview:
    cycle_id: int
    day: date
    reviewed_at: datetime | None = None
    answers: list[Answer] = field(default_factory=list)


@dataclass
class CycleReview:
    cycle_id: int
    lessons: str = ""
    celebration: str = ""
    reviewed_at: datetime | None = None
