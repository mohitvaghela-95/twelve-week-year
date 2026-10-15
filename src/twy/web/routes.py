"""All web pages. Cycle pages are addressed by id (/c/<id>/…) so every URL is bookmarkable."""

import hmac
import tempfile
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from pathlib import Path

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from twy.calendars import MAX_LINKS
from twy.db import backup_to, connect
from twy.domain.compare import summary as compare_summary
from twy.domain.dates import (
    EXECUTION_WEEKS,
    REVIEW_WEEK,
    WEEKDAY_NAMES,
    align_to_week_start,
    current_week,
    cycle_end,
    week_range,
)
from twy.domain.guardrails import heavy_weeks, plan_warnings
from twy.domain.schedule import feed_weeks, lanes, overlaps, planning_offset
from twy.domain.scoring import cycle_average, execution_score, format_score, goal_outcome, planned_items, score_band
from twy.models import (
    BLOCK_KINDS,
    PROMPT_KINDS,
    PROMPT_SCOPES,
    WEEKDAY_SHORT,
    Block,
    Cycle,
    Goal,
    Prompt,
    Slot,
    Tactic,
    hhmm,
)
from twy.repo import Repo
from twy.services import (
    RESET_WORD,
    FormError,
    close_cycle,
    create_cycle,
    cycle_status,
    default_review_week,
    edit_cycle,
    factory_reset,
    fmt_num,
    loggable,
    parse_alert_minutes,
    parse_answers,
    parse_block_form,
    parse_cycle_form,
    parse_day_window,
    parse_float,
    parse_goal_form,
    parse_import_urls,
    parse_int,
    parse_max_weekly_items,
    parse_prompt_form,
    parse_slots,
    parse_tactic_form,
    pending_weeks,
    plan_clashes,
    previous_cycle_id,
    reflection_items,
    review_rows,
    reviewable_weeks,
    save_day,
    save_week,
    save_week13,
    schedule_check,
    show_goal_outcomes,
    today_rows,
    week_entries,
    week_of,
    week_progress,
)
from twy.web.ics import render_calendar
from twy.web.svg import score_chart_svg
from twy.week_plans import (
    FORM_FIELDS,
    active_tactics,
    actual_form,
    after_block_saved,
    after_tactic_saved,
    before_block_deleted,
    before_tactics_deleted,
    copy_prev_items,
    form_rows,
    imports_for_week,
    next_week_to_plan,
    parse_plan_rows,
    parse_review_record,
    plan_form,
    plannable_weeks,
    planned_on,
    posted_form,
    recordable,
    resolved_items,
    review_record,
    save_actual,
    save_week_plan,
    shortfall,
    unplanned_reminders,
    week_anytime,
    week_days,
    week_ended,
    week_plan_check,
    week_record,
    week_sessions,
    week_view,
)

bp = Blueprint("web", __name__)

GOAL_FIELDS = ("title", "why", "unit", "baseline", "target")
TACTIC_FIELDS = ("title", "times", "start", "end")


# -- helpers ----------------------------------------------------------------------


def repo() -> Repo:
    """One connection per request, closed in teardown (see twy.web.create_app)."""
    if "repo" not in g:
        g.repo = Repo(connect(current_app.config["TWY_DB"]))
    return g.repo


def today() -> date:
    return current_app.config["TWY_TODAY"] or date.today()


def load_cycle(cycle_id: int) -> Cycle:
    try:
        return repo().get_cycle(cycle_id)
    except KeyError:
        abort(404)


def writable_cycle(cycle_id: int) -> Cycle:
    cycle = load_cycle(cycle_id)
    if cycle.status == "closed":
        abort(403, "This cycle is closed and read-only.")
    return cycle


def load_goal(cycle: Cycle, goal_id: int) -> Goal:
    try:
        goal = repo().get_goal(goal_id)
    except KeyError:
        abort(404)
    if goal.cycle_id != cycle.id:
        abort(404)
    return goal


def load_tactic(cycle: Cycle, tactic_id: int) -> tuple[Tactic, Goal]:
    try:
        tactic = repo().get_tactic(tactic_id)
    except KeyError:
        abort(404)
    return tactic, load_goal(cycle, tactic.goal_id)


def load_block(cycle: Cycle, block_id: int) -> Block:
    try:
        block = repo().get_block(block_id)
    except KeyError:
        abort(404)
    if block.cycle_id != cycle.id:
        abort(404)
    return block


def slot_rows(slots: list[Slot]) -> list[dict]:
    """Form rows for the slot editor: the saved slots plus one blank row to add another."""
    rows = [{"day": str(s.weekday), "time": hhmm(s.start_min), "minutes": str(s.duration_min)} for s in slots]
    return rows + [{"day": str(repo().week_start), "time": "", "minutes": "60"}]


def posted_slot_rows() -> list[dict]:
    days, times, minutes = (request.form.getlist(k) for k in ("slot_day", "slot_time", "slot_minutes"))
    return [{"day": d, "time": t, "minutes": m} for d, t, m in zip(days, times, minutes, strict=False)]


def posted_slots() -> list[Slot]:
    f = request.form
    return parse_slots(f.getlist("slot_day"), f.getlist("slot_time"), f.getlist("slot_minutes"))


def to_plan(cycle: Cycle, anchor: str | None = None):
    return redirect(url_for(".plan", cycle_id=cycle.id, _anchor=anchor))


# What a session on the Week page, Today or a week's plan is, as people read it.
SESSION_KINDS = {**BLOCK_KINDS, "event": "Event"}

# Cycle statuses as people read them (the stored ones are short identifiers).
STATUS_LABELS = {"planning": "Planning", "active": "In progress", "review": "Week 13 review", "closed": "Closed"}


def move_delta() -> int:
    return -1 if request.form.get("delta") == "-1" else 1


def moved(cycle: Cycle, anchor: str):
    """After a reorder: JSON for the Plan page's in-place move, else back to the row."""
    if request.accept_mimetypes.best == "application/json":
        return jsonify(ok=True)
    return to_plan(cycle, anchor)


@bp.app_context_processor
def nav_context() -> dict:
    """The cycle the tab bar links to: the one on screen, else the current one."""
    cycle_id = (request.view_args or {}).get("cycle_id")
    if cycle_id is None and request.endpoint not in ("auth.login", "web.healthz", "static"):
        current = repo().current_cycle()
        cycle_id = current.id if current else None
    return {
        "nav_cycle_id": cycle_id,
        "EXECUTION_WEEKS": EXECUTION_WEEKS,
        "WEEKDAY_SHORT": WEEKDAY_SHORT,
        "STATUS_LABELS": STATUS_LABELS,
    }


