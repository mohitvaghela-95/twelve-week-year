"""Week arithmetic for a 12-week cycle. Date-only, so time zones and DST never matter."""

from dataclasses import dataclass
from datetime import date, timedelta

EXECUTION_WEEKS = 12
REVIEW_WEEK = 13
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def align_to_week_start(d: date, week_start: int) -> date:
    """Return the first day on or after `d` that falls on `week_start` (0=Mon … 6=Sun)."""
    return d + timedelta(days=(week_start - d.weekday()) % 7)


def week_range(start_date: date, week_no: int) -> tuple[date, date]:
    """First and last day of week `week_no` (1-based) of a cycle starting on `start_date`."""
    if week_no < 1:
        raise ValueError("week_no must be >= 1")
    first = start_date + timedelta(days=7 * (week_no - 1))
    return first, first + timedelta(days=6)


def cycle_end(start_date: date) -> date:
    """Last day of week 12."""
    return week_range(start_date, EXECUTION_WEEKS)[1]


@dataclass(frozen=True)
class CyclePosition:
    """Where `today` falls in a cycle."""

    week_no: int | None  # 1..13 while in the cycle, else None
    not_started: bool = False
    finished: bool = False  # past week 13

    @property
    def in_review_week(self) -> bool:
        return self.week_no == REVIEW_WEEK


def current_week(start_date: date, today: date) -> CyclePosition:
    if today < start_date:
        return CyclePosition(week_no=None, not_started=True)
    week_no = (today - start_date).days // 7 + 1
    if week_no > REVIEW_WEEK:
        return CyclePosition(week_no=None, finished=True)
    return CyclePosition(week_no=week_no)


def last_finished_week(start_date: date, today: date) -> int:
    """Number of execution weeks (0..12) whose last day is before `today`."""
    if today <= start_date:
        return 0
    return min((today - start_date).days // 7, EXECUTION_WEEKS)
