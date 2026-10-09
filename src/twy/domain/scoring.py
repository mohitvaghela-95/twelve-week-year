"""Execution scoring and goal outcomes. Pure functions over models."""

from collections.abc import Iterable

from twy.models import Goal, Tactic, WeekEntry

TARGET_SCORE = 0.85


def planned_items(tactics: Iterable[Tactic], week_no: int) -> int:
    """Items planned for a week: repeating tactics count once per occurrence."""
    return sum(t.times_per_week for t in tactics if t.active_in(week_no))


def execution_score(entries: Iterable[WeekEntry]) -> float | None:
    """Share of planned items completed (0..1), or None if nothing was planned."""
    planned = done = 0
    for e in entries:
        planned += e.planned
        done += max(0, min(e.done, e.planned))
    return done / planned if planned else None


def cycle_average(scores: Iterable[float | None]) -> float | None:
    """Mean of the reviewed weeks' scores, skipping weeks with nothing planned."""
    real = [s for s in scores if s is not None]
    return sum(real) / len(real) if real else None


def goal_outcome(goal: Goal, latest_value: float | None = None) -> str | None:
    """'hit' / 'missed' for a measurable goal, else the manually set outcome.

    Direction comes from baseline -> target, so "lose 5 kg" (80 -> 75) and
    "read 6 books" (0 -> 6) both work. Without a baseline we assume "reach or exceed".
    """
    if goal.target is None:
        return goal.outcome
    value = goal.final_value if goal.final_value is not None else latest_value
    if value is None:
        return None
    decreasing = goal.baseline is not None and goal.target < goal.baseline
    hit = value <= goal.target if decreasing else value >= goal.target
    return "hit" if hit else "missed"


def score_band(score: float | None) -> str:
    """CSS-ish band name used by the UI: good / warn / bad / none."""
    if score is None:
        return "none"
    if score >= TARGET_SCORE:
        return "good"
    if score >= 0.65:
        return "warn"
    return "bad"


def format_score(score: float | None) -> str:
    return "—" if score is None else f"{score:.0%}"
