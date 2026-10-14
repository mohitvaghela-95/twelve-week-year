"""App rules kept out of the Flask routes: review rows, banners, form validation and
the Week 13 flow. No Flask imports here, so they stay easy to test.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date

from twy.domain.conflicts import (
    PairWarning,
    Placed,
    Weeks,
    check_rows,
    find_clashes,
    pair_warnings,
    placed_for_cycle,
    weeks_label,
)
from twy.domain.dates import (
    EXECUTION_WEEKS,
    WEEKDAY_NAMES,
    CyclePosition,
    align_to_week_start,
    current_week,
    cycle_end,
    last_finished_week,
    week_range,
)
from twy.domain.schedule import Session, sessions_for_week
from twy.domain.scoring import execution_score, goal_outcome
from twy.models import (
    BLOCK_KINDS,
    PROMPT_KINDS,
    Answer,
    Block,
    Cycle,
    CycleReview,
    DayReview,
    Goal,
    Prompt,
    Slot,
    Tactic,
    WeekEntry,
    WeekReview,
    hhmm,
)
from twy.repo import Repo

OUTCOMES = ("hit", "missed")


class FormError(ValueError):
    """User input that can't be saved; the message is shown as-is."""


# -- parsing ----------------------------------------------------------------------


def parse_float(text: str) -> float | None:
    text = text.strip()
    return float(text) if text else None


def parse_int(text: str, default: int = 0) -> int:
    text = text.strip()
    return int(text) if text else default


MAX_WEEKLY_ITEMS_RANGE = (1, 200)


def parse_max_weekly_items(text: str) -> int:
    """The heavy-week limit from a settings form."""
    low, high = MAX_WEEKLY_ITEMS_RANGE
    try:
        value = int(text.strip())
    except ValueError:
        raise FormError("Max items per week must be a whole number.") from None
    if not low <= value <= high:
        raise FormError(f"Max items per week must be between {low} and {high}.")
    return value


def parse_alert_minutes(text: str) -> int:
    """Calendar alert lead time from the settings form; 0 turns alerts off."""
    try:
        value = int(text.strip())
    except ValueError:
        raise FormError("Alert must be a whole number of minutes (0 for none).") from None
    if not 0 <= value <= 1440:
        raise FormError("Alert must be between 0 and 1440 minutes.")
    return value


def parse_time(text: str) -> int | None:
    """ "HH:MM" (or "HH:MM:SS" from some browsers) → minutes after midnight, else None."""
    parts = text.strip().split(":")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts) or len(parts[1]) != 2:
        return None
    h, m = int(parts[0]), int(parts[1])
    return h * 60 + m if h < 24 and m < 60 else None


def parse_slot_row(day: str, time: str, length: str) -> Slot:
    """One slot editor row. Error messages are lower-case so callers can prefix the row."""
    if not (day.isdigit() and 0 <= int(day) <= 6):
        raise FormError("pick a day.")
    start = parse_time(time)
    if start is None:
        raise FormError("use a time like 07:30.")
    if not length.strip().isdigit() or not 5 <= int(length) <= 720:
        raise FormError("length must be 5–720 minutes.")
    if start + int(length) > 24 * 60:
        raise FormError("must end by midnight.")
    return Slot(weekday=int(day), start_min=start, duration_min=int(length))


def parse_slots(days: list[str], times: list[str], minutes: list[str]) -> list[Slot]:
    """Weekly slots from the parallel day/time/minutes form lists. Rows without a time are skipped."""
    slots: list[Slot] = []
    for row, (day, time, length) in enumerate(zip(days, times, minutes, strict=False), start=1):
        if not time.strip():
            continue
        try:
            slots.append(parse_slot_row(day, time, length))
        except FormError as e:
            raise FormError(f"Slot {row}: {e}") from None
    return sorted(slots, key=lambda s: (s.weekday, s.start_min))


def parse_day_window(start_text: str, end_text: str) -> tuple[int, int]:
    """The "suggest times between" pair from the settings form."""
    start, end = parse_time(start_text), parse_time(end_text)
    if start is None or end is None:
        raise FormError("Use times like 06:00 for the suggestion hours.")
    if end - start < 30:
        raise FormError("Suggestion hours must start at least 30 minutes before they end.")
    return start, end


