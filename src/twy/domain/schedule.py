"""Turn a week's items into dated sessions for the Week page and the calendar feed. Pure functions."""

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from twy.domain.dates import REVIEW_WEEK, current_week, week_range
from twy.models import Block, Cycle, WeekItem


@dataclass(frozen=True)
class Session:
    """One occurrence of a slot on a real date."""

    day: date
    start_min: int
    duration_min: int
    title: str
    kind: str  # "tactic", "event", or the block's kind (admin, chores, breakout, review, other)
    owner: str  # "tactic" | "block" | "event"
    owner_id: int | None  # None once the tactic or block has been deleted
    key: str  # stable identity for calendar apps; see sessions_from_items
    cycle_id: int
    week_no: int
    context: str = ""  # the goal, for tactics
    item_id: int | None = None  # the stored week item it came from (None before a week is planned)

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
        return f"{self.key}@twy"


def slot_date(cycle: Cycle, week_no: int, weekday: int) -> date:
    """The date that `weekday` (0 = Monday) falls on in week `week_no` of `cycle`."""
    first, _last = week_range(cycle.start_date, week_no)
    return first + timedelta(days=(weekday - first.weekday()) % 7)


def template_items(cycle: Cycle, week_no: int, blocks: Iterable[Block]) -> list[WeekItem]:
    """A week before it is planned: just the blocks' weekly slots (blocks run in weeks 1–13)."""
    if not 1 <= week_no <= REVIEW_WEEK:
        return []
    return [
        WeekItem(
            cycle.id or 0,
            week_no,
            "block",
            s.weekday,
            s.start_min,
            s.duration_min,
            block_id=b.id,
            title=b.title,
            orig_weekday=s.weekday,
            orig_start_min=s.start_min,
        )
        for b in blocks
        for s in b.slots
    ]


def sessions_from_items(
    cycle: Cycle,
    week_no: int,
    items: Iterable[WeekItem],
    goal_titles: Mapping[int, str],
    block_kinds: Mapping[int, str],
) -> list[Session]:
    """Dated sessions for a week's timed items (skipped block occurrences and untimed tactic sessions
    left out; see anytime_from_items for those).

    `goal_titles` maps tactic id → its goal, `block_kinds` block id → kind. Keys stay put when a week
    is planned or re-saved: a tactic's timed sessions (and the week's events) are numbered in time order, and a block occurrence is
    named after the weekly slot it came from, so moving it this week updates the same calendar event.
    """
    sessions: list[Session] = []
    nth: Counter = Counter()
    for item in sorted((i for i in items if i.timed), key=lambda i: (i.weekday, i.start_min)):
        if item.skipped:
            continue
        if item.kind == "tactic":
            owner_id, kind, context = item.tactic_id, "tactic", goal_titles.get(item.tactic_id, "")
            nth[item.tactic_id] += 1
            key = f"tactic-{item.tactic_id}-w{week_no}-{nth[item.tactic_id]}" if item.tactic_id else f"item-{item.id}"
        elif item.kind == "event":
            owner_id, kind, context = None, "event", ""
            nth["event"] += 1
            key = f"event-c{cycle.id}-w{week_no}-{nth['event']}"
        else:
            owner_id, kind, context = item.block_id, block_kinds.get(item.block_id, "other"), ""
            weekday = item.weekday if item.orig_weekday is None else item.orig_weekday
            start = item.start_min if item.orig_start_min is None else item.orig_start_min
            key = f"block-{item.block_id}-{slot_date(cycle, week_no, weekday):%Y%m%d}-{start}"
            key = key if item.block_id else f"item-{item.id}"
        assert item.start_min is not None and item.duration_min is not None
        sessions.append(
            Session(
                day=slot_date(cycle, week_no, item.weekday),
                start_min=item.start_min,
                duration_min=item.duration_min,
                title=item.title,
                kind=kind,
                owner=item.kind,
                owner_id=owner_id,
                key=key,
                cycle_id=cycle.id or 0,
                week_no=week_no,
                context=context,
                item_id=item.id,
            )
        )
    return sorted(sessions, key=lambda s: (s.day, s.start_min, s.title))


@dataclass(frozen=True)
class Anytime:
    """A tactic's untimed sessions on one day of a week: "Drink water ×5" on Wednesday."""

    day: date
    tactic_id: int | None  # None once the tactic has been deleted
    title: str
    count: int
    cycle_id: int
    week_no: int
    context: str = ""  # the goal
    # After a recorded week, when they differ: how many of the tactic's sessions happened that day
    # (with a time or not) and how many were planned there.
    happened: int | None = None
    planned: int | None = None

    @property
    def label(self) -> str:
        return self.title if self.count <= 1 else f"{self.title} ×{self.count}"

    @property
    def uid(self) -> str:
        return f"tactic-{self.tactic_id}-w{self.week_no}-d{self.day:%Y%m%d}@twy"


def anytime_from_items(
    cycle: Cycle, week_no: int, items: Iterable[WeekItem], goal_titles: Mapping[int, str]
) -> list[Anytime]:
    """A week's untimed tactic sessions, counted per tactic per day, by day then title."""
    counts: Counter = Counter()
    titles: dict[tuple[int | None, int], str] = {}
    for item in items:
        if item.kind == "tactic" and not item.timed:
            key = (item.tactic_id, item.weekday)
            counts[key] += 1
            titles.setdefault(key, item.title)
    found = [
        Anytime(
            day=slot_date(cycle, week_no, weekday),
            tactic_id=tactic_id,
            title=titles[(tactic_id, weekday)],
            count=n,
            cycle_id=cycle.id or 0,
            week_no=week_no,
            context=goal_titles.get(tactic_id, "") if tactic_id else "",
        )
        for (tactic_id, weekday), n in counts.items()
    ]
    return sorted(found, key=lambda a: (a.day, a.title))


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


def feed_weeks(
    cycles: list[Cycle],
    planning: Mapping[int, int | None],
    today: date,
    planned: frozenset[tuple[int, int]] | set[tuple[int, int]] = frozenset(),
) -> list[tuple[Cycle, int]]:
    """The (cycle, week_no) pairs the calendar feed publishes on `today`.

    `planning` maps cycle id → planning_offset(...) for that cycle; `planned` holds the
    (cycle id, week_no) pairs already planned. Next week joins the feed once it's planned
    or from the weekly review day, whichever comes first.
    """
    current = cycle_week_at(cycles, today)
    if current is None:
        return []
    cycle, week_no = current
    weeks = [current]
    offset = planning.get(cycle.id)
    week_first, _week_last = week_range(cycle.start_date, week_no)
    # Look the date up again so week 13 hands over to the next cycle's week 1 (or to nothing).
    upcoming = cycle_week_at(cycles, week_first + timedelta(days=7))
    if upcoming is not None and upcoming != current:
        review_day = offset is not None and (today - week_first).days >= offset
        if review_day or (upcoming[0].id, upcoming[1]) in planned:
            weeks.append(upcoming)
    return weeks
