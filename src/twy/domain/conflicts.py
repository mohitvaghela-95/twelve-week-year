"""Calendar clashes between weekly slots, and free times to move a clashing slot to. Pure functions.

Slots are weekly templates, so each pair is checked once rather than week by week: a clash
needs the same weekday, overlapping times, and at least one week in which both items are active.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from twy.domain.dates import REVIEW_WEEK
from twy.models import WEEKDAY_SHORT, Block, Slot, Tactic

Weeks = tuple[int, int]  # first and last cycle week, inclusive

# Suggestions only look between these times (minutes after midnight); Settings can change them.
DEFAULT_DAY_WINDOW = (6 * 60, 22 * 60)
SUGGEST_STEP = 15


@dataclass(frozen=True)
class Placed:
    """A slot with its owner and the weeks that owner is active in."""

    title: str
    owner: str  # "tactic" | "block"
    owner_id: int | None
    weeks: Weeks
    slot: Slot
    row: int | None = None  # 1-based row number, for unsaved slots in a form


@dataclass(frozen=True)
class Clash:
    a: Placed
    b: Placed
    weeks: Weeks


def weeks_label(weeks: Weeks) -> str:
    first, last = weeks
    return f"week {first}" if first == last else f"weeks {first}–{last}"


def shared_weeks(a: Weeks, b: Weeks) -> Weeks | None:
    first, last = max(a[0], b[0]), min(a[1], b[1])
    return (first, last) if first <= last else None


def times_overlap(a: Slot, b: Slot) -> bool:
    """Same weekday and intersecting times. Back-to-back (08:00 end, 08:00 start) is fine."""
    return a.weekday == b.weekday and a.start_min < b.end_min and b.start_min < a.end_min


def clash_weeks(a: Placed, b: Placed) -> Weeks | None:
    """The weeks in which `a` and `b` collide, or None."""
    return shared_weeks(a.weeks, b.weeks) if times_overlap(a.slot, b.slot) else None


def find_clashes(placed: list[Placed]) -> list[Clash]:
    clashes: list[Clash] = []
    for i, a in enumerate(placed):
        for b in placed[i + 1 :]:
            weeks = clash_weeks(a, b)
            if weeks:
                clashes.append(Clash(a, b, weeks))
    return clashes


def placed_for_cycle(
    tactics: Iterable[Tactic],
    tactic_slots: Mapping[int, list[Slot]],
    blocks: Iterable[Block],
    skip: tuple[str, int | None] | None = None,
) -> list[Placed]:
    """Every saved slot in a cycle, except those of `skip` = (owner, id): the item being edited."""
    placed = [
        Placed(t.title, "tactic", t.id, (t.start_week, t.end_week), s)
        for t in tactics
        if ("tactic", t.id) != skip
        for s in tactic_slots.get(t.id, [])
    ]
    placed += [
        Placed(b.title, "block", b.id, (1, REVIEW_WEEK), s) for b in blocks if ("block", b.id) != skip for s in b.slots
    ]
    return placed


@dataclass
class PairWarning:
    """One Plan-page line per pair of clashing items."""

    owner: str  # the first item, whose edit form the line links to
    owner_id: int
    title: str
    other: str | None  # None: two of the item's own slots overlap
    days: list[int]
    weeks: Weeks

    @property
    def text(self) -> str:
        days = ", ".join(WEEKDAY_SHORT[d] for d in self.days)
        other = "itself" if self.other is None else f"“{self.other}”"
        return f"“{self.title}” overlaps {other} on {days} ({weeks_label(self.weeks)})."


def pair_warnings(clashes: Iterable[Clash]) -> list[PairWarning]:
    pairs: dict[tuple, PairWarning] = {}
    for c in clashes:
        key = ((c.a.owner, c.a.owner_id), (c.b.owner, c.b.owner_id))
        same = key[0] == key[1]
        warning = pairs.setdefault(
            key, PairWarning(c.a.owner, c.a.owner_id, c.a.title, None if same else c.b.title, [], c.weeks)
        )
        if c.a.slot.weekday not in warning.days:
            warning.days.append(c.a.slot.weekday)
    for warning in pairs.values():
        warning.days.sort()
    return list(pairs.values())


def free_starts(
    slot: Slot, busy: Iterable[Slot], window: tuple[int, int], step: int = SUGGEST_STEP
) -> tuple[int | None, int | None]:
    """The nearest free start times before and after `slot.start_min`, keeping its length.

    `busy` holds only the slots that matter: same weekday, sharing at least one week.
    A candidate start must sit on the `step` grid (07:00, 07:15, …), fit wholly inside
    `window` = (day starts, day ends), and overlap nothing in `busy`.
    Returns (earlier, later); either is None when there's no such time.
    """
    busy = list(busy)
    low, high = window
    length = slot.duration_min

    def free(start: int) -> bool:
        return not any(times_overlap(Slot(slot.weekday, start, length), b) for b in busy)

    first = -(-low // step) * step  # round up onto the grid, so a 06:10 window starts at 06:15
    grid = range(first, high - length + 1, step)
    earlier = max((s for s in grid if s < slot.start_min and free(s)), default=None)
    later = min((s for s in grid if s > slot.start_min and free(s)), default=None)
    return earlier, later


@dataclass
class RowCheck:
    clashes: list[tuple[Placed, Weeks]]
    earlier: int | None = None
    later: int | None = None


def check_rows(rows: list[Placed], others: list[Placed], window: tuple[int, int]) -> list[RowCheck]:
    """Clashes and suggested moves for each unsaved form row, against the cycle and the form's other rows."""
    results: list[RowCheck] = []
    for row in rows:
        rest = [p for p in rows if p is not row] + others
        hits = [(p, weeks) for p in rest if (weeks := clash_weeks(row, p))]
        check = RowCheck(hits)
        if hits:
            busy = [p.slot for p in rest if p.slot.weekday == row.slot.weekday and shared_weeks(row.weeks, p.weeks)]
            check.earlier, check.later = free_starts(row.slot, busy, window)
        results.append(check)
    return results