@bp.app_errorhandler(400)
@bp.app_errorhandler(403)
@bp.app_errorhandler(404)
def http_error(error):
    return render_template("error.html", error=error), error.code


# -- home, onboarding, cycles -----------------------------------------------------


@bp.get("/healthz")
def healthz():
    repo().conn.execute("SELECT 1")
    return "ok"


@bp.get("/")
def index():
    r = repo()
    if not r.is_configured:
        return redirect(url_for(".setup"))
    cycle = r.current_cycle()
    if cycle is not None:
        return redirect(url_for(".dashboard", cycle_id=cycle.id))
    return render_template("no_cycle.html", tab="home", has_cycles=bool(r.list_cycles()))


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    r = repo()
    if r.is_configured:
        return redirect(url_for(".index"))
    if request.method == "POST":
        week_start = request.form.get("week_start", type=int)
        if week_start is None or not 0 <= week_start <= 6:
            abort(400)
        r.set_setting("week_start", str(week_start))
        for horizon in ("long_term", "three_year"):
            r.set_vision(horizon, request.form.get(horizon, ""))
        flash("Welcome! Now create your first 12-week cycle.")
        return redirect(url_for(".new_cycle"))
    return render_template("setup.html", weekdays=WEEKDAY_NAMES)


@bp.route("/cycles/new", methods=["GET", "POST"])
def new_cycle():
    r = repo()
    if r.current_cycle() is not None:
        flash("Finish the current cycle first (Week 13 → Close cycle).", "warn")
        return redirect(url_for(".index"))
    copy_from = request.values.get("copy_from", type=int) or previous_cycle_id(r)
    copy_name = None
    if copy_from is not None:
        try:
            copy_name = r.get_cycle(copy_from).name
        except KeyError:
            copy_from = None
    week_start = r.week_start
    next_week = align_to_week_start(today() + timedelta(days=1), week_start)
    this_week = next_week - timedelta(days=7)
    # Early in the week, starting now loses little; from Thursday on, a fresh start next week is better.
    default_start = this_week if (today() - this_week).days <= 2 else next_week
    form = {"name": f"Cycle from {default_start:%d %b %Y}", "start": default_start.isoformat(), "copy": True}
    error = None
    if request.method == "POST":
        form = {k: request.form.get(k, "") for k in ("name", "start")} | {"copy": "copy" in request.form}
        try:
            name, start = parse_cycle_form(form["name"], form["start"], week_start)
        except FormError as e:
            error = str(e)
        else:
            cycle = create_cycle(r, name, start, copy_from if form["copy"] else None)
            flash(f"Created “{name}”. Add goals and tactics, then start the cycle.")
            return to_plan(cycle)
    return render_template(
        "cycle_new.html",
        form=form,
        error=error,
        copy_from=copy_from,
        copy_name=copy_name,
        week_start=week_start,
        week_start_name=WEEKDAY_NAMES[week_start],
        this_week=this_week,
        next_week=next_week,
        first_run=not r.list_cycles(),
        tab="cycles",
    ), 422 if error else 200


@bp.get("/cycles")
def cycles():
    r = repo()
    rows = []
    for c in r.list_cycles():
        goals = r.list_goals(c.id)
        hit = sum(1 for g in goals if goal_outcome(g, r.latest_progress(g.id)) == "hit")
        rows.append(
            {
                "cycle": c,
                "end": cycle_end(c.start_date),
                "average": cycle_average(r.weekly_scores(c.id).values()),
                "goals_hit": f"{hit}/{len(goals)}" if goals and c.status in ("review", "closed") else None,
            }
        )
    return render_template("cycles.html", rows=rows, can_create=r.current_cycle() is None, tab="cycles")


@bp.route("/c/<int:cycle_id>/edit", methods=["GET", "POST"])
def cycle_edit(cycle_id: int):
    """Rename or re-date a cycle. Closed cycles reach this page only to be deleted."""
    r = repo()
    cycle = load_cycle(cycle_id)
    read_only = cycle.status == "closed"
    if read_only and request.method == "POST":
        abort(403, "This cycle is closed and read-only.")
    form = {"name": cycle.name, "start": cycle.start_date.isoformat()}
    error = None
    if request.method == "POST":
        form = {k: request.form.get(k, "") for k in ("name", "start")}
        try:
            edit_cycle(r, cycle, form["name"], form["start"])
        except FormError as e:
            error = str(e)
        else:
            flash("Cycle saved.")
            return redirect(url_for(".dashboard", cycle_id=cycle.id))
    week_start = r.week_start
    return render_template(
        "cycle_form.html",
        cycle=cycle,
        form=form,
        error=error,
        read_only=read_only,
        start_locked=r.cycle_has_activity(cycle.id),
        week_start=week_start,
        week_start_name=WEEKDAY_NAMES[week_start],
        tab="cycles",
    ), 422 if error else 200


@bp.post("/c/<int:cycle_id>/delete")
def cycle_delete(cycle_id: int):
    r = repo()
    cycle = load_cycle(cycle_id)
    r.delete_cycle(cycle.id)
    flash(f"Deleted “{cycle.name}”.")
    return redirect(url_for(".cycles") if r.list_cycles() else url_for(".index"))


# -- dashboard and trend ----------------------------------------------------------


@bp.get("/c/<int:cycle_id>")
def dashboard(cycle_id: int):
    r = repo()
    cycle = load_cycle(cycle_id)
    status = cycle_status(r, cycle, today())
    scores = r.weekly_scores(cycle.id)
    last_week = max(scores) if scores else None
    show_outcomes = show_goal_outcomes(cycle, status)
    goals = []
    for goal in r.list_goals(cycle.id):
        latest = r.latest_progress(goal.id)
        goals.append(
            {
                "goal": goal,
                "now": goal.final_value if goal.final_value is not None else latest,
                "outcome": goal_outcome(goal, latest) if show_outcomes else None,
            }
        )
    current = r.current_cycle()
    # The daily action comes first on Home: how much of today's plan is logged.
    today_rows_ = today_rows(r, cycle, today()) if loggable(cycle, today(), today()) else None
    return render_template(
        "dashboard.html",
        cycle=cycle,
        status=status,
        end=cycle_end(cycle.start_date),
        last_week=last_week,
        last_score=scores[last_week] if last_week else None,
        average=cycle_average(scores.values()),
        reviewed_count=len(scores),
        chart=score_chart_svg(scores, compact=True),
        goals=goals,
        current=current if current and current.id != cycle.id else None,
        today_rows=today_rows_,
        week_progress=week_progress(today_rows_) if today_rows_ else None,
        unplanned=unplanned_reminders(r, cycle, today()),
        tab="home",
    )


