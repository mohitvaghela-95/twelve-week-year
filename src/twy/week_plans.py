"""Week-by-week planning: which weeks can be planned, what a week holds, and saving a week's plan.

Tactics have no fixed times. Each week you place its sessions around whatever else is going on,
the book's "plan your week" step. Blocks stay weekly templates: a week that hasn't been planned
shows them as they are, and saving a week's plan stores its block occurrences alongside the
tactic sessions, so a past week keeps the plan it had. A week's plan can also skip or move a
block for that week only, and hold one-off events. Events from your other calendars (see
twy.calendars) show alongside, and count when checking for clashes. No Flask imports here.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from itertools import zip_longest

from twy.calendars import all_day_dates, busy, day_spans
from twy.domain.compare import Change, comparable, compare, log_hints, summary, when
from twy.domain.conflicts import Placed, placed_for_week
from twy.domain.dates import EXECUTION_WEEKS, REVIEW_WEEK, current_week, week_range
from twy.domain.schedule import (
    Anytime,
    Session,
    anytime_from_items,
    planning_offset,
    sessions_from_items,
    template_items,
)
from twy.models import WEEKDAY_SHORT, Block, Cycle, Slot, Tactic, WeekItem, hhmm
from twy.repo import Repo
from twy.services import FormError, clash_hints, form_slots, parse_slot_row, week_of

# Without a weekly review block, next week's "not planned yet" reminder starts on the week's 6th day.
DEFAULT_PLANNING_OFFSET = 5


def week_ended(cycle: Cycle, week_no: int, today: date) -> bool:
    return today > week_range(cycle.start_date, week_no)[1]


def plannable_weeks(cycle: Cycle, today: date) -> list[int]:
    """This week and next. Before the cycle starts, week 1 (any time); none once it's closed or over."""
    if cycle.status == "closed":
        return []
    pos = current_week(cycle.start_date, today)
    if pos.not_started:
        return [1]
    if pos.week_no is None:
        return []
    return [w for w in (pos.week_no, pos.week_no + 1) if w <= REVIEW_WEEK]


def open_planned_weeks(repo: Repo, cycle: Cycle, today: date) -> list[int]:
    """Planned weeks that haven't ended: the only plans that may still change."""
    return sorted(w for w in repo.planned_weeks(cycle.id) if not week_ended(cycle, w, today))


def active_tactics(repo: Repo, cycle: Cycle, week_no: int) -> list[Tactic]:
    if week_no > EXECUTION_WEEKS:
        return []
    return [t for t in repo.list_tactics(cycle.id) if t.active_in(week_no)]


def resolved_items(repo: Repo, cycle: Cycle, week_no: int) -> list[WeekItem]:
    """A week's plan as saved, or, if it hasn't been planned, its blocks' weekly slots."""
    if week_no in repo.planned_weeks(cycle.id):
        return repo.week_items(cycle.id, week_no)
    return template_items(cycle, week_no, repo.list_blocks(cycle.id))


def tactic_contexts(repo: Repo, cycle: Cycle) -> dict[int, str]:
    """Tactic id → its goal's title."""
    goals = {g.id: g.title for g in repo.list_goals(cycle.id)}
    return {t.id: goals.get(t.goal_id, "") for t in repo.list_tactics(cycle.id) if t.id is not None}


def week_sessions(repo: Repo, cycle: Cycle, week_no: int) -> list[Session]:
    """Every timed session in one cycle week, in time order."""
    kinds = {b.id: b.kind for b in repo.list_blocks(cycle.id)}
    return sessions_from_items(
        cycle, week_no, resolved_items(repo, cycle, week_no), tactic_contexts(repo, cycle), kinds
    )


def week_anytime(repo: Repo, cycle: Cycle, week_no: int) -> list[Anytime]:
    """The tactic sessions planned for a day of the week but not a time."""
    return anytime_from_items(cycle, week_no, resolved_items(repo, cycle, week_no), tactic_contexts(repo, cycle))


def planned_on(repo: Repo, cycle: Cycle, day: date) -> dict[int, int] | None:
    """Tactic id → how many sessions the week's plan puts on `day`, or None if the week isn't planned."""
    week_no = week_of(cycle, day)
    if week_no not in repo.planned_weeks(cycle.id):
        return None
    return dict(
        Counter(
            i.tactic_id
            for i in repo.week_items(cycle.id, week_no)
            if i.kind == "tactic" and i.tactic_id is not None and i.weekday == day.weekday()
        )
    )