def parse_block_form(block: Block, fields: Mapping[str, str], slots: list[Slot]) -> Block:
    title = fields.get("title", "").strip()
    kind = fields.get("kind", "other")
    if not title:
        raise FormError("Block title is required.")
    if kind not in BLOCK_KINDS:
        raise FormError("Pick a kind of block.")
    if not slots:
        raise FormError("Add at least one time slot.")
    return replace(block, title=title, kind=kind, slots=slots)


def fmt_num(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:g}"


# -- cycles -----------------------------------------------------------------------


def snap_start(text: str, week_start: int) -> date | None:
    """Parse YYYY-MM-DD and move it forward to the next week-start day."""
    try:
        return align_to_week_start(date.fromisoformat(text.strip()), week_start)
    except ValueError:
        return None


def parse_cycle_form(name: str, start_text: str, week_start: int) -> tuple[str, date]:
    name = name.strip()
    start = snap_start(start_text, week_start)
    if not name:
        raise FormError("Name is required.")
    if start is None:
        raise FormError("Start date must be YYYY-MM-DD.")
    return name, start


def previous_cycle_id(repo: Repo) -> int | None:
    """The cycle a new one copies its plan from by default: the newest one."""
    cycles = repo.list_cycles()
    return cycles[0].id if cycles else None


def create_cycle(repo: Repo, name: str, start: date, copy_from: int | None = None) -> Cycle:
    cycle = repo.create_cycle(name, start, repo.week_start)
    if copy_from is not None:
        repo.copy_plan(copy_from, cycle.id)
    return cycle


@dataclass
class CycleStatus:
    """What the dashboard says about a cycle today."""

    position: CyclePosition
    pending: list[int]  # finished weeks not reviewed yet
    banner: str | None  # read_only | planning | pending | week13 | None
    where: str  # "Week 3 of 12  (12 Oct – 18 Oct)" etc.

    @property
    def show_outcomes(self) -> bool:
        return self.position.in_review_week or self.position.finished


def pending_weeks(repo: Repo, cycle: Cycle, today: date) -> list[int]:
    reviewed = repo.reviewed_weeks(cycle.id)
    return [w for w in range(1, last_finished_week(cycle.start_date, today) + 1) if w not in reviewed]


def cycle_status(repo: Repo, cycle: Cycle, today: date) -> CycleStatus:
    pos = current_week(cycle.start_date, today)
    pending = pending_weeks(repo, cycle, today)
    if cycle.status == "closed":
        banner = "read_only"
    elif cycle.status == "planning":
        banner = "planning"
    elif pending:
        banner = "pending"
    elif pos.in_review_week or pos.finished:
        banner = "week13"
    else:
        banner = None

    if pos.not_started:
        where = f"Starts {cycle.start_date:%a %d %b}"
    elif pos.finished:
        where = "Finished"
    elif pos.in_review_week:
        where = "Week 13 · review"
    else:
        first, last = week_range(cycle.start_date, pos.week_no)
        where = f"Week {pos.week_no} of {EXECUTION_WEEKS}  ({first:%d %b} – {last:%d %b})"
    return CycleStatus(pos, pending, banner, where)


def show_goal_outcomes(cycle: Cycle, status: CycleStatus) -> bool:
    return status.show_outcomes or cycle.status in ("review", "closed")


# -- goals and tactics ------------------------------------------------------------


def parse_goal_form(goal: Goal, fields: Mapping[str, str]) -> Goal:
    """Validate title/why/unit/baseline/target and return an updated copy of `goal`."""
    title = fields.get("title", "").strip()
    if not title:
        raise FormError("Goal title is required.")
    try:
        baseline, target = parse_float(fields.get("baseline", "")), parse_float(fields.get("target", ""))
    except ValueError:
        raise FormError("Baseline and target must be numbers.") from None
    if baseline is not None and target is None:
        raise FormError("Add a target, or clear the baseline.")
    return replace(
        goal,
        title=title,
        why=fields.get("why", "").strip(),
        unit=fields.get("unit", "").strip() or None,
        baseline=baseline,
        target=target,
    )


