"""Week-by-week planning: which weeks can be planned, what a week holds, and saving a week's plan.

Tactics have no fixed times. Each week you place its sessions around whatever else is going on,
the book's "plan your week" step. Blocks stay weekly templates: a week that hasn't been planned
shows them as they are, and saving a week's plan stores its block occurrences alongside the
tactic sessions, so a past week keeps the plan it had. No Flask imports here.
"""

from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date

from twy.domain.conflicts import Placed, placed_for_week
from twy.domain.dates import EXECUTION_WEEKS, REVIEW_WEEK, current_week, week_range
from twy.domain.schedule import Session, planning_offset, sessions_from_items, template_items
from twy.models import Cycle, Tactic, WeekItem, hhmm
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


@dataclass
class TacticGroup:
    """One tactic on the planning page: its rows in the slot editor."""

    tactic: Tactic
    goal_title: str
    rows: list[dict] = field(default_factory=list)  # {"day", "time", "minutes"} as strings

    @property
    def placed(self) -> int:
        return sum(1 for r in self.rows if r["time"].strip())


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


def posted_groups(
    repo: Repo, cycle: Cycle, week_no: int, refs: list[str], days: list[str], times: list[str], minutes: list[str]
) -> list[TacticGroup]:
    """The planning form as it was posted, to show it again with an error."""
    groups = {g.tactic.id: g for g in tactic_groups(repo, cycle, week_no, [])}
    for group in groups.values():
        group.rows = []
    for ref, day, time, length in zip(refs, days, times, minutes, strict=False):
        if ref.isdigit() and int(ref) in groups:
            groups[int(ref)].rows.append({"day": day, "time": time, "minutes": length})
    return list(groups.values())


def parse_week_rows(
    cycle: Cycle,
    week_no: int,
    tactics: list[Tactic],
    kinds: list[str],
    refs: list[str],
    days: list[str],
    times: list[str],
    minutes: list[str],
) -> list[WeekItem]:
    """Tactic sessions from the planning form's parallel lists. Rows without a time are skipped."""
    by_id = {t.id: t for t in tactics}
    nth: Counter = Counter()
    items: list[WeekItem] = []
    for kind, ref, day, time, length in zip(kinds, refs, days, times, minutes, strict=False):
        tactic = by_id.get(int(ref)) if kind == "tactic" and ref.isdigit() else None
        if tactic is None:
            raise FormError("A tactic on this page has changed. Reload the page and try again.")
        nth[tactic.id] += 1
        if not time.strip():
            continue
        try:
            slot = parse_slot_row(day, time, length)
        except FormError as e:
            raise FormError(f"{tactic.title}, time {nth[tactic.id]}: {e}") from None
        items.append(
            WeekItem(
                cycle.id,
                week_no,
                "tactic",
                slot.weekday,
                slot.start_min,
                slot.duration_min,
                tactic_id=tactic.id,
                title=tactic.title,
            )
        )
    return items


def save_week_plan(repo: Repo, cycle: Cycle, week_no: int, today: date, tactic_items: list[WeekItem]) -> None:
    """Save a week's tactic sessions, keeping its block occurrences as they stand, and mark it planned."""
    if week_no not in plannable_weeks(cycle, today) or week_ended(cycle, week_no, today):
        raise FormError(f"Week {week_no} can't be planned now.")
    others = [i for i in resolved_items(repo, cycle, week_no) if i.kind != "tactic"]
    repo.replace_week_items(cycle.id, week_no, "plan", tactic_items + others)


def copy_prev_items(repo: Repo, cycle: Cycle, week_no: int) -> list[WeekItem] | None:
    """Last week's tactic times for the tactics active this week, or None if last week wasn't planned."""
    if week_no <= 1 or week_no - 1 not in repo.planned_weeks(cycle.id):
        return None
    active = {t.id for t in active_tactics(repo, cycle, week_no)}
    return [
        replace(i, week_no=week_no, id=None)
        for i in repo.week_items(cycle.id, week_no - 1)
        if i.kind == "tactic" and i.tactic_id in active
    ]


def week_plan_check(
    repo: Repo, cycle: Cycle, week_no: int, refs: list[str], days: list[str], times: list[str], minutes: list[str]
) -> dict:
    """Live hints for the planning form: clashes per row, and how many sessions each tactic has placed."""
    tactics = {t.id: t for t in active_tactics(repo, cycle, week_no)}
    rows: list[Placed | None] = []
    for n, (ref, slot) in enumerate(zip(refs, form_slots(days, times, minutes), strict=False), start=1):
        tactic = tactics.get(int(ref)) if ref.isdigit() else None
        rows.append(
            None
            if slot is None or tactic is None
            else Placed(tactic.title, "tactic", tactic.id, (week_no,) * 2, slot, n)
        )
    others = placed_for_week(week_no, [i for i in resolved_items(repo, cycle, week_no) if i.kind != "tactic"])
    placed = Counter(str(p.owner_id) for p in rows if p is not None)
    return {"rows": clash_hints(rows, others, repo.day_window), "placed": {str(t): placed[str(t)] for t in tactics}}


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
