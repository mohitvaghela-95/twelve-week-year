"""Rules shared by the terminal and web UIs. No Textual or Flask imports here.

Both front ends call these so that review rows, banners, form validation and the
Week 13 flow can't drift apart.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date

from twy.domain.dates import (
    EXECUTION_WEEKS,
    CyclePosition,
    align_to_week_start,
    current_week,
    last_finished_week,
    week_range,
)
from twy.domain.scoring import execution_score, goal_outcome
from twy.models import Cycle, CycleReview, Goal, Tactic, WeekEntry, WeekReview
from twy.repo import Repo

OUTCOMES = ("hit", "missed")


class FormError(ValueError):
    """User input that can't be saved; the message is shown as-is."""


# -- parsing ----------------------------------------------------------------------


def parse_float(text: str) -> float | None:
    text = text.strip()
    return float(text) if text else None


def parse_int(text: str, default: int = 0) -> int:
    text = text.strip()
    return int(text) if text else default


def fmt_num(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:g}"


# -- cycles -----------------------------------------------------------------------


def snap_start(text: str, week_start: int) -> date | None:
    """Parse YYYY-MM-DD and move it forward to the next week-start day."""
    try:
        return align_to_week_start(date.fromisoformat(text.strip()), week_start)
    except ValueError:
        return None


def parse_cycle_form(name: str, start_text: str, week_start: int) -> tuple[str, date]:
    name = name.strip()
    start = snap_start(start_text, week_start)
    if not name:
        raise FormError("Name is required.")
    if start is None:
        raise FormError("Start date must be YYYY-MM-DD.")
    return name, start


def previous_cycle_id(repo: Repo) -> int | None:
    """The cycle a new one copies its plan from by default: the newest one."""
    cycles = repo.list_cycles()
    return cycles[0].id if cycles else None


def create_cycle(repo: Repo, name: str, start: date, copy_from: int | None = None) -> Cycle:
    cycle = repo.create_cycle(name, start, repo.week_start)
    if copy_from is not None:
        repo.copy_plan(copy_from, cycle.id)
    return cycle


@dataclass
class CycleStatus:
    """What the dashboard says about a cycle today."""

    position: CyclePosition
    pending: list[int]  # finished weeks not reviewed yet
    banner: str | None  # read_only | planning | pending | week13 | None
    where: str  # "Week 3 of 12  (12 Oct – 18 Oct)" etc.

    @property
    def show_outcomes(self) -> bool:
        return self.position.in_review_week or self.position.finished


def pending_weeks(repo: Repo, cycle: Cycle, today: date) -> list[int]:
    reviewed = repo.reviewed_weeks(cycle.id)
    return [w for w in range(1, last_finished_week(cycle.start_date, today) + 1) if w not in reviewed]


def cycle_status(repo: Repo, cycle: Cycle, today: date) -> CycleStatus:
    pos = current_week(cycle.start_date, today)
    pending = pending_weeks(repo, cycle, today)
    if cycle.status == "closed":
        banner = "read_only"
    elif cycle.status == "planning":
        banner = "planning"
    elif pending:
        banner = "pending"
    elif pos.in_review_week or pos.finished:
        banner = "week13"
    else:
        banner = None

    if pos.not_started:
        where = f"Starts {cycle.start_date:%a %d %b}"
    elif pos.finished:
        where = "Finished"
    elif pos.in_review_week:
        where = "Week 13 · review"
    else:
        first, last = week_range(cycle.start_date, pos.week_no)
        where = f"Week {pos.week_no} of {EXECUTION_WEEKS}  ({first:%d %b} – {last:%d %b})"
    return CycleStatus(pos, pending, banner, where)


def show_goal_outcomes(cycle: Cycle, status: CycleStatus) -> bool:
    return status.show_outcomes or cycle.status in ("review", "closed")


# -- goals and tactics ------------------------------------------------------------


def parse_goal_form(goal: Goal, fields: Mapping[str, str]) -> Goal:
    """Validate title/why/unit/baseline/target and return an updated copy of `goal`."""
    title = fields.get("title", "").strip()
    if not title:
        raise FormError("Goal title is required.")
    try:
        baseline, target = parse_float(fields.get("baseline", "")), parse_float(fields.get("target", ""))
    except ValueError:
        raise FormError("Baseline and target must be numbers.") from None
    if baseline is not None and target is None:
        raise FormError("Add a target, or clear the baseline.")
    return replace(
        goal,
        title=title,
        why=fields.get("why", "").strip(),
        unit=fields.get("unit", "").strip() or None,
        baseline=baseline,
        target=target,
    )