def parse_tactic_form(tactic: Tactic, fields: Mapping[str, str]) -> Tactic:
    """Validate title/times/start/end and return an updated copy of `tactic`."""
    title = fields.get("title", "").strip()
    if not title:
        raise FormError("Tactic title is required.")
    try:
        times = parse_int(fields.get("times", ""), 1)
        start = parse_int(fields.get("start", ""), 1)
        end = parse_int(fields.get("end", ""), EXECUTION_WEEKS)
    except ValueError:
        raise FormError("Times and weeks must be whole numbers.") from None
    if times < 1:
        raise FormError("Times per week must be at least 1.")
    if not (1 <= start <= end <= EXECUTION_WEEKS):
        raise FormError(f"Weeks must satisfy 1 ≤ from ≤ to ≤ {EXECUTION_WEEKS}.")
    return replace(tactic, title=title, times_per_week=times, start_week=start, end_week=end)


# -- schedule ---------------------------------------------------------------------


def week_sessions(repo: Repo, cycle: Cycle, week_no: int) -> list[Session]:
    """Every tactic and block session in one cycle week, in time order."""
    goals = {g.id: g.title for g in repo.list_goals(cycle.id)}
    return sessions_for_week(
        cycle, week_no, repo.list_tactics(cycle.id), repo.tactic_slots(cycle.id), goals, repo.list_blocks(cycle.id)
    )


def plan_clashes(repo: Repo, cycle: Cycle) -> list[PairWarning]:
    """Every pair of saved items whose calendar times overlap in a shared week."""
    placed = placed_for_cycle(repo.list_tactics(cycle.id), repo.tactic_slots(cycle.id), repo.list_blocks(cycle.id))
    return pair_warnings(find_clashes(placed))


def schedule_check(
    repo: Repo,
    cycle: Cycle,
    owner: str,
    owner_id: int | None,
    weeks: Weeks,
    days: list[str],
    times: list[str],
    minutes: list[str],
) -> list[dict | None]:
    """Live clash hints for the slot editor, one entry per posted row (None: blank, invalid or clear)."""
    others = placed_for_cycle(
        repo.list_tactics(cycle.id), repo.tactic_slots(cycle.id), repo.list_blocks(cycle.id), skip=(owner, owner_id)
    )
    rows: list[Placed] = []
    index: list[int | None] = []  # posted row → position in `rows`
    for n, (day, time, length) in enumerate(zip(days, times, minutes, strict=False), start=1):
        try:
            slot = parse_slot_row(day, time, length) if time.strip() else None
        except FormError:
            slot = None
        index.append(None if slot is None else len(rows))
        if slot is not None:
            rows.append(Placed(f"slot {n}", owner, owner_id, weeks, slot, row=n))

    checks = check_rows(rows, others, repo.day_window)
    results: list[dict | None] = []
    for i in index:
        if i is None or not checks[i].clashes:
            results.append(None)
            continue
        row, check = rows[i], checks[i]
        notes = []
        for other, shared in check.clashes:
            note = f"Overlaps {other.title} · {other.slot.label}"
            notes.append(note if other.row is not None else f"{note} · {weeks_label(shared)}")
        results.append(
            {
                "clashes": notes,
                "earlier": None if check.earlier is None else hhmm(check.earlier),
                "later": None if check.later is None else hhmm(check.later),
                "no_gap": None
                if check.earlier is not None or check.later is not None
                else f"No free {row.slot.duration_min}-min gap on {WEEKDAY_NAMES[row.slot.weekday]}.",
            }
        )
    return results


# -- weekly review ----------------------------------------------------------------


@dataclass
class ReviewRow:
    tactic_id: int
    goal_title: str
    title: str
    planned: int
    done: int
    logged: int = 0  # from the Today page's daily logs


def reviewable_weeks(cycle: Cycle, today: date) -> list[int]:
    """Weeks that can be scored so far: 1 up to the current week (max 12)."""
    pos = current_week(cycle.start_date, today)
    if pos.not_started:
        return []
    newest = EXECUTION_WEEKS if pos.week_no is None or pos.week_no > EXECUTION_WEEKS else pos.week_no
    return list(range(1, newest + 1))