@bp.get("/c/<int:cycle_id>/trend")
def trend(cycle_id: int):
    r = repo()
    cycle = load_cycle(cycle_id)
    scores = r.weekly_scores(cycle.id)
    answers = r.weekly_answers(cycle.id)
    weeks = []
    for w in range(1, EXECUTION_WEEKS + 1):
        first, last = week_range(cycle.start_date, w)
        weeks.append(
            {
                "no": w,
                "first": first,
                "last": last,
                "reviewed": w in scores,
                "answers": answers.get(w, []),
                "score": scores.get(w),
            }
        )
    return render_template(
        "trend.html",
        cycle=cycle,
        chart=score_chart_svg(scores),
        average=cycle_average(scores.values()),
        reviewed_count=len(scores),
        weeks=weeks,
        reviewable=set(reviewable_weeks(cycle, today())) if cycle.status != "closed" else set(),
        tab="trend",
    )


# -- plan -------------------------------------------------------------------------


@bp.get("/c/<int:cycle_id>/plan")
def plan(cycle_id: int):
    r = repo()
    cycle = load_cycle(cycle_id)
    goals = r.list_goals(cycle.id)
    tactics = r.list_tactics(cycle.id)
    limit = r.max_weekly_items
    heavy = set(heavy_weeks(tactics, limit))
    return render_template(
        "plan.html",
        cycle=cycle,
        goals=[(g, [t for t in tactics if t.goal_id == g.id]) for g in goals],
        blocks=r.list_blocks(cycle.id),
        BLOCK_KINDS=BLOCK_KINDS,
        has_tactics=bool(tactics),
        warnings=plan_warnings(goals, tactics, limit),
        clashes=plan_clashes(r, cycle),
        load=[(w, planned_items(tactics, w), w in heavy) for w in range(1, EXECUTION_WEEKS + 1)],
        read_only=cycle.status == "closed",
        tab="plan",
    )


@bp.post("/c/<int:cycle_id>/start")
def start_cycle(cycle_id: int):
    cycle = writable_cycle(cycle_id)
    if not repo().list_tactics(cycle.id):
        flash("Add at least one tactic before starting.", "warn")
        return to_plan(cycle)
    repo().set_cycle_status(cycle.id, "active")
    flash(f"“{cycle.name}” is active. Score each week in Review.")
    return redirect(url_for(".dashboard", cycle_id=cycle.id))


def _goal_page(cycle: Cycle, goal: Goal):
    fields = {
        "title": goal.title,
        "why": goal.why,
        "unit": goal.unit or "",
        "baseline": fmt_num(goal.baseline),
        "target": fmt_num(goal.target),
    }
    error = None
    if request.method == "POST":
        fields = {k: request.form.get(k, "") for k in GOAL_FIELDS}
        try:
            saved = repo().save_goal(parse_goal_form(goal, fields))
        except FormError as e:
            error = str(e)
        else:
            flash("Goal saved.")
            return to_plan(cycle, f"goal-{saved.id}")
    return render_template("goal_form.html", cycle=cycle, goal=goal, fields=fields, error=error, tab="plan"), (
        422 if error else 200
    )


@bp.route("/c/<int:cycle_id>/goals/new", methods=["GET", "POST"])
def goal_new(cycle_id: int):
    cycle = writable_cycle(cycle_id)
    return _goal_page(cycle, Goal(id=None, cycle_id=cycle.id, title=""))


@bp.route("/c/<int:cycle_id>/goals/<int:goal_id>/edit", methods=["GET", "POST"])
def goal_edit(cycle_id: int, goal_id: int):
    cycle = writable_cycle(cycle_id)
    return _goal_page(cycle, load_goal(cycle, goal_id))


@bp.post("/c/<int:cycle_id>/goals/<int:goal_id>/delete")
def goal_delete(cycle_id: int, goal_id: int):
    cycle = writable_cycle(cycle_id)
    goal = load_goal(cycle, goal_id)
    before_tactics_deleted(repo(), cycle, [t.id for t in repo().list_tactics_for_goal(goal.id)], today())
    repo().delete_goal(goal.id)
    flash(f"Deleted “{goal.title}”.")
    return to_plan(cycle)


@bp.post("/c/<int:cycle_id>/goals/<int:goal_id>/move")
def goal_move(cycle_id: int, goal_id: int):
    cycle = writable_cycle(cycle_id)
    goal = load_goal(cycle, goal_id)
    repo().move_goal(goal.id, move_delta())
    return moved(cycle, f"goal-{goal.id}")


def _tactic_page(cycle: Cycle, goal: Goal, tactic: Tactic):
    fields = {
        "title": tactic.title,
        "times": str(tactic.times_per_week),
        "start": str(tactic.start_week),
        "end": str(tactic.end_week),
    }
    error = None
    if request.method == "POST":
        fields = {k: request.form.get(k, "") for k in TACTIC_FIELDS}
        try:
            updated = parse_tactic_form(tactic, fields)
        except FormError as e:
            error = str(e)
        else:
            saved = repo().save_tactic(updated)
            after_tactic_saved(repo(), cycle, saved, today())
            flash("Tactic saved.")
            return to_plan(cycle, f"tactic-{saved.id}")
    return render_template(
        "tactic_form.html",
        cycle=cycle,
        goal=goal,
        tactic=tactic,
        fields=fields,
        error=error,
        tab="plan",
    ), 422 if error else 200


@bp.route("/c/<int:cycle_id>/goals/<int:goal_id>/tactics/new", methods=["GET", "POST"])
def tactic_new(cycle_id: int, goal_id: int):
    cycle = writable_cycle(cycle_id)
    goal = load_goal(cycle, goal_id)
    return _tactic_page(cycle, goal, Tactic(id=None, goal_id=goal.id, title=""))