def parse_tactic_form(tactic: Tactic, fields: Mapping[str, str]) -> Tactic:
    """Validate title/times/start/end and return an updated copy of `tactic`."""
    title = fields.get("title", "").strip()
    if not title:
        raise FormError("Tactic title is required.")
    try:
        times = parse_int(fields.get("times", ""), 1)
        start = parse_int(fields.get("start", ""), 1)
        end = parse_int(fields.get("end", ""), EXECUTION_WEEKS)
    except ValueError:
        raise FormError("Times and weeks must be whole numbers.") from None
    if times < 1:
        raise FormError("Times per week must be at least 1.")
    if not (1 <= start <= end <= EXECUTION_WEEKS):
        raise FormError(f"Weeks must satisfy 1 ≤ from ≤ to ≤ {EXECUTION_WEEKS}.")
    return replace(tactic, title=title, times_per_week=times, start_week=start, end_week=end)


# -- weekly review ----------------------------------------------------------------


@dataclass
class ReviewRow:
    tactic_id: int
    goal_title: str
    title: str
    planned: int
    done: int


def reviewable_weeks(cycle: Cycle, today: date) -> list[int]:
    """Weeks that can be scored so far: 1 up to the current week (max 12)."""
    pos = current_week(cycle.start_date, today)
    if pos.not_started:
        return []
    newest = EXECUTION_WEEKS if pos.week_no is None or pos.week_no > EXECUTION_WEEKS else pos.week_no
    return list(range(1, newest + 1))


def default_review_week(repo: Repo, cycle: Cycle, today: date) -> int | None:
    """The oldest finished week still to review, else the latest finished week."""
    weeks = reviewable_weeks(cycle, today)
    if not weeks:
        return None
    pending = pending_weeks(repo, cycle, today)
    default = pending[0] if pending else max(last_finished_week(cycle.start_date, today), 1)
    return min(default, weeks[-1])


def review_rows(repo: Repo, cycle: Cycle, week_no: int, existing: WeekReview | None = None) -> list[ReviewRow]:
    """Tactics to score for a week.

    A reviewed week shows its snapshot (tactics added later are left out); an
    unreviewed week shows the tactics active in it now.
    """
    if existing is None:
        existing = repo.get_week_review(cycle.id, week_no)
    snapshot = {e.tactic_id: e for e in existing.entries} if existing else {}
    goals = {g.id: g for g in repo.list_goals(cycle.id)}
    rows = []
    for t in repo.list_tactics(cycle.id):
        if t.id in snapshot:
            planned, done = snapshot[t.id].planned, snapshot[t.id].done
        elif t.active_in(week_no) and existing is None:
            planned, done = t.times_per_week, 0
        else:
            continue
        rows.append(ReviewRow(t.id, goals[t.goal_id].title, t.title, planned, done))
    return rows


def week_entries(cycle_id: int, week_no: int, rows: list[ReviewRow], done: Mapping[int, int]) -> list[WeekEntry]:
    """Scorecard entries with done clamped to 0..planned."""
    return [
        WeekEntry(cycle_id, week_no, r.tactic_id, r.planned, max(0, min(done.get(r.tactic_id, 0), r.planned)))
        for r in rows
    ]


def save_week(
    repo: Repo,
    cycle: Cycle,
    week_no: int,
    entries: list[WeekEntry],
    obstacles: str,
    fix: str,
    progress: dict[int, float | None],
) -> float | None:
    """Save a weekly review; the first review moves a planning cycle to active. Returns the score."""
    review = WeekReview(cycle.id, week_no, obstacles=obstacles.strip(), fix=fix.strip(), entries=entries)
    repo.save_week_review(review, progress)
    if cycle.status == "planning":
        repo.set_cycle_status(cycle.id, "active")
    return execution_score(entries)


# -- week 13 ----------------------------------------------------------------------


def save_week13(
    repo: Repo,
    cycle: Cycle,
    finals: Mapping[int, str],
    manual: Mapping[int, str | None],
    lessons: str,
    celebration: str,
) -> None:
    """Save final values / manual outcomes and reflections, and mark the cycle as in review.

    `finals` holds the typed final value per measurable goal, `manual` the hit/missed
    choice for the rest. Nothing is written if any final value is invalid.
    """
    goals = repo.list_goals(cycle.id)
    for g in goals:
        if g.measurable:
            try:
                g.final_value = parse_float(finals.get(g.id, ""))
            except ValueError:
                raise FormError(f"Final value for “{g.title}” must be a number.") from None
            g.outcome = goal_outcome(g)
        else:
            value = manual.get(g.id)
            g.outcome = value if value in OUTCOMES else None
    for g in goals:
        repo.save_goal(g)
    repo.save_cycle_review(CycleReview(cycle.id, lessons=lessons.strip(), celebration=celebration.strip()))
    if cycle.status != "closed":
        repo.set_cycle_status(cycle.id, "review")


def close_cycle(repo: Repo, cycle: Cycle) -> None:
    repo.set_cycle_status(cycle.id, "closed")
