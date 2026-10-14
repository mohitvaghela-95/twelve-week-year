"""Turn weekly slots into dated sessions for the Week page and the calendar feed. Pure functions."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from twy.domain.dates import EXECUTION_WEEKS, REVIEW_WEEK, current_week, week_range
from twy.models import Block, Cycle, Slot, Tactic


@dataclass(frozen=True)
class Session:
    """One occurrence of a slot on a real date."""

    day: date
    start_min: int
    duration_min: int
    title: str
    kind: str  # "tactic", or the block's kind (admin, chores, breakout, review, other)
    owner: str  # "tactic" | "block"
    owner_id: int
    slot_id: int
    cycle_id: int
    week_no: int
    context: str = ""  # the goal, for tactics

    @property
    def end_min(self) -> int:
        return self.start_min + self.duration_min

    @property
    def start(self) -> datetime:
        return datetime.combine(self.day, datetime.min.time()) + timedelta(minutes=self.start_min)

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.duration_min)

    @property
    def uid(self) -> str:
        """Stable across feed refreshes, so calendar apps update events instead of duplicating them."""
        return f"{self.owner}-{self.slot_id}-{self.day:%Y%m%d}@twy"


def slot_date(cycle: Cycle, week_no: int, weekday: int) -> date:
    """The date that `weekday` (0 = Monday) falls on in week `week_no` of `cycle`."""
    first, _last = week_range(cycle.start_date, week_no)
    return first + timedelta(days=(weekday - first.weekday()) % 7)


def sessions_for_week(
    cycle: Cycle,
    week_no: int,
    tactics: Iterable[Tactic],
    tactic_slots: Mapping[int, list[Slot]],
    goal_titles: Mapping[int, str],
    blocks: Iterable[Block],
) -> list[Session]:
    """Tactics run in their own week range (never week 13); blocks run in weeks 1–13."""
    sessions: list[Session] = []

    def add(slot: Slot, title: str, kind: str, owner: str, owner_id: int, context: str = "") -> None:
        sessions.append(
            Session(
                day=slot_date(cycle, week_no, slot.weekday),
                start_min=slot.start_min,
                duration_min=slot.duration_min,
                title=title,
                kind=kind,
                owner=owner,
                owner_id=owner_id,
                slot_id=slot.id or 0,
                cycle_id=cycle.id or 0,
                week_no=week_no,
                context=context,
            )
        )

    if 1 <= week_no <= EXECUTION_WEEKS:
        for t in tactics:
            if t.active_in(week_no):
                for slot in tactic_slots.get(t.id, []):
                    add(slot, t.title, "tactic", "tactic", t.id, goal_titles.get(t.goal_id, ""))
    if 1 <= week_no <= REVIEW_WEEK:
        for b in blocks:
            for slot in b.slots:
                add(slot, b.title, b.kind, "block", b.id)
    return sorted(sessions, key=lambda s: (s.day, s.start_min, s.title))


def overlaps(sessions: Iterable[Session]) -> dict[str, list[str]]:
    """Sessions that clash with another on the same day: uid → titles of what they clash with."""
    clashing: dict[str, list[str]] = {}

    def note(s: Session, other: Session) -> None:
        titles = clashing.setdefault(s.uid, [])
        if other.title not in titles:
            titles.append(other.title)

    ordered = sorted(sessions, key=lambda s: (s.day, s.start_min))
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
            if b.day != a.day or b.start_min >= a.start_min + a.duration_min:
                break
            note(a, b)
            note(b, a)
    return clashing


def lanes(sessions: Iterable[Session]) -> dict[str, tuple[int, int]]:
    """Side-by-side columns for the week timeline: uid → (lane, number of lanes in its clash group)."""
    placed: dict[str, int] = {}
    groups: list[list[str]] = []
    lane_ends: list[int] = []
    group_day, group_end = None, -1
    for s in sorted(sessions, key=lambda s: (s.day, s.start_min, s.end_min)):
        if s.day != group_day or s.start_min >= group_end:
            groups.append([])
            lane_ends, group_day, group_end = [], s.day, -1
        free = next((i for i, end in enumerate(lane_ends) if end <= s.start_min), None)
        if free is None:
            free = len(lane_ends)
            lane_ends.append(0)
        lane_ends[free] = s.end_min
        placed[s.uid] = free
        groups[-1].append(s.uid)
        group_end = max(group_end, s.end_min)
    return {uid: (placed[uid], max(placed[u] for u in group) + 1) for group in groups for uid in group}


def cycle_week_at(cycles: Iterable[Cycle], day: date) -> tuple[Cycle, int] | None:
    """The open cycle whose weeks 1–13 contain `day`, with that week number.

    If two cycles cover the day (a new one starting during the old one's week 13),
    the newer one wins, matching Repo.current_cycle.
    """
    found: tuple[Cycle, int] | None = None
    for cycle in cycles:
        if cycle.status == "closed":
            continue
        week_no = current_week(cycle.start_date, day).week_no
        if week_no is not None and (found is None or cycle.start_date > found[0].start_date):
            found = (cycle, week_no)
    return found


def planning_offset(cycle: Cycle, blocks: Iterable[Block]) -> int | None:
    """Days from the start of a cycle week to its first "weekly review" block slot, or None.

    E.g. weeks start Monday and the review block is on Sunday → 6.
    """
    week_first = cycle.start_date.weekday()
    offsets = [(s.weekday - week_first) % 7 for b in blocks if b.kind == "review" for s in b.slots]
    return min(offsets) if offsets else None


def feed_weeks(cycles: list[Cycle], planning: Mapping[int, int | None], today: date) -> list[tuple[Cycle, int]]:
    """The (cycle, week_no) pairs the calendar feed publishes on `today`.

    `planning` maps cycle id → planning_offset(...) for that cycle.
    """
    current = cycle_week_at(cycles, today)
    if current is None:
        return []
    cycle, week_no = current
    weeks = [current]
    offset = planning.get(cycle.id)
    week_first, _week_last = week_range(cycle.start_date, week_no)
    if offset is not None and (today - week_first).days >= offset:
        # Look the date up again so week 13 hands over to the next cycle's week 1 (or to nothing).
        upcoming = cycle_week_at(cycles, week_first + timedelta(days=7))
        if upcoming is not None and upcoming != current:
            weeks.append(upcoming)
    return weeks