@bp.route("/c/<int:cycle_id>/tactics/<int:tactic_id>/edit", methods=["GET", "POST"])
def tactic_edit(cycle_id: int, tactic_id: int):
    cycle = writable_cycle(cycle_id)
    tactic, goal = load_tactic(cycle, tactic_id)
    return _tactic_page(cycle, goal, tactic)


@bp.post("/c/<int:cycle_id>/tactics/<int:tactic_id>/delete")
def tactic_delete(cycle_id: int, tactic_id: int):
    cycle = writable_cycle(cycle_id)
    tactic, goal = load_tactic(cycle, tactic_id)
    before_tactics_deleted(repo(), cycle, [tactic.id], today())
    repo().delete_tactic(tactic.id)
    flash(f"Deleted “{tactic.title}”.")
    return to_plan(cycle, f"goal-{goal.id}")


@bp.post("/c/<int:cycle_id>/tactics/<int:tactic_id>/move")
def tactic_move(cycle_id: int, tactic_id: int):
    cycle = writable_cycle(cycle_id)
    tactic, _goal = load_tactic(cycle, tactic_id)
    repo().move_tactic(tactic.id, move_delta())
    return moved(cycle, f"tactic-{tactic.id}")


@bp.post("/c/<int:cycle_id>/schedule/check")
def schedule_check_json(cycle_id: int):
    """Live clash hints for the block form. Posted like the form itself, so the CSRF check applies."""
    cycle = writable_cycle(cycle_id)
    f = request.form
    if f.get("owner") != "block":
        abort(400)
    rows = schedule_check(
        repo(),
        cycle,
        f.get("owner_id", type=int),
        f.getlist("slot_day"),
        f.getlist("slot_time"),
        f.getlist("slot_minutes"),
    )
    return jsonify({"rows": rows})


# -- blocks -----------------------------------------------------------------------


def _block_page(cycle: Cycle, block: Block):
    fields = {"title": block.title, "kind": block.kind}
    rows = slot_rows(block.slots)
    error = None
    if request.method == "POST":
        fields = {k: request.form.get(k, "") for k in ("title", "kind")}
        rows = posted_slot_rows()
        try:
            saved = repo().save_block(parse_block_form(block, fields, posted_slots()))
        except FormError as e:
            error = str(e)
        else:
            after_block_saved(repo(), cycle, saved.id, today())
            flash("Block saved.")
            return to_plan(cycle, f"block-{saved.id}")
    return render_template(
        "block_form.html",
        cycle=cycle,
        block=block,
        fields=fields,
        slot_rows=rows,
        kinds=BLOCK_KINDS,
        error=error,
        tab="plan",
    ), 422 if error else 200


@bp.route("/c/<int:cycle_id>/blocks/new", methods=["GET", "POST"])
def block_new(cycle_id: int):
    cycle = writable_cycle(cycle_id)
    kind = request.args.get("kind", "other")
    kind = kind if kind in BLOCK_KINDS else "other"
    title = BLOCK_KINDS[kind] if kind != "other" else ""
    return _block_page(cycle, Block(id=None, cycle_id=cycle.id, title=title, kind=kind))


@bp.route("/c/<int:cycle_id>/blocks/<int:block_id>/edit", methods=["GET", "POST"])
def block_edit(cycle_id: int, block_id: int):
    cycle = writable_cycle(cycle_id)
    return _block_page(cycle, load_block(cycle, block_id))


@bp.post("/c/<int:cycle_id>/blocks/<int:block_id>/delete")
def block_delete(cycle_id: int, block_id: int):
    cycle = writable_cycle(cycle_id)
    block = load_block(cycle, block_id)
    before_block_deleted(repo(), cycle, block.id, today())
    repo().delete_block(block.id)
    flash(f"Deleted “{block.title}”.")
    return to_plan(cycle, "blocks")


@bp.post("/c/<int:cycle_id>/blocks/<int:block_id>/move")
def block_move(cycle_id: int, block_id: int):
    cycle = writable_cycle(cycle_id)
    block = load_block(cycle, block_id)
    repo().move_block(block.id, move_delta())
    return moved(cycle, f"block-{block.id}")


# -- week schedule ----------------------------------------------------------------


def default_schedule_week(cycle: Cycle) -> int:
    position = current_week(cycle.start_date, today())
    if position.week_no is not None:
        return position.week_no
    return 1 if position.not_started else REVIEW_WEEK


@bp.get("/c/<int:cycle_id>/week")
def week_default(cycle_id: int):
    cycle = load_cycle(cycle_id)
    return redirect(url_for(".week", cycle_id=cycle.id, week_no=default_schedule_week(cycle)))


