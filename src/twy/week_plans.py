"""Week-by-week planning: which weeks can be planned, what a week holds, and saving a week's plan.

Tactics have no fixed times. Each week you place its sessions around whatever else is going on,
the book's "plan your week" step. Blocks stay weekly templates: a week that hasn't been planned
shows them as they are, and saving a week's plan stores its block occurrences alongside the
tactic sessions, so a past week keeps the plan it had. A week's plan can also skip or move a
block for that week only, and hold one-off events. No Flask imports here.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from itertools import zip_longest

from twy.domain.conflicts import Placed, placed_for_week
from twy.domain.dates import EXECUTION_WEEKS, REVIEW_WEEK, current_week, week_range
from twy.domain.schedule import Session, planning_offset, sessions_from_items, template_items
from twy.models import WEEKDAY_SHORT, Block, Cycle, Tactic, WeekItem, hhmm
from twy.repo import Repo
from twy.services import FormError, clash_hints, form_slots, parse_slot_row

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


def week_sessions(repo: Repo, cycle: Cycle, week_no: int) -> list[Session]:
    """Every session in one cycle week, in time order."""
    goals = {g.id: g.title for g in repo.list_goals(cycle.id)}
    contexts = {t.id: goals.get(t.goal_id, "") for t in repo.list_tactics(cycle.id)}
    kinds = {b.id: b.kind for b in repo.list_blocks(cycle.id)}
    return sessions_from_items(cycle, week_no, resolved_items(repo, cycle, week_no), contexts, kinds)


def shortfall(tactics: list[Tactic], items: list[WeekItem]) -> list[tuple[Tactic, int]]:
    """Tactics with fewer sessions placed than their times per week, with how many are placed."""
    placed = Counter(i.tactic_id for i in items if i.kind == "tactic")
    return [(t, placed[t.id]) for t in tactics if placed[t.id] < t.times_per_week]


# -- the planning form ------------------------------------------------------------

# One row per session, as parallel lists: kind (tactic | block | event), ref (tactic or block id),
# origin ("weekday:start" of a block's weekly slot), title (events), then day, time and minutes.
FORM_FIELDS = ("slot_kind", "slot_ref", "slot_origin", "slot_title", "slot_day", "slot_time", "slot_minutes")
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
class TacticGroup:
    """One tactic on the planning page: its rows in the slot editor."""

    tactic: Tactic
    goal_title: str
    rows: list[dict] = field(default_factory=list)  # {"day", "time", "minutes"} as strings

    @property
    def placed(self) -> int:
        return sum(1 for r in self.rows if r["time"].strip())


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


def tactic_groups(repo: Repo, cycle: Cycle, week_no: int, items: list[WeekItem]) -> list[TacticGroup]:
    """Each active tactic with its placed times, plus blank rows up to its times per week."""
    goals = {g.id: g.title for g in repo.list_goals(cycle.id)}
    groups = []
    for t in active_tactics(repo, cycle, week_no):
        mine = [i for i in items if i.kind == "tactic" and i.tactic_id == t.id]
        rows = [{"day": str(i.weekday), "time": hhmm(i.start_min), "minutes": str(i.duration_min)} for i in mine]
        day = (mine[-1].weekday + 1) if mine else cycle.start_date.weekday()
        minutes = rows[-1]["minutes"] if rows else "60"
        rows += [
            {"day": str((day + n) % 7), "time": "", "minutes": minutes}
            for n in range(max(t.times_per_week - len(mine), 0))
        ]
        groups.append(TacticGroup(t, goals.get(t.goal_id, ""), rows))
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


def posted_form(repo: Repo, cycle: Cycle, week_no: int, rows: list[FormRow]) -> PlanForm:
    """The planning form as it was posted, to show it again with an error."""
    tactics = {g.tactic.id: g for g in tactic_groups(repo, cycle, week_no, [])}
    for group in tactics.values():
        group.rows = []
    blocks = {b.id: BlockGroup(b) for b in repo.list_blocks(cycle.id)}
    events = []
    for row in rows:
        ref = int(row.ref) if row.ref.isdigit() else None
        if row.kind == "tactic" and ref in tactics:
            tactics[ref].rows.append(row.view)
        elif row.kind == "block" and ref in blocks and (origin := parse_origin(row.origin)):
            blocks[ref].rows.append(row.view | {"origin": row.origin, "usual": usual_label(origin)})
        elif row.kind == "event":
            events.append(row.view)
    return PlanForm(list(tactics.values()), [b for b in blocks.values() if b.rows], events or [blank_event(cycle)])


def parse_plan_rows(
    cycle: Cycle, week_no: int, tactics: list[Tactic], blocks: list[Block], rows: list[FormRow]
) -> list[WeekItem]:
    """A week's items from the planning form.

    Tactic and event rows without a time are left out. A block row without a time is that
    occurrence skipped this week; one at another time, or for another length, is moved.
    """
    tactic_by_id = {t.id: t for t in tactics}
    block_by_id = {b.id: b for b in blocks}
    nth: Counter = Counter()
    items: list[WeekItem] = []

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
            nth[tactic.id] += 1
            if row.time.strip():
                slot = slot_for(row, f"{tactic.title}, time {nth[tactic.id]}")
                items.append(
                    WeekItem(
                        cycle.id,
                        week_no,
                        "tactic",
                        *slot_fields(slot),
                        tactic_id=tactic.id,
                        title=tactic.title,
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
            if not row.time.strip():
                items.append(replace(base, skipped=True, custom=True))
                continue
            slot = slot_for(row, f"{block.title} (usually {usual_label(origin)})")
            moved = slot_fields(slot) != (base.weekday, base.start_min, base.duration_min)
            items.append(
                replace(
                    base, weekday=slot.weekday, start_min=slot.start_min, duration_min=slot.duration_min, custom=moved
                )
            )
        elif row.kind == "event":
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
    )
    placed = Counter(p.owner_id for p in placed_rows if p is not None and p.owner == "tactic")
    return {
        "rows": clash_hints(placed_rows, others, repo.day_window),
        "placed": {str(t): placed[t] for t in tactics},
    }


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