def default_review_week(repo: Repo, cycle: Cycle, today: date) -> int | None:
    """The oldest finished week still to review, else the latest finished week."""
    weeks = reviewable_weeks(cycle, today)
    if not weeks:
        return None
    pending = pending_weeks(repo, cycle, today)
    default = pending[0] if pending else max(last_finished_week(cycle.start_date, today), 1)
    return min(default, weeks[-1])


def prefill_done(planned: int, logged: int, snapshot: WeekEntry | None) -> int:
    """The done count a tactic's row starts with on the weekly scorecard.

    `logged` is the total from the Today page's daily logs for that week (it can exceed
    `planned`). `snapshot` is the entry saved by an earlier review of the week, if any.
    """
    # TODO(human): decide how daily logs pre-fill the weekly scorecard.
    return snapshot.done if snapshot else 0


def review_rows(repo: Repo, cycle: Cycle, week_no: int, existing: WeekReview | None = None) -> list[ReviewRow]:
    """Tactics to score for a week, with done counts pre-filled by `prefill_done`.

    A reviewed week shows its snapshot (tactics added later are left out); an
    unreviewed week shows the tactics active in it now.
    """
    if existing is None:
        existing = repo.get_week_review(cycle.id, week_no)
    snapshot = {e.tactic_id: e for e in existing.entries} if existing else {}
    logged = repo.logged_between(cycle.id, *week_range(cycle.start_date, week_no))
    goals = {g.id: g for g in repo.list_goals(cycle.id)}
    rows = []
    for t in repo.list_tactics(cycle.id):
        entry = snapshot.get(t.id)
        if entry is not None:
            planned = entry.planned
        elif t.active_in(week_no) and existing is None:
            planned = t.times_per_week
        else:
            continue
        count = logged.get(t.id, 0)
        rows.append(
            ReviewRow(t.id, goals[t.goal_id].title, t.title, planned, prefill_done(planned, count, entry), count)
        )
    return rows


def week_entries(cycle_id: int, week_no: int, rows: list[ReviewRow], done: Mapping[int, int]) -> list[WeekEntry]:
    """Scorecard entries with done clamped to 0..planned."""
    return [
        WeekEntry(cycle_id, week_no, r.tactic_id, r.planned, max(0, min(done.get(r.tactic_id, 0), r.planned)))
        for r in rows
    ]


def save_week(
    repo: Repo,
    cycle: Cycle,
    week_no: int,
    entries: list[WeekEntry],
    answers: list[Answer],
    progress: dict[int, float | None],
) -> float | None:
    """Save a weekly review; the first review moves a planning cycle to active. Returns the score."""
    repo.save_week_review(WeekReview(cycle.id, week_no, entries=entries, answers=answers), progress)
    if cycle.status == "planning":
        repo.set_cycle_status(cycle.id, "active")
    return execution_score(entries)


# -- reflections ------------------------------------------------------------------


@dataclass
class ReflectionItem:
    """One question on a daily or weekly review form."""

    key: str  # form field name
    prompt_id: int | None
    label: str
    kind: str
    value: str = ""


def reflection_items(prompts: list[Prompt], answers: list[Answer]) -> list[ReflectionItem]:
    """Questions to show for one period, in the current prompt order.

    An answered prompt keeps the wording it was answered under (like the weekly
    snapshot). Answers to prompts since deleted or hidden come last, so nothing saved is lost.
    """
    saved = {a.prompt_id: a for a in answers if a.prompt_id is not None}
    items = []
    for p in prompts:
        a = saved.pop(p.id, None)
        items.append(
            ReflectionItem(f"p-{p.id}", p.id, a.label, a.kind, a.value)
            if a
            else ReflectionItem(f"p-{p.id}", p.id, p.label, p.kind)
        )
    items += [
        ReflectionItem(f"p-{a.prompt_id}" if a.prompt_id else f"a-{i}", a.prompt_id, a.label, a.kind, a.value)
        for i, a in enumerate(answers)
        if a.prompt_id is None or a.prompt_id in saved
    ]
    return items


