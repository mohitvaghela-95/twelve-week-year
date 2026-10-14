"""Soft warnings based on the book's guidance. They inform; they never block."""

from collections.abc import Mapping

from twy.domain.dates import EXECUTION_WEEKS
from twy.domain.scoring import planned_items
from twy.models import Goal, Tactic

MAX_GOALS = 3
MAX_TACTICS = 8
# A 5x/week tactic counts as 5 items, so 3 goals with a daily habit each reach ~25 quickly.
DEFAULT_MAX_WEEKLY_ITEMS = 25


def heavy_weeks(tactics: list[Tactic], limit: int) -> list[int]:
    """Execution weeks whose planned items exceed the user's limit."""
    return [w for w in range(1, EXECUTION_WEEKS + 1) if planned_items(tactics, w) > limit]


def plan_warnings(
    goals: list[Goal],
    tactics: list[Tactic],
    max_weekly_items: int = DEFAULT_MAX_WEEKLY_ITEMS,
    slot_counts: Mapping[int, int] | None = None,
) -> list[str]:
    warnings: list[str] = []
    if len(goals) > MAX_GOALS:
        warnings.append(f"{len(goals)} goals: the book recommends 1–{MAX_GOALS}.")
    if len(tactics) > MAX_TACTICS:
        warnings.append(f"{len(tactics)} tactics: more than ~{MAX_TACTICS} is hard to execute.")

    with_tactics = {t.goal_id for t in tactics}
    for g in goals:
        if g.id not in with_tactics:
            warnings.append(f"Goal “{g.title}” has no tactics yet.")

    for t in tactics:
        if (
            t.start_week > t.end_week
            or not (1 <= t.start_week <= EXECUTION_WEEKS)
            or not (1 <= t.end_week <= EXECUTION_WEEKS)
        ):
            warnings.append(f"Tactic “{t.title}” has an invalid week range ({t.start_week}–{t.end_week}).")

    for t in tactics:
        slots = (slot_counts or {}).get(t.id, 0)
        if slots and slots != t.times_per_week:
            word = "slot" if slots == 1 else "slots"
            warnings.append(f"“{t.title}” has {slots} {word} in the calendar but is planned {t.times_per_week}×/week.")

    heavy = heavy_weeks(tactics, max_weekly_items)
    if heavy:
        weeks = ", ".join(str(w) for w in heavy)
        warnings.append(f"Heavy weeks (> {max_weekly_items} items): {weeks}.")
    return warnings
