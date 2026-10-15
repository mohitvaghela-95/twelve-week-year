"""Plan vs what actually happened, session by session. Pure functions.

After a week ends its plan is frozen, and what you record about the week is kept beside it.
Each recorded session points at the planned one it records (`plan_item_id`), so a session
moved close to another of the same tactic can't be mistaken for it. Tactic sessions and
block occurrences are compared; one-off events are context only.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from twy.models import WEEKDAY_SHORT, WeekItem, hhmm

STATUSES = ("kept", "moved", "dropped", "added")


def when(item: WeekItem) -> str:
    return f"{WEEKDAY_SHORT[item.weekday]} {hhmm(item.start_min)}"


def comparable(items: Iterable[WeekItem]) -> list[WeekItem]:
    """Planned tactic sessions and block occurrences that still have their tactic or block.

    Skipped blocks weren't meant to happen, and a deleted tactic's sessions can't be recorded.
    """
    return [
        i
        for i in items
        if not i.skipped
        and ((i.kind == "tactic" and i.tactic_id is not None) or (i.kind == "block" and i.block_id is not None))
    ]


@dataclass(frozen=True)
class Change:
    status: str  # kept | moved | dropped | added
    plan: WeekItem | None
    actual: WeekItem | None

    @property
    def label(self) -> str:
        """How the Week page tags a session: "moved from Tue 07:00", "didn't happen", ..."""
        if self.status == "moved":
            return f"moved from {when(self.plan)}"
        if self.status == "dropped":
            return "didn't happen"
        if self.status == "added":
            return "added"
        return ""

    @property
    def ghost_label(self) -> str:
        """What a plan's faint outline says where a session was meant to be."""
        return f"moved to {when(self.actual)}" if self.status == "moved" else "didn't happen"


def compare(plan: list[WeekItem], actual: list[WeekItem] | None) -> list[Change]:
    """Every planned and recorded session, matched by link. `actual=None`: not recorded, so as planned."""
    planned = comparable(plan)
    if actual is None:
        return [Change("kept", p, p) for p in planned]
    by_plan = {a.plan_item_id: a for a in actual if a.plan_item_id is not None}
    changes = []
    for p in planned:
        a = by_plan.pop(p.id, None)
        if a is None:
            changes.append(Change("dropped", p, None))
        elif (a.weekday, a.start_min, a.duration_min) == (p.weekday, p.start_min, p.duration_min):
            changes.append(Change("kept", p, a))
        else:
            changes.append(Change("moved", p, a))
    linked = {id(a) for a in by_plan.values()}  # linked to a plan row that's gone: counts as added
    changes += [Change("added", None, a) for a in actual if a.plan_item_id is None or id(a) in linked]
    return sorted(changes, key=lambda c: _sort_key(c.actual or c.plan))


def _sort_key(item: WeekItem) -> tuple[int, int]:
    return item.weekday, item.start_min


def summary(changes: Iterable[Change]) -> str:
    """ "2 moved, 1 didn't happen, 1 added", or "Went to plan"."""
    changes = list(changes)
    parts = [
        (sum(1 for c in changes if c.status == "moved"), "moved"),
        (sum(1 for c in changes if c.status == "dropped"), "didn't happen"),
        (sum(1 for c in changes if c.status == "added"), "added"),
    ]
    text = ", ".join(f"{n} {what}" for n, what in parts if n)
    return text or "Went to plan"
