"""Plain dataclasses mirroring the SQLite tables."""

from dataclasses import dataclass, field
from datetime import date, datetime


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
class WeekEntry:
    cycle_id: int
    week_no: int
    tactic_id: int
    planned: int
    done: int


@dataclass
class WeekReview:
    cycle_id: int
    week_no: int
    obstacles: str = ""
    fix: str = ""
    reviewed_at: datetime | None = None
    entries: list[WeekEntry] = field(default_factory=list)


@dataclass
class CycleReview:
    cycle_id: int
    lessons: str = ""
    celebration: str = ""
    reviewed_at: datetime | None = None
