"""Soft warnings based on the book's guidance. They inform; they never block."""

from twy.domain.dates import EXECUTION_WEEKS
from twy.domain.scoring import planned_items
from twy.models import Goal, Tactic

MAX_GOALS = 3
MAX_TACTICS = 8
MAX_WEEKLY_ITEMS = 15


def plan_warnings(goals: list[Goal], tactics: list[Tactic]) -> list[str]:
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
        if t.start_week > t.end_week or not (1 <= t.start_week <= EXECUTION_WEEKS) or not (
            1 <= t.end_week <= EXECUTION_WEEKS
        ):
            warnings.append(f"Tactic “{t.title}” has an invalid week range ({t.start_week}–{t.end_week}).")

    heavy = [w for w in range(1, EXECUTION_WEEKS + 1) if planned_items(tactics, w) > MAX_WEEKLY_ITEMS]
    if heavy:
        weeks = ", ".join(str(w) for w in heavy)
        warnings.append(f"Heavy weeks (> {MAX_WEEKLY_ITEMS} items): {weeks}.")
    return warnings