def parse_answers(items: list[ReflectionItem], form: Mapping[str, str]) -> list[Answer]:
    """Read posted answers into `items` (so the form can be re-shown) and validate them.

    Blank answers are skipped. Ratings must be 1–5 and yes/no must be "yes" or "no".
    """
    answers, error = [], None
    for item in items:
        item.value = form.get(item.key, "").strip()
        if not item.value:
            continue
        if item.kind == "rating" and item.value not in ("1", "2", "3", "4", "5"):
            error = error or f"“{item.label}” must be a rating from 1 to 5."
        elif item.kind == "yesno" and item.value not in ("yes", "no"):
            error = error or f"“{item.label}” must be yes or no."
        answers.append(Answer(item.prompt_id, item.label, item.kind, item.value))
    if error:
        raise FormError(error)
    return answers


def parse_prompt_form(prompt: Prompt, fields: Mapping[str, str]) -> Prompt:
    label = fields.get("label", "").strip()
    if not label:
        raise FormError("A prompt needs some text.")
    kind = fields.get("kind", prompt.kind)
    if kind not in PROMPT_KINDS:
        raise FormError("Unknown prompt type.")
    return replace(prompt, label=label, kind=kind)


# -- today ------------------------------------------------------------------------


@dataclass
class TodayRow:
    tactic_id: int
    goal_title: str
    title: str
    planned: int  # times per week
    today: int  # logged on this day
    week: int  # logged over the whole week


def loggable(cycle: Cycle, day: date, today: date) -> bool:
    """Days you can log and reflect on: in weeks 1–12 and not in the future."""
    return cycle.start_date <= day <= min(today, cycle_end(cycle.start_date))


def week_of(cycle: Cycle, day: date) -> int:
    return (day - cycle.start_date).days // 7 + 1


def today_rows(repo: Repo, cycle: Cycle, day: date) -> list[TodayRow]:
    """Every tactic active in `day`'s week, with what has been logged so far."""
    week_no = week_of(cycle, day)
    on_day = repo.logged_between(cycle.id, day, day)
    in_week = repo.logged_between(cycle.id, *week_range(cycle.start_date, week_no))
    goals = {g.id: g.title for g in repo.list_goals(cycle.id)}
    return [
        TodayRow(t.id, goals[t.goal_id], t.title, t.times_per_week, on_day.get(t.id, 0), in_week.get(t.id, 0))
        for t in repo.list_tactics(cycle.id)
        if t.active_in(week_no)
    ]


def week_progress(rows: list[TodayRow]) -> float | None:
    """Share of the week's planned items logged so far (each tactic capped at its plan)."""
    return execution_score(WeekEntry(0, 0, r.tactic_id, r.planned, r.week) for r in rows)


def save_day(repo: Repo, cycle: Cycle, day: date, answers: list[Answer]) -> None:
    repo.save_day_review(DayReview(cycle.id, day, answers=answers))


# -- week 13 ----------------------------------------------------------------------


def save_week13(
    repo: Repo,
    cycle: Cycle,
    finals: Mapping[int, str],
    manual: Mapping[int, str | None],
    lessons: str,
    celebration: str,
) -> None:
    """Save final values / manual outcomes and reflections, and mark the cycle as in review.

    `finals` holds the typed final value per measurable goal, `manual` the hit/missed
    choice for the rest. Nothing is written if any final value is invalid.
    """
    goals = repo.list_goals(cycle.id)
    for g in goals:
        if g.measurable:
            try:
                g.final_value = parse_float(finals.get(g.id, ""))
            except ValueError:
                raise FormError(f"Final value for “{g.title}” must be a number.") from None
            g.outcome = goal_outcome(g)
        else:
            value = manual.get(g.id)
            g.outcome = value if value in OUTCOMES else None
    for g in goals:
        repo.save_goal(g)
    repo.save_cycle_review(CycleReview(cycle.id, lessons=lessons.strip(), celebration=celebration.strip()))
    if cycle.status != "closed":
        repo.set_cycle_status(cycle.id, "review")


def close_cycle(repo: Repo, cycle: Cycle) -> None:
    repo.set_cycle_status(cycle.id, "closed")