@bp.get("/c/<int:cycle_id>/week/<int:week_no>")
def week(cycle_id: int, week_no: int):
    r = repo()
    cycle = load_cycle(cycle_id)
    if not 1 <= week_no <= REVIEW_WEEK:
        abort(404)
    view = week_view(r, cycle, week_no, today())
    sessions = view.sessions
    anytime_count = sum(a.count for a in view.anytime)
    imported = imports_for_week(r, cycle, week_no)
    shown = sorted(sessions + imported.timed, key=lambda s: (s.day, s.start_min, s.title))
    first, last = week_range(cycle.start_date, week_no)
    days = [first + timedelta(days=i) for i in range(7)]
    # Timeline bounds (desktop): whole hours around the week's sessions, at least 07:00–21:00.
    ghosts = [g for g, _label in view.ghosts]
    lo = min([s.start_min for s in shown + ghosts] + [7 * 60]) // 60 * 60
    hi = -(-max([s.end_min for s in shown + ghosts] + [21 * 60]) // 60) * 60
    planned = view.planned
    ended = week_ended(cycle, week_no, today())
    tactics = r.list_tactics(cycle.id) if week_no <= EXECUTION_WEEKS else []
    tactics = [t for t in tactics if t.active_in(week_no)]
    can_plan = (
        cycle.status != "closed"
        and week_no in plannable_weeks(cycle, today())
        and not week_ended(cycle, week_no, today())
    )
    return render_template(
        "week.html",
        cycle=cycle,
        week_no=week_no,
        first=first,
        last=last,
        days=[(d, [s for s in shown if s.day == d]) for d in days],
        clashes=overlaps(shown),
        layout=lanes(shown),
        imported=imported,
        kinds=SESSION_KINDS,
        total_min=sum(s.duration_min for s in sessions),
        session_count=len(sessions) + anytime_count,
        anytime=by_day(view.anytime),
        planned=planned,
        has_tactics=bool(tactics),
        short=shortfall(tactics, resolved_items(r, cycle, week_no)) if planned and not ended else [],
        can_plan=can_plan,
        plan_url=url_for(".week_plan", cycle_id=cycle.id, week_no=week_no),
        ended=ended,
        view=view,
        ghosts_by_day={d: [(g, label) for g, label in view.ghosts if g.day == d] for d in days},
        can_record=recordable(cycle, week_no, today()),
        record_url=url_for(".week_actual", cycle_id=cycle.id, week_no=week_no),
        lo=lo,
        span=hi - lo,
        hours=list(range(lo, hi, 60)),
        today=today(),
        read_only=cycle.status == "closed",
        tab="week",
    )


def plan_page(cycle: Cycle, week_no: int, form, error: str | None = None, copied: bool = False):
    r = repo()
    first, last = week_range(cycle.start_date, week_no)
    imported = imports_for_week(r, cycle, week_no)
    saved = sorted(week_sessions(r, cycle, week_no) + imported.timed, key=lambda s: (s.day, s.start_min, s.title))
    return render_template(
        "week_plan.html",
        cycle=cycle,
        week_no=week_no,
        first=first,
        last=last,
        form=form,
        days=[(d, [s for s in saved if s.day == d]) for d in (first + timedelta(days=i) for i in range(7))],
        grid_days=week_days(cycle, week_no),
        anytime=by_day(week_anytime(r, cycle, week_no)),
        imported=imported,
        kinds=SESSION_KINDS,
        planned=week_no in r.planned_weeks(cycle.id),
        can_copy=week_no > 1 and week_no - 1 in r.planned_weeks(cycle.id),
        copied=copied,
        error=error,
        today=today(),
        tab="week",
    ), 422 if error else 200


def by_day(anytime: list) -> dict:
    """Anytime sessions grouped by date, for the day-by-day lists."""
    grouped: dict = {}
    for a in anytime:
        grouped.setdefault(a.day, []).append(a)
    return grouped


def plannable_week(cycle_id: int, week_no: int) -> Cycle:
    """A week whose plan can be edited now: this week or next, not yet over, in an open cycle."""
    cycle = writable_cycle(cycle_id)
    if week_no not in plannable_weeks(cycle, today()) or week_ended(cycle, week_no, today()):
        abort(404)
    return cycle


def posted_plan_rows():
    return form_rows({name: request.form.getlist(name) for name in FORM_FIELDS})


@bp.route("/c/<int:cycle_id>/week/<int:week_no>/plan", methods=["GET", "POST"])
def week_plan(cycle_id: int, week_no: int):
    """Plan one week: place each tactic's sessions around what's already on, and adjust the blocks."""
    r = repo()
    if request.method == "GET" and recordable(load_cycle(cycle_id), week_no, today()):
        # The week is over: its plan is kept as it was, and changes now record what happened.
        return redirect(url_for(".week_actual", cycle_id=cycle_id, week_no=week_no))
    cycle = plannable_week(cycle_id, week_no)
    tactics = active_tactics(r, cycle, week_no)
    if request.method == "POST":
        rows = posted_plan_rows()
        try:
            items = parse_plan_rows(cycle, week_no, tactics, r.list_blocks(cycle.id), rows)
            save_week_plan(r, cycle, week_no, today(), items)
        except FormError as e:
            return plan_page(cycle, week_no, posted_form(r, cycle, week_no, rows), str(e))
        note = "; ".join(f"{t.title} {n} of {t.times_per_week}" for t, n in shortfall(tactics, items))
        flash(f"Week {week_no} planned." + (f" Not all placed: {note}." if note else ""), "warn" if note else "info")
        return redirect(url_for(".week", cycle_id=cycle.id, week_no=week_no))

    items = resolved_items(r, cycle, week_no)
    copied = request.args.get("copy") == "prev"
    if copied:
        previous = copy_prev_items(r, cycle, week_no)
        if previous is None:
            flash(f"Week {week_no - 1} wasn't planned, so there's nothing to copy.", "warn")
            copied = False
        else:
            items = previous + [i for i in items if i.kind != "tactic"]
    return plan_page(cycle, week_no, plan_form(r, cycle, week_no, items), copied=copied)


@bp.post("/c/<int:cycle_id>/week/<int:week_no>/plan/check")
def week_plan_check_json(cycle_id: int, week_no: int):
    """Live clash hints and placed counts for the planning form."""
    cycle = plannable_week(cycle_id, week_no)
    return jsonify(week_plan_check(repo(), cycle, week_no, posted_plan_rows()))


def local_next(default: str) -> str:
    """Where to go after a form, from `next`, if it's a path on this site."""
    target = request.values.get("next", "")
    return target if target.startswith("/") and not target.startswith("//") else default


def actual_page(cycle: Cycle, week_no: int, form, record, error: str | None = None):
    r = repo()
    first, last = week_range(cycle.start_date, week_no)
    imported = imports_for_week(r, cycle, week_no)
    planned = sorted(week_sessions(r, cycle, week_no) + imported.timed, key=lambda s: (s.day, s.start_min, s.title))
    return render_template(
        "week_actual.html",
        cycle=cycle,
        week_no=week_no,
        first=first,
        last=last,
        form=form,
        record=record,
        summary=summary_text(record),
        days=[(d, [s for s in planned if s.day == d]) for d in (first + timedelta(days=i) for i in range(7))],
        grid_days=week_days(cycle, week_no),
        anytime=by_day(week_anytime(r, cycle, week_no)),
        imported=imported,
        kinds=SESSION_KINDS,
        next_url=local_next(""),
        error=error,
        today=today(),
        tab="week",
    ), 422 if error else 200


def summary_text(record) -> str | None:
    if not record.planned:
        return None
    return compare_summary(record.changes) if record.recorded else "Not recorded yet, so taken as planned"


@bp.route("/c/<int:cycle_id>/week/<int:week_no>/actual", methods=["GET", "POST"])
def week_actual(cycle_id: int, week_no: int):
    """After a week: record what actually happened, beside the plan it had."""
    r = repo()
    cycle = writable_cycle(cycle_id)
    if not recordable(cycle, week_no, today()):
        abort(404)
    record = week_record(r, cycle, week_no)
    done = local_next(url_for(".week", cycle_id=cycle.id, week_no=week_no))
    if request.method == "POST":
        if request.form.get("action") == "reset":
            r.clear_actual(cycle.id, week_no)
            flash(f"Week {week_no} now reads as having gone to plan.")
            return redirect(done)
        rows = posted_plan_rows()
        try:
            items = parse_plan_rows(
                cycle, week_no, r.list_tactics(cycle.id), r.list_blocks(cycle.id), rows, record.plan_by_id
            )
            changed = save_actual(r, cycle, week_no, today(), items)
        except FormError as e:
            return actual_page(cycle, week_no, posted_form(r, cycle, week_no, rows, record.plan_by_id), record, str(e))
        record = week_record(r, cycle, week_no)
        if not record.planned:
            flash(f"Saved what happened in week {week_no}. It wasn't planned, so there's nothing to compare.")
        else:
            flash(f"Week {week_no}: {compare_summary(record.changes) if changed else 'went to plan'}.")
        return redirect(done)
    return actual_page(cycle, week_no, actual_form(r, cycle, week_no, record), record)


# -- today ------------------------------------------------------------------------


def load_day(cycle: Cycle, iso: str) -> date:
    try:
        day = date.fromisoformat(iso)
    except ValueError:
        abort(404)
    if not loggable(cycle, day, today()):
        abort(404)
    return day


@bp.get("/c/<int:cycle_id>/today")
def today_default(cycle_id: int):
    cycle = load_cycle(cycle_id)
    if not loggable(cycle, today(), today()):
        return render_template(
            "today.html", cycle=cycle, day=None, pos=current_week(cycle.start_date, today()), tab="today"
        )
    return redirect(url_for(".day_page", cycle_id=cycle.id, iso=today().isoformat()))


@bp.route("/c/<int:cycle_id>/day/<iso>", methods=["GET", "POST"])
def day_page(cycle_id: int, iso: str):
    r = repo()
    cycle = load_cycle(cycle_id)
    day = load_day(cycle, iso)
    read_only = cycle.status == "closed"
    existing = r.get_day_review(cycle.id, day)
    items = reflection_items(r.list_prompts("daily", active_only=True), existing.answers if existing else [])
    error = None

    if request.method == "POST":
        if read_only:
            abort(403, "This cycle is closed and read-only.")
        try:
            answers = parse_answers(items, request.form)
        except FormError as e:
            error = str(e)
        else:
            save_day(r, cycle, day, answers)
            flash(f"Reflection saved for {day:%a %d %b}.")
            return redirect(url_for(".day_page", cycle_id=cycle.id, iso=iso, _anchor="reflection"))

    week_no = week_of(cycle, day)
    first, last = week_range(cycle.start_date, week_no)
    rows = today_rows(r, cycle, day)
    imported = imports_for_week(r, cycle, week_no)
    return render_template(
        "today.html",
        cycle=cycle,
        day=day,
        week_no=week_no,
        days=[first + timedelta(days=i) for i in range(7)],
        open_days={d for d in (first + timedelta(days=i) for i in range(7)) if loggable(cycle, d, today())},
        reflected=r.reviewed_days(cycle.id, first, last),
        today=today(),
        rows=rows,
        progress=week_progress(rows),
        sessions=sorted(
            [s for s in week_sessions(r, cycle, week_no) if s.day == day] + imported.on(day),
            key=lambda s: (s.start_min, s.title),
        ),
        anytime=[a for a in week_anytime(r, cycle, week_no) if a.day == day],
        planned_today=planned_on(r, cycle, day),
        all_day=imported.all_day.get(day, []),
        import_errors=imported.errors,
        unplanned=unplanned_reminders(r, cycle, today()),
        kinds=SESSION_KINDS,
        existing=existing,
        items=items,
        error=error,
        read_only=read_only,
        review_week=default_review_week(r, cycle, today()),
        pending=pending_weeks(r, cycle, today()),
        tab="today",
    ), 422 if error else 200


@bp.post("/c/<int:cycle_id>/day/<iso>/log")
def day_log(cycle_id: int, iso: str):
    """Log one more (or one less) of a tactic on a day."""
    r = repo()
    cycle = writable_cycle(cycle_id)
    day = load_day(cycle, iso)
    tactic, _ = load_tactic(cycle, request.form.get("tactic_id", -1, type=int))
    if not tactic.active_in(week_of(cycle, day)):
        abort(404)
    r.log_tactic(tactic.id, day, move_delta())
    if request.accept_mimetypes.best == "application/json":
        rows = today_rows(r, cycle, day)
        row = next(row for row in rows if row.tactic_id == tactic.id)
        progress = week_progress(rows)
        return jsonify(today=row.today, week=row.week, progress=format_score(progress), band=score_band(progress))
    return redirect(url_for(".day_page", cycle_id=cycle.id, iso=iso, _anchor=f"t-{tactic.id}"))


# -- weekly review ----------------------------------------------------------------


@bp.get("/c/<int:cycle_id>/review")
def review_default(cycle_id: int):
    cycle = load_cycle(cycle_id)
    week_no = default_review_week(repo(), cycle, today())
    if week_no is None:
        return render_template("review.html", cycle=cycle, week_no=None, tab="review")
    return redirect(url_for(".review", cycle_id=cycle.id, week_no=week_no))


@bp.route("/c/<int:cycle_id>/review/<int:week_no>", methods=["GET", "POST"])
def review(cycle_id: int, week_no: int):
    r = repo()
    cycle = load_cycle(cycle_id)
    weeks = reviewable_weeks(cycle, today())
    if week_no not in weeks:
        abort(404)
    read_only = cycle.status == "closed"
    existing = r.get_week_review(cycle.id, week_no)
    rows = review_rows(r, cycle, week_no, existing)
    lag_goals = [g for g in r.list_goals(cycle.id) if g.measurable]
    items = reflection_items(r.list_prompts("weekly", active_only=True), existing.answers if existing else [])
    values = {f"lag-{g.id}": fmt_num(r.goal_progress(g.id).get(week_no)) for g in lag_goals}
    what_happened = review_record(r, cycle, week_no, today())
    error = None

    if request.method == "POST":
        if read_only:
            abort(403, "This cycle is closed and read-only.")
        # "What happened" is part of the same form: checked first, saved only if it was changed.
        record_rows = (
            posted_plan_rows() if what_happened and what_happened.form and "record_present" in request.form else None
        )
        happened = None
        done = {}
        for row in rows:
            if row.tactic_id is None:
                continue  # deleted tactic: its saved count is kept as is
            try:
                done[row.tactic_id] = parse_int(request.form.get(f"done-{row.tactic_id}", ""))
            except ValueError:
                done[row.tactic_id] = 0
        entries = week_entries(cycle.id, week_no, rows, done)
        values = {k: request.form.get(k, "") for k in values}
        try:
            answers = parse_answers(items, request.form)
            try:
                progress = {g.id: parse_float(values[f"lag-{g.id}"]) for g in lag_goals}
            except ValueError:
                raise FormError("Lag measures must be numbers.") from None
            if record_rows is not None:
                try:
                    happened = parse_review_record(r, cycle, week_no, record_rows)
                except FormError as e:
                    raise FormError(f"What happened: {e}") from None
        except FormError as e:
            error = str(e)
            for row, entry in zip(rows, entries):
                row.done = entry.done
            if record_rows is not None:
                what_happened.form = posted_form(r, cycle, week_no, record_rows, what_happened.record.plan_by_id)
        else:
            score = save_week(r, cycle, week_no, entries, answers, progress)
            if happened is not None:
                save_actual(r, cycle, week_no, today(), happened)
            pending = pending_weeks(r, cycle, today())
            if pending:
                # Catching up: go straight on to the next week still to score.
                flash(f"Week {week_no} saved: {format_score(score)}. Now week {pending[0]}.")
                return redirect(url_for(".review", cycle_id=cycle.id, week_no=pending[0]))
            to_plan_next = next_week_to_plan(r, cycle, today())
            if to_plan_next is not None:
                # The book's weekly routine: score last week, then plan this one.
                flash(f"Week {week_no} saved: {format_score(score)}. Now plan week {to_plan_next}.")
                return redirect(url_for(".week_plan", cycle_id=cycle.id, week_no=to_plan_next))
            flash(f"Week {week_no} saved: {format_score(score)}.")
            return redirect(url_for(".dashboard", cycle_id=cycle.id))

    first, last = week_range(cycle.start_date, week_no)
    return render_template(
        "review.html",
        cycle=cycle,
        week_no=week_no,
        weeks=weeks,
        reviewed=r.reviewed_weeks(cycle.id),
        first=first,
        last=last,
        existing=existing,
        rows=rows,
        score=execution_score(week_entries(cycle.id, week_no, rows, {row.tactic_id: row.done for row in rows})),
        lag_goals=lag_goals,
        values=values,
        items=items,
        error=error,
        read_only=read_only,
        in_progress=current_week(cycle.start_date, today()).week_no,
        what_happened=what_happened,
        grid_days=week_days(cycle, week_no),
        record_open=bool(error and request.method == "POST" and "record_present" in request.form)
        or bool(what_happened and (what_happened.hints or what_happened.record.recorded)),
        record_url=url_for(".week_actual", cycle_id=cycle.id, week_no=week_no, next=request.path),
        tab="review",
    ), 422 if error else 200


# -- week 13 ----------------------------------------------------------------------


@bp.route("/c/<int:cycle_id>/week13", methods=["GET", "POST"])
def week13(cycle_id: int):
    r = repo()
    cycle = load_cycle(cycle_id)
    read_only = cycle.status == "closed"
    goals = r.list_goals(cycle.id)
    cycle_review = r.get_cycle_review(cycle.id)
    values = {
        "lessons": cycle_review.lessons if cycle_review else "",
        "celebration": cycle_review.celebration if cycle_review else "",
    }
    for goal in goals:
        if goal.measurable:
            values[f"final-{goal.id}"] = fmt_num(
                goal.final_value if goal.final_value is not None else r.latest_progress(goal.id)
            )
        else:
            values[f"manual-{goal.id}"] = goal.outcome or ""
    error = None

    if request.method == "POST":
        if read_only:
            abort(403, "This cycle is closed and read-only.")
        values = {k: request.form.get(k, "") for k in values}
        try:
            save_week13(
                r,
                cycle,
                finals={goal.id: values[f"final-{goal.id}"] for goal in goals if goal.measurable},
                manual={goal.id: values[f"manual-{goal.id}"] or None for goal in goals if not goal.measurable},
                lessons=values["lessons"],
                celebration=values["celebration"],
            )
        except FormError as e:
            error = str(e)
        else:
            if request.form.get("action") == "close":
                close_cycle(r, cycle)
                flash(f"“{cycle.name}” closed. Well done! Set up your next 12 weeks.")
                return redirect(url_for(".new_cycle", copy_from=cycle.id))
            flash("Week 13 review saved.")
            return redirect(url_for(".week13", cycle_id=cycle.id))

    goal_rows = []
    for goal in goals:
        outcome = None
        if goal.measurable:
            try:
                final = parse_float(values[f"final-{goal.id}"])
            except ValueError:
                final = None
            outcome = goal_outcome(replace(goal, final_value=final))
        goal_rows.append({"goal": goal, "outcome": outcome})

    scores = r.weekly_scores(cycle.id)
    pos = current_week(cycle.start_date, today())
    early = pos.not_started or (pos.week_no is not None and pos.week_no <= EXECUTION_WEEKS)
    return render_template(
        "week13.html",
        cycle=cycle,
        average=cycle_average(scores.values()),
        reviewed_count=len(scores),
        good_weeks=sum(1 for s in scores.values() if s is not None and s >= 0.85),
        early=early,
        current_week=pos.week_no or 0,
        goal_rows=goal_rows,
        values=values,
        error=error,
        read_only=read_only,
        tab="week13",
    ), 422 if error else 200


# -- vision, settings, more -------------------------------------------------------


@bp.route("/vision", methods=["GET", "POST"])
def vision():
    r = repo()
    if request.method == "POST":
        for horizon in ("long_term", "three_year"):
            r.set_vision(horizon, request.form.get(horizon, ""))
        flash("Vision saved.")
        return redirect(url_for(".vision"))
    return render_template(
        "vision.html", long_term=r.get_vision("long_term"), three_year=r.get_vision("three_year"), tab="vision"
    )


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    r = repo()
    fields, error = saved_settings_fields(), None
    if request.method == "POST":
        week_start = request.form.get("week_start", type=int)
        if week_start is None or not 0 <= week_start <= 6:
            abort(400)
        fields = {k: request.form.get(k, "") for k in fields} | {"week_start": week_start}
        try:
            limit = parse_max_weekly_items(fields["max_weekly_items"])
            alert_minutes = parse_alert_minutes(fields["alert_minutes"])
            window = parse_day_window(fields["day_start"], fields["day_end"])
        except FormError as e:
            error = str(e)
        else:
            r.set_setting("week_start", str(week_start))
            r.set_setting("max_weekly_items", str(limit))
            r.set_setting("alert_minutes", str(alert_minutes))
            r.set_setting("day_start", str(window[0]))
            r.set_setting("day_end", str(window[1]))
            flash(f"Settings saved. Weeks start on {WEEKDAY_NAMES[week_start]}.")
            return redirect(url_for(".settings"))
    return settings_page(fields, error=error)


def settings_page(fields: dict, error: str | None = None, import_text: str | None = None, import_error=None):
    r = repo()
    feed_url = url_for(".calendar_feed", token=r.calendar_token, _external=True)
    return render_template(
        "settings.html",
        weekdays=WEEKDAY_NAMES,
        **fields,
        feed_url=feed_url,
        webcal_url="webcal://" + feed_url.split("://", 1)[1],
        error=error,
        import_urls="\n".join(r.import_urls) if import_text is None else import_text,
        import_error=import_error,
        MAX_LINKS=MAX_LINKS,
        db_path=current_app.config["TWY_DB"],
        prompts={scope: r.list_prompts(scope) for scope in PROMPT_SCOPES},
        prompt_kinds=PROMPT_KINDS,
        RESET_WORD=RESET_WORD,
        tab="settings",
    )


def saved_settings_fields() -> dict:
    r = repo()
    day_start, day_end = (hhmm(m) for m in r.day_window)
    return {
        "week_start": r.week_start,
        "max_weekly_items": str(r.max_weekly_items),
        "alert_minutes": str(r.alert_minutes),
        "day_start": day_start,
        "day_end": day_end,
    }


def load_prompt(prompt_id: int) -> Prompt:
    try:
        return repo().get_prompt(prompt_id)
    except KeyError:
        abort(404)


def to_prompts(prompt: Prompt | None = None):
    return redirect(url_for(".settings", _anchor=f"prompt-{prompt.id}" if prompt else "prompts"))


@bp.post("/settings/prompts")
def prompt_new():
    scope = request.form.get("scope")
    if scope not in PROMPT_SCOPES:
        abort(400)
    try:
        prompt = parse_prompt_form(Prompt(None, scope, ""), request.form)
    except FormError as e:
        flash(str(e), "warn")
        return to_prompts()
    repo().save_prompt(prompt)
    flash(f"Added “{prompt.label}” to the {scope} review.")
    return to_prompts(prompt)


@bp.post("/settings/prompts/<int:prompt_id>")
def prompt_edit(prompt_id: int):
    try:
        prompt = parse_prompt_form(load_prompt(prompt_id), request.form)
    except FormError as e:
        flash(str(e), "warn")
        return to_prompts()
    repo().save_prompt(prompt)
    flash(f"Saved “{prompt.label}”. Past reviews keep the wording they were answered under.")
    return to_prompts(prompt)


@bp.post("/settings/prompts/<int:prompt_id>/toggle")
def prompt_toggle(prompt_id: int):
    prompt = load_prompt(prompt_id)
    repo().save_prompt(replace(prompt, active=not prompt.active))
    return to_prompts(prompt)


@bp.post("/settings/prompts/<int:prompt_id>/move")
def prompt_move(prompt_id: int):
    prompt = load_prompt(prompt_id)
    repo().move_prompt(prompt.id, move_delta())
    return to_prompts(prompt)


@bp.post("/settings/prompts/<int:prompt_id>/delete")
def prompt_delete(prompt_id: int):
    prompt = load_prompt(prompt_id)
    repo().delete_prompt(prompt.id)
    flash(f"Deleted “{prompt.label}”. Past answers to it are kept.")
    return to_prompts()


@bp.post("/settings/imports")
def settings_imports():
    text = request.form.get("import_urls", "")
    try:
        urls = parse_import_urls(text)
    except FormError as e:
        return settings_page(saved_settings_fields(), import_text=text, import_error=str(e)), 422
    repo().set_setting("import_urls", "\n".join(urls))
    flash(
        f"Planning around {len(urls)} calendar{'s' if len(urls) != 1 else ''}." if urls else "Calendar links removed."
    )
    return redirect(url_for(".settings", _anchor="imports"))


@bp.post("/settings/calendar/reset")
def calendar_reset():
    repo().rotate_calendar_token()
    flash("New calendar link created. Subscribe again with it; the old link no longer works.", "warn")
    return redirect(url_for(".settings", _anchor="calendar"))


@bp.post("/settings/reset")
def settings_reset():
    try:
        factory_reset(repo(), request.form.get("confirm", ""))
    except FormError as e:
        flash(str(e), "warn")
        return redirect(url_for(".settings", _anchor="reset"))
    flash("Everything was deleted. Set twy up again to start fresh.")
    return redirect(url_for(".setup"))


@bp.get("/calendar/<token>.ics")
def calendar_feed(token: str):
    """Public (calendar apps can't log in); the secret token in the URL is the password."""
    r = repo()
    if not hmac.compare_digest(token.encode(), r.calendar_token.encode()):
        abort(404)
    cycles = [c for c in r.list_cycles() if c.status != "closed"]
    planning = {c.id: planning_offset(c, r.list_blocks(c.id)) for c in cycles}
    planned = {(c.id, w) for c in cycles for w in r.planned_weeks(c.id)}
    weeks = feed_weeks(cycles, planning, today(), planned)
    sessions = [s for cycle, week_no in weeks for s in week_sessions(r, cycle, week_no)]
    anytime = [a for cycle, week_no in weeks for a in week_anytime(r, cycle, week_no)]
    body = render_calendar(sessions, r.alert_minutes, datetime.now(UTC), anytime=anytime)
    return Response(body, mimetype="text/calendar", headers={"Cache-Control": "no-cache"})


@bp.get("/settings/backup")
def backup():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "twy.db"
        backup_to(repo().conn, path)
        data = path.read_bytes()
    return send_file(
        BytesIO(data),
        mimetype="application/vnd.sqlite3",
        as_attachment=True,
        download_name=f"twy-{date.today():%Y-%m-%d}.db",
    )


@bp.get("/more")
def more():
    return render_template("more.html", tab="more")