@dataclass
class Imported:
    """A week's events from your other calendars, ready to show."""

    timed: list[Session] = field(default_factory=list)  # split at midnight, one per day
    all_day: dict[date, list[str]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def on(self, day: date) -> list[Session]:
        return [s for s in self.timed if s.day == day]


def imports_for_week(repo: Repo, cycle: Cycle, week_no: int) -> Imported:
    """Events from the calendar links in Settings during one cycle week (none if there are no links)."""
    urls = repo.import_urls
    if not urls:
        return Imported()
    first, last = week_range(cycle.start_date, week_no)
    events, errors = busy(urls, first, last)
    imported = Imported(errors=errors)
    for n, event in enumerate(events, start=1):
        if event.all_day:
            for day in all_day_dates(event, first, last):
                imported.all_day.setdefault(day, []).append(event.title)
            continue
        for day, start, end in day_spans(event, first, last):
            imported.timed.append(
                Session(
                    day=day,
                    start_min=start,
                    duration_min=end - start,
                    title=event.title,
                    kind="import",
                    owner="import",
                    owner_id=None,
                    key=f"import-{n}-{day:%Y%m%d}",
                    cycle_id=cycle.id or 0,
                    week_no=week_no,
                    context="Your calendar",
                )
            )
    return imported


def import_placed(imported: Imported, week_no: int) -> list[Placed]:
    """Timed imported events for clash checks (all-day ones never clash)."""
    return [
        Placed(s.title, "import", None, (week_no, week_no), Slot(s.day.weekday(), s.start_min, s.duration_min))
        for s in imported.timed
    ]


def shortfall(tactics: list[Tactic], items: list[WeekItem]) -> list[tuple[Tactic, int]]:
    """Tactics with fewer sessions placed than their times per week, with how many are placed."""
    placed = Counter(i.tactic_id for i in items if i.kind == "tactic")
    return [(t, placed[t.id]) for t in tactics if placed[t.id] < t.times_per_week]


# -- the planning form ------------------------------------------------------------

# One row per session, as parallel lists: kind (tactic | block | event), ref (tactic or block id),
# origin ("weekday:start" of a block's weekly slot), title (events), day, time and minutes, and
# when recording what happened, plan (the id of the planned session the row records). On the
# tactic grid each session is a hidden row inside its day's cell; a blank time is "no set time".
FORM_FIELDS = (
    "slot_kind",
    "slot_ref",
    "slot_origin",
    "slot_title",
    "slot_day",
    "slot_time",
    "slot_minutes",
    "slot_plan",
)
STALE = "Something on this page has changed. Reload the page and try again."
EVENT_TITLE_MAX = 120


@dataclass
class FormRow:
    kind: str
    ref: str = ""
    origin: str = ""
    title: str = ""
    day: str = "0"
    time: str = ""
    minutes: str = "60"
    plan: str = ""

    @property
    def view(self) -> dict:
        return {"day": self.day, "time": self.time, "minutes": self.minutes, "title": self.title}


def form_rows(lists: Mapping[str, list[str]]) -> list[FormRow]:
    """The planning form's parallel lists, zipped into rows."""
    columns = [lists.get(name, []) for name in FORM_FIELDS]
    return [FormRow(*values) for values in zip_longest(*columns, fillvalue="")]


def parse_origin(text: str) -> tuple[int, int] | None:
    """A block row's "weekday:start" back to numbers, or None if it was tampered with."""
    day, _, start = text.partition(":")
    if not (day.isdigit() and start.isdigit() and 0 <= int(day) <= 6 and 0 <= int(start) < 24 * 60):
        return None
    return int(day), int(start)


def usual_label(origin: tuple[int, int]) -> str:
    return f"{WEEKDAY_SHORT[origin[0]]} {hhmm(origin[1])}"


@dataclass
class DayCell:
    """One tactic on one day of the week grid: its sessions there, each with an optional time."""

    day: date
    sessions: list[dict] = field(default_factory=list)  # {"time", "minutes", "plan"} as strings
    planned: list[dict] = field(default_factory=list)  # recording: the plan's sessions this day, with "id"

    @property
    def weekday(self) -> int:
        return self.day.weekday()

    @property
    def count(self) -> int:
        return len(self.sessions)


@dataclass
class TacticGroup:
    """One tactic on the week grid: a cell for each day of the cycle week, in week order."""

    tactic: Tactic
    goal_title: str
    days: list[DayCell] = field(default_factory=list)

    @property
    def placed(self) -> int:
        return sum(c.count for c in self.days)

    @property
    def over(self) -> int:
        return max(self.placed - self.tactic.times_per_week, 0)


@dataclass
class BlockGroup:
    """One block on the planning page: its occurrences this week. A blank time skips one."""

    block: Block
    rows: list[dict] = field(default_factory=list)  # also "origin" and "usual"


@dataclass
class PlanForm:
    tactics: list[TacticGroup]
    blocks: list[BlockGroup]
    events: list[dict]  # {"title", "day", "time", "minutes"}


def week_days(cycle: Cycle, week_no: int) -> list[date]:
    """The seven dates of a cycle week, in order."""
    first, _last = week_range(cycle.start_date, week_no)
    return [first + timedelta(days=n) for n in range(7)]


def session_view(item: WeekItem, plan: str = "") -> dict:
    """A tactic session as the grid shows it: a blank time means no set time."""
    if item.start_min is None:
        return {"time": "", "minutes": "", "plan": plan}
    return {"time": hhmm(item.start_min), "minutes": str(item.duration_min), "plan": plan}


def by_time(item: WeekItem) -> tuple[int, int]:
    """Timed sessions first, in time order, then the ones with no set time."""
    return (0, item.start_min) if item.start_min is not None else (1, 0)


def build_cells(
    cycle: Cycle, week_no: int, sessions: list[tuple[int, dict]], planned: list[tuple[int, dict]] = ()
) -> list[DayCell]:
    """A grid row's seven cells from (weekday, session view) pairs, in the order given."""
    cells = {d.weekday(): DayCell(d) for d in week_days(cycle, week_no)}
    for weekday, view in sessions:
        cells[weekday].sessions.append(view)
    for weekday, view in planned:
        cells[weekday].planned.append(view)
    return list(cells.values())


def tactic_groups(repo: Repo, cycle: Cycle, week_no: int, items: list[WeekItem]) -> list[TacticGroup]:
    """Each active tactic with its sessions, day by day."""
    goals = {g.id: g.title for g in repo.list_goals(cycle.id)}
    groups = []
    for t in active_tactics(repo, cycle, week_no):
        mine = sorted((i for i in items if i.kind == "tactic" and i.tactic_id == t.id), key=by_time)
        cells = build_cells(cycle, week_no, [(i.weekday, session_view(i)) for i in mine])
        groups.append(TacticGroup(t, goals.get(t.goal_id, ""), cells))
    return groups


def blank_event(cycle: Cycle) -> dict:
    return {"title": "", "day": str(cycle.start_date.weekday()), "time": "", "minutes": "60"}


def plan_form(repo: Repo, cycle: Cycle, week_no: int, items: list[WeekItem]) -> PlanForm:
    """The planning form for a week's items: tactics, then blocks (in Plan order), then events."""
    blocks = []
    for b in repo.list_blocks(cycle.id):
        rows = []
        for i in items:
            if i.kind == "block" and i.block_id == b.id:
                origin = (i.weekday, i.start_min) if i.orig_weekday is None else (i.orig_weekday, i.orig_start_min)
                rows.append(
                    {
                        "day": str(i.weekday),
                        "time": "" if i.skipped else hhmm(i.start_min),
                        "minutes": str(i.duration_min),
                        "origin": f"{origin[0]}:{origin[1]}",
                        "usual": usual_label(origin),
                    }
                )
        if rows:
            blocks.append(BlockGroup(b, rows))
    events = [
        {"title": i.title, "day": str(i.weekday), "time": hhmm(i.start_min), "minutes": str(i.duration_min)}
        for i in items
        if i.kind == "event"
    ]
    return PlanForm(tactic_groups(repo, cycle, week_no, items), blocks, [*events, blank_event(cycle)])


def posted_form(
    repo: Repo,
    cycle: Cycle,
    week_no: int,
    rows: list[FormRow],
    plan_items: Mapping[int, WeekItem] | None = None,
) -> PlanForm:
    """The planning (or, with `plan_items`, recording) form as it was posted, to show it again with an error."""
    goals = {g.id: g.title for g in repo.list_goals(cycle.id)}
    tactic_list = repo.list_tactics(cycle.id)
    posted: dict[int, list[tuple[int, dict]]] = {t.id: [] for t in tactic_list}
    blocks = {b.id: BlockGroup(b) for b in repo.list_blocks(cycle.id)}
    events = []
    for row in rows:
        ref = int(row.ref) if row.ref.isdigit() else None
        view = row.view
        if row.kind == "tactic" and ref in posted and row.day.isdigit() and int(row.day) <= 6:
            minutes = row.minutes if row.time.strip() else ""
            posted[ref].append((int(row.day), {"time": row.time, "minutes": minutes, "plan": row.plan}))
            continue
        if plan_items is not None:
            planned = plan_items.get(int(row.plan)) if row.plan.isdigit() else None
            view |= {"plan": row.plan, "note": record_note(planned, row.time)}
        if row.kind == "block" and ref in blocks and (origin := parse_origin(row.origin)):
            blocks[ref].rows.append(view | {"origin": row.origin, "usual": usual_label(origin)})
        elif row.kind == "event":
            events.append(view)
    shown = []
    for t in tactic_list:
        if not (posted[t.id] or t.active_in(week_no) and week_no <= EXECUTION_WEEKS):
            continue
        planned_cells = planned_views(plan_items.values(), t.id) if plan_items is not None else []
        shown.append(TacticGroup(t, goals.get(t.goal_id, ""), build_cells(cycle, week_no, posted[t.id], planned_cells)))
    if plan_items is not None:
        return PlanForm(shown, [b for b in blocks.values() if b.rows], [])
    return PlanForm(shown, [b for b in blocks.values() if b.rows], events or [blank_event(cycle)])


def parse_plan_rows(
    cycle: Cycle,
    week_no: int,
    tactics: list[Tactic],
    blocks: list[Block],
    rows: list[FormRow],
    plan_items: Mapping[int, WeekItem] | None = None,
) -> list[WeekItem]:
    """A week's items from the planning form, or with `plan_items`, from recording what happened.

    Every tactic row is a session on its day; without a time it has no set time. Event rows
    without a time are left out. When planning, a block row without a time is that occurrence
    skipped this week, and one at another time or length is moved. When recording, rows are the
    "actual" layer: a tactic session that didn't happen is simply not posted, a block row with a
    blank time didn't happen, and each row may name the planned session (by id, from
    `plan_items`) it records.
    """
    recording = plan_items is not None
    layer = "actual" if recording else "plan"
    tactic_by_id = {t.id: t for t in tactics}
    block_by_id = {b.id: b for b in blocks}
    nth: Counter = Counter()
    items: list[WeekItem] = []
    linked: set[int] = set()

    def link(row: FormRow, kind: str, owner_id: int) -> int | None:
        """The planned session a recorded row stands for, checked against this week's plan."""
        if not row.plan:
            return None
        planned = (plan_items or {}).get(int(row.plan)) if row.plan.isdigit() else None
        owner = None if planned is None else (planned.tactic_id if kind == "tactic" else planned.block_id)
        if planned is None or planned.kind != kind or owner != owner_id or planned.id in linked:
            raise FormError(STALE)
        linked.add(planned.id)
        return planned.id

    def slot_for(row: FormRow, label: str):
        try:
            return parse_slot_row(row.day, row.time, row.minutes)
        except FormError as e:
            raise FormError(f"{label}: {e}") from None

    for row in rows:
        ref = int(row.ref) if row.ref.isdigit() else None
        if row.kind == "tactic":
            tactic = tactic_by_id.get(ref)
            if tactic is None:
                raise FormError(STALE)
            if not (row.day.isdigit() and 0 <= int(row.day) <= 6):
                raise FormError(STALE)
            nth[(tactic.id, row.day)] += 1
            plan_item_id = link(row, "tactic", tactic.id) if recording else None
            if row.time.strip():
                label = f"{tactic.title}, {WEEKDAY_SHORT[int(row.day)]} session {nth[(tactic.id, row.day)]}"
                when_ = slot_fields(slot_for(row, label))
            else:
                when_ = (int(row.day), None, None)
            items.append(
                WeekItem(
                    cycle.id,
                    week_no,
                    "tactic",
                    *when_,
                    layer=layer,
                    tactic_id=tactic.id,
                    title=tactic.title,
                    plan_item_id=plan_item_id,
                )
            )
        elif row.kind == "block":
            block, origin = block_by_id.get(ref), parse_origin(row.origin)
            if block is None or origin is None:
                raise FormError(STALE)
            usual = next((s for s in block.slots if (s.weekday, s.start_min) == origin), None)
            base = WeekItem(
                cycle.id,
                week_no,
                "block",
                origin[0],
                origin[1],
                usual.duration_min if usual else 30,
                block_id=block.id,
                title=block.title,
                orig_weekday=origin[0],
                orig_start_min=origin[1],
            )
            if recording:
                base = replace(base, layer=layer, plan_item_id=link(row, "block", block.id))
                if not row.time.strip():
                    continue  # didn't happen
            elif not row.time.strip():
                items.append(replace(base, skipped=True, custom=True))
                continue
            slot = slot_for(row, f"{block.title} (usually {usual_label(origin)})")
            moved = slot_fields(slot) != (base.weekday, base.start_min, base.duration_min)
            items.append(
                replace(
                    base,
                    weekday=slot.weekday,
                    start_min=slot.start_min,
                    duration_min=slot.duration_min,
                    custom=moved and not recording,
                )
            )
        elif row.kind == "event" and not recording:
            nth["event"] += 1
            title = row.title.strip()[:EVENT_TITLE_MAX]
            if not row.time.strip():
                if title:
                    raise FormError(f"“{title}” needs a start time.")
                continue
            if not title:
                raise FormError(f"Event {nth['event']}: give it a name.")
            slot = slot_for(row, f"“{title}”")
            items.append(WeekItem(cycle.id, week_no, "event", *slot_fields(slot), title=title))
        else:
            raise FormError(STALE)
    return items


def slot_fields(slot) -> tuple[int, int, int]:
    return slot.weekday, slot.start_min, slot.duration_min


def save_week_plan(repo: Repo, cycle: Cycle, week_no: int, today: date, items: list[WeekItem]) -> None:
    """Save a week's plan, as posted from the planning page, and mark the week planned.

    A block with no rows in `items` wasn't on the page (say, added in another tab), so its
    occurrences are kept as they stand. Skipping one is a row too, so nothing is lost by accident.
    """
    if week_no not in plannable_weeks(cycle, today) or week_ended(cycle, week_no, today):
        raise FormError(f"Week {week_no} can't be planned now.")
    posted = {i.block_id for i in items if i.kind == "block"}
    kept = [i for i in resolved_items(repo, cycle, week_no) if i.kind == "block" and i.block_id not in posted]
    repo.replace_week_items(cycle.id, week_no, "plan", items + kept)


def copy_prev_items(repo: Repo, cycle: Cycle, week_no: int) -> list[WeekItem] | None:
    """Last week's tactic times for the tactics active this week, or None if last week wasn't planned.

    Blocks and events aren't copied: they stay as this week has them.
    """
    if week_no <= 1 or week_no - 1 not in repo.planned_weeks(cycle.id):
        return None
    active = {t.id for t in active_tactics(repo, cycle, week_no)}
    return [
        replace(i, week_no=week_no, id=None)
        for i in repo.week_items(cycle.id, week_no - 1)
        if i.kind == "tactic" and i.tactic_id in active
    ]


def week_plan_check(repo: Repo, cycle: Cycle, week_no: int, rows: list[FormRow]) -> dict:
    """Live hints for the planning form: clashes per row, and how many sessions each tactic has placed."""
    tactics = {t.id: t for t in active_tactics(repo, cycle, week_no)}
    blocks = {b.id: b for b in repo.list_blocks(cycle.id)}
    slots = form_slots([r.day for r in rows], [r.time for r in rows], [r.minutes for r in rows])
    placed_rows: list[Placed | None] = []
    for n, (row, slot) in enumerate(zip(rows, slots, strict=True), start=1):
        ref = int(row.ref) if row.ref.isdigit() else None
        if row.kind == "tactic" and ref in tactics:
            owner = (tactics[ref].title, "tactic", ref)
        elif row.kind == "block" and ref in blocks:
            owner = (blocks[ref].title, "block", ref)
        elif row.kind == "event":
            owner = (row.title.strip() or "an event", "event", None)
        else:
            owner = None
        placed_rows.append(None if slot is None or owner is None else Placed(*owner, (week_no, week_no), slot, n))
    posted = {int(r.ref) for r in rows if r.kind == "block" and r.ref.isdigit()}
    others = placed_for_week(
        week_no,
        [i for i in resolved_items(repo, cycle, week_no) if i.kind == "block" and i.block_id not in posted],
    ) + import_placed(imports_for_week(repo, cycle, week_no), week_no)
    placed = Counter(int(r.ref) for r in rows if r.kind == "tactic" and r.ref.isdigit())
    return {
        "rows": clash_hints(placed_rows, others, repo.day_window),
        "placed": {str(t): placed[t] for t in tactics},
    }


# -- what actually happened -------------------------------------------------------


def recordable(cycle: Cycle, week_no: int, today: date) -> bool:
    """Once a week is over, what happened can be recorded until the cycle is closed."""
    return cycle.status != "closed" and 1 <= week_no <= REVIEW_WEEK and week_ended(cycle, week_no, today)


@dataclass
class WeekRecord:
    """A week's frozen plan beside what was recorded about it."""

    planned: bool
    plan: list[WeekItem]  # as saved; empty if the week was never planned
    actual: list[WeekItem] | None  # None: not recorded, so taken to have gone to plan

    @property
    def recorded(self) -> bool:
        return self.actual is not None

    @property
    def changes(self) -> list[Change]:
        return compare(self.plan, self.actual) if self.planned else []

    @property
    def plan_by_id(self) -> dict[int, WeekItem]:
        return {i.id: i for i in comparable(self.plan)}


def week_record(repo: Repo, cycle: Cycle, week_no: int) -> WeekRecord:
    planned = week_no in repo.planned_weeks(cycle.id)
    recorded = week_no in repo.recorded_weeks(cycle.id)
    return WeekRecord(
        planned,
        repo.week_items(cycle.id, week_no) if planned else [],
        repo.week_items(cycle.id, week_no, "actual") if recorded else None,
    )


def record_note(planned: WeekItem | None, time: str) -> str:
    """The line under a row when recording: what was planned, and whether it happened."""
    if planned is None:
        return "Added: wasn't in the plan"
    return f"Planned {when(planned)}" + ("" if time.strip() else " · didn't happen")


def actual_form(repo: Repo, cycle: Cycle, week_no: int, record: WeekRecord) -> PlanForm:
    """The recording form: what happened (as recorded, or else as planned), with what didn't happen blank."""
    if record.actual is not None:
        happened = record.actual
    elif record.planned:
        happened = [replace(i, layer="actual", plan_item_id=i.id) for i in comparable(record.plan)]
    else:
        happened = template_items(cycle, week_no, repo.list_blocks(cycle.id))
    plan_by_id = record.plan_by_id
    done = {i.plan_item_id for i in happened}
    missed = [p for p in plan_by_id.values() if p.id not in done and p.kind == "block"]

    def row(item: WeekItem, planned: WeekItem | None, happened_: bool) -> dict:
        time = hhmm(item.start_min) if happened_ else ""
        out = {
            "day": str(item.weekday),
            "time": time,
            "minutes": str(item.duration_min),
            "plan": str(planned.id) if planned else "",
            "note": record_note(planned, time),
        }
        if item.kind == "block":
            origin = (
                (item.orig_weekday, item.orig_start_min)
                if item.orig_weekday is not None
                else (item.weekday, item.start_min)
            )
            out |= {"origin": f"{origin[0]}:{origin[1]}", "usual": usual_label(origin)}
        return out

    happened_blocks = [i for i in happened if i.kind == "block"]
    entries = [(i, plan_by_id.get(i.plan_item_id), True) for i in happened_blocks] + [(p, p, False) for p in missed]
    entries.sort(key=lambda e: ((e[1] or e[0]).weekday, (e[1] or e[0]).start_min))

    goals = {g.id: g.title for g in repo.list_goals(cycle.id)}
    tactics = []
    for t in repo.list_tactics(cycle.id):
        mine = sorted((i for i in happened if i.kind == "tactic" and i.tactic_id == t.id), key=by_time)
        planned_cells = planned_views(plan_by_id.values(), t.id)
        if mine or planned_cells or (week_no <= EXECUTION_WEEKS and t.active_in(week_no)):
            sessions = [(i.weekday, session_view(i, str(i.plan_item_id or ""))) for i in mine]
            cells = build_cells(cycle, week_no, sessions, planned_cells)
            tactics.append(TacticGroup(t, goals.get(t.goal_id, ""), cells))
    blocks = []
    for b in repo.list_blocks(cycle.id):
        rows = [row(i, p, ok) for i, p, ok in entries if i.kind == "block" and i.block_id == b.id]
        if rows:
            blocks.append(BlockGroup(b, rows))
    return PlanForm(tactics, blocks, [])


def planned_views(plan_items, tactic_id: int | None) -> list[tuple[int, dict]]:
    """A tactic's planned sessions as (weekday, view with "id") pairs, for the recording grid."""
    mine = sorted((p for p in plan_items if p.kind == "tactic" and p.tactic_id == tactic_id), key=by_time)
    return [(p.weekday, session_view(p) | {"id": str(p.id)}) for p in mine]


def save_actual(repo: Repo, cycle: Cycle, week_no: int, today: date, items: list[WeekItem]) -> bool:
    """Record what happened. Returns False if it matches the plan, which is then simply "went to plan"."""
    if not recordable(cycle, week_no, today):
        raise FormError(f"What happened in week {week_no} can't be recorded now.")
    record = week_record(repo, cycle, week_no)
    if record.planned and all(c.status == "kept" for c in compare(record.plan, items)):
        repo.clear_actual(cycle.id, week_no)
        return False
    repo.replace_week_items(cycle.id, week_no, "actual", items)
    return True


def rows_of(form: PlanForm) -> list[FormRow]:
    """The rows a rendered form posts, in page order: tactics, blocks, then events."""
    rows = [
        FormRow("tactic", str(g.tactic.id), "", "", str(c.weekday), r["time"], r["minutes"], r.get("plan", ""))
        for g in form.tactics
        for c in g.days
        for r in c.sessions
    ]
    rows += [
        FormRow("block", str(g.block.id), r["origin"], "", r["day"], r["time"], r["minutes"], r.get("plan", ""))
        for g in form.blocks
        for r in g.rows
    ]
    return rows + [FormRow("event", "", "", r["title"], r["day"], r["time"], r["minutes"]) for r in form.events]


def _item_key(items: list[WeekItem]) -> list[tuple]:
    def n(value: int | None) -> int:
        return -1 if value is None else value

    return sorted(
        (i.kind, n(i.tactic_id), n(i.block_id), n(i.plan_item_id), i.weekday, n(i.start_min), n(i.duration_min))
        for i in items
    )


@dataclass
class ReviewRecord:
    """The weekly review's "What happened" section."""

    record: WeekRecord
    summary: str
    hints: list[str]
    form: PlanForm | None  # None while the week is on, or once the cycle is closed


def review_record(repo: Repo, cycle: Cycle, week_no: int, today: date) -> ReviewRecord | None:
    """What happened against the plan, for the review, or None for a week with nothing to show."""
    record = week_record(repo, cycle, week_no)
    if not (record.planned or record.recorded or active_tactics(repo, cycle, week_no)):
        return None
    if not week_ended(cycle, week_no, today):
        text = "Record what happened once the week is over."
    elif record.planned:
        text = (
            f"Against the plan: {summary(record.changes)}."
            if record.recorded
            else "Not recorded yet, so taken as planned."
        )
    else:
        text = "Recorded, but the week wasn't planned." if record.recorded else "This week wasn't planned."
    hints = []
    if week_ended(cycle, week_no, today):
        first, last = week_range(cycle.start_date, week_no)
        titles = {t.id: t.title for t in repo.list_tactics(cycle.id)}
        hints = log_hints(record.changes, repo.logged_between(cycle.id, first, last), titles)
    form = actual_form(repo, cycle, week_no, record) if recordable(cycle, week_no, today) else None
    return ReviewRecord(record, text, hints, form)


def parse_review_record(repo: Repo, cycle: Cycle, week_no: int, rows: list[FormRow]) -> list[WeekItem] | None:
    """What happened, as posted with the review, or None if the section was left as it was shown.

    Leaving it alone mustn't record anything: for a week that wasn't planned, the shown rows
    would otherwise become a record of their own.
    """
    record = week_record(repo, cycle, week_no)
    tactics, blocks = repo.list_tactics(cycle.id), repo.list_blocks(cycle.id)
    posted = parse_plan_rows(cycle, week_no, tactics, blocks, rows, record.plan_by_id)
    shown = rows_of(actual_form(repo, cycle, week_no, record))
    initial = parse_plan_rows(cycle, week_no, tactics, blocks, shown, record.plan_by_id)
    return None if _item_key(posted) == _item_key(initial) else posted


@dataclass
class WeekView:
    """What the Week page shows: the plan, or after the week, what happened against it."""

    sessions: list[Session]
    ghosts: list[tuple[Session, str]] = field(default_factory=list)  # where moved or missed sessions were planned
    badges: dict[str, str] = field(default_factory=dict)  # session uid → "moved from Tue 07:00" / "added"
    summary: str | None = None
    planned: bool = False
    recorded: bool = False
    anytime: list[Anytime] = field(default_factory=list)  # tactic sessions with a day but no set time


def week_view(repo: Repo, cycle: Cycle, week_no: int, today: date) -> WeekView:
    planned = week_no in repo.planned_weeks(cycle.id)
    record = week_record(repo, cycle, week_no) if week_ended(cycle, week_no, today) else None
    if record is None or record.actual is None:
        return WeekView(
            week_sessions(repo, cycle, week_no), planned=planned, anytime=week_anytime(repo, cycle, week_no)
        )
    contexts = tactic_contexts(repo, cycle)
    kinds = {b.id: b.kind for b in repo.list_blocks(cycle.id)}
    events = [i for i in record.plan if i.kind == "event"]
    sessions = sessions_from_items(cycle, week_no, record.actual + events, contexts, kinds)
    view = WeekView(sessions, planned=planned, recorded=True)
    view.anytime = recorded_anytime(cycle, week_no, record, contexts)
    if not planned:
        return view
    by_item = {s.item_id: s for s in sessions}
    for change in record.changes:
        if change.status in ("moved", "added") and change.actual.id in by_item:
            view.badges[by_item[change.actual.id].uid] = change.label
        if change.status in ("moved", "dropped") and change.plan.timed:
            [ghost] = sessions_from_items(cycle, week_no, [change.plan], contexts, kinds)
            view.ghosts.append((ghost, change.ghost_label))
    view.summary = summary(record.changes)
    return view


def recorded_anytime(cycle: Cycle, week_no: int, record: WeekRecord, contexts: Mapping[int, str]) -> list[Anytime]:
    """What happened with no set time, each day noting how many of the tactic's sessions happened
    there against how many were planned, when those differ (a day planned but missed shows too)."""
    actual, plan = record.actual or [], comparable(record.plan)
    done = Counter((i.tactic_id, i.weekday) for i in actual if i.kind == "tactic")
    meant = Counter((i.tactic_id, i.weekday) for i in plan if i.kind == "tactic")
    untimed = {(a.tactic_id, a.day.weekday()): a for a in anytime_from_items(cycle, week_no, actual, contexts)}
    missed = {(a.tactic_id, a.day.weekday()): a for a in anytime_from_items(cycle, week_no, plan, contexts)}
    found = []
    for key in untimed.keys() | missed.keys():
        if key not in untimed and done[key]:
            continue  # planned with no time, and it happened at a time: the timeline shows it
        base = untimed.get(key) or replace(missed[key], count=0)
        differs = done[key] != meant[key]
        found.append(replace(base, happened=done[key] if differs else None, planned=meant[key] if differs else None))
    return sorted(found, key=lambda a: (a.day, a.title))


# -- keeping open plans in step with the 12-week plan -----------------------------


def after_block_saved(repo: Repo, cycle: Cycle, block_id: int, today: date) -> None:
    repo.resync_block_rows(repo.get_block(block_id), open_planned_weeks(repo, cycle, today))


def before_block_deleted(repo: Repo, cycle: Cycle, block_id: int, today: date) -> None:
    """Ended weeks keep the occurrence (titled as it was); open plans drop it."""
    repo.prune_plan_rows(cycle.id, "block", block_id, open_planned_weeks(repo, cycle, today))


def after_tactic_saved(repo: Repo, cycle: Cycle, tactic: Tactic, today: date) -> None:
    """A tactic moved out of a planned week (its week range changed) leaves that week's plan."""
    weeks = [w for w in open_planned_weeks(repo, cycle, today) if not tactic.active_in(w)]
    repo.prune_plan_rows(cycle.id, "tactic", tactic.id, weeks)


def before_tactics_deleted(repo: Repo, cycle: Cycle, tactic_ids: list[int], today: date) -> None:
    weeks = open_planned_weeks(repo, cycle, today)
    for tactic_id in tactic_ids:
        repo.prune_plan_rows(cycle.id, "tactic", tactic_id, weeks)


# -- reminders and flow -----------------------------------------------------------


def unplanned_reminders(repo: Repo, cycle: Cycle, today: date) -> list[int]:
    """Weeks to nudge about: this week if unplanned, and next week from the weekly review day.

    Only weeks with tactics to place count. Without a review block, next week's nudge starts
    on the week's 6th day.
    """
    if cycle.status == "closed":
        return []
    planned = repo.planned_weeks(cycle.id)
    tactics = repo.list_tactics(cycle.id)

    def needs(week_no: int) -> bool:
        return week_no <= EXECUTION_WEEKS and week_no not in planned and any(t.active_in(week_no) for t in tactics)

    offset = planning_offset(cycle, repo.list_blocks(cycle.id))
    lead = DEFAULT_PLANNING_OFFSET if offset is None else offset
    pos = current_week(cycle.start_date, today)
    if pos.not_started:
        days_into_week_before = 7 - (cycle.start_date - today).days
        return [1] if days_into_week_before >= lead and needs(1) else []
    if pos.week_no is None:
        return []
    weeks = [pos.week_no] if needs(pos.week_no) else []
    first, _last = week_range(cycle.start_date, pos.week_no)
    if (today - first).days >= lead and needs(pos.week_no + 1):
        weeks.append(pos.week_no + 1)
    return weeks


def next_week_to_plan(repo: Repo, cycle: Cycle, today: date) -> int | None:
    """The first of this week and next with tactics but no plan yet, for after a weekly review."""
    planned = repo.planned_weeks(cycle.id)
    return next(
        (
            w
            for w in plannable_weeks(cycle, today)
            if w not in planned and not week_ended(cycle, w, today) and active_tactics(repo, cycle, w)
        ),
        None,
    )
