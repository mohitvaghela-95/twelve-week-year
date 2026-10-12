"""All web pages. Cycle pages are addressed by id (/c/<id>/…) so every URL is bookmarkable."""

import tempfile
from dataclasses import replace
from datetime import date
from io import BytesIO
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file, url_for

from twy.db import backup_to, connect
from twy.domain.dates import EXECUTION_WEEKS, WEEKDAY_NAMES, align_to_week_start, current_week, cycle_end, week_range
from twy.domain.guardrails import heavy_weeks, plan_warnings
from twy.domain.scoring import cycle_average, execution_score, format_score, goal_outcome, planned_items
from twy.models import Cycle, Goal, Tactic
from twy.repo import Repo
from twy.services import (
    FormError,
    close_cycle,
    create_cycle,
    cycle_status,
    default_review_week,
    fmt_num,
    parse_cycle_form,
    parse_float,
    parse_goal_form,
    parse_int,
    parse_max_weekly_items,
    parse_tactic_form,
    previous_cycle_id,
    review_rows,
    reviewable_weeks,
    save_week,
    save_week13,
    show_goal_outcomes,
    week_entries,
)
from twy.web.svg import score_chart_svg

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


def to_plan(cycle: Cycle, anchor: str | None = None):
    return redirect(url_for(".plan", cycle_id=cycle.id, _anchor=anchor))


def move_delta() -> int:
    return -1 if request.form.get("delta") == "-1" else 1


@bp.app_context_processor
def nav_context() -> dict:
    """The cycle the tab bar links to: the one on screen, else the current one."""
    cycle_id = (request.view_args or {}).get("cycle_id")
    if cycle_id is None and request.endpoint not in ("auth.login", "web.healthz", "static"):
        current = repo().current_cycle()
        cycle_id = current.id if current else None
    return {"nav_cycle_id": cycle_id, "EXECUTION_WEEKS": EXECUTION_WEEKS}


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
    default_start = align_to_week_start(today(), week_start)
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
        tab="home",
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
    return render_template("cycles.html", rows=rows, tab="more")


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
        tab="home",
    )


@bp.get("/c/<int:cycle_id>/trend")
def trend(cycle_id: int):
    r = repo()
    cycle = load_cycle(cycle_id)
    scores = r.weekly_scores(cycle.id)
    weeks = []
    for w in range(1, EXECUTION_WEEKS + 1):
        first, last = week_range(cycle.start_date, w)
        weeks.append({"no": w, "first": first, "last": last, "review": r.get_week_review(cycle.id, w), "score": scores.get(w)})
    return render_template(
        "trend.html",
        cycle=cycle,
        chart=score_chart_svg(scores),
        average=cycle_average(scores.values()),
        reviewed_count=len(scores),
        weeks=weeks,
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
        has_tactics=bool(tactics),
        warnings=plan_warnings(goals, tactics, limit),
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
    repo().delete_goal(goal.id)
    flash(f"Deleted “{goal.title}”.")
    return to_plan(cycle)


@bp.post("/c/<int:cycle_id>/goals/<int:goal_id>/move")
def goal_move(cycle_id: int, goal_id: int):
    cycle = writable_cycle(cycle_id)
    goal = load_goal(cycle, goal_id)
    repo().move_goal(goal.id, move_delta())
    return to_plan(cycle, f"goal-{goal.id}")


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
            saved = repo().save_tactic(parse_tactic_form(tactic, fields))
        except FormError as e:
            error = str(e)
        else:
            flash("Tactic saved.")
            return to_plan(cycle, f"tactic-{saved.id}")
    return render_template(
        "tactic_form.html", cycle=cycle, goal=goal, tactic=tactic, fields=fields, error=error, tab="plan"
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
    repo().delete_tactic(tactic.id)
    flash(f"Deleted “{tactic.title}”.")
    return to_plan(cycle, f"goal-{goal.id}")


@bp.post("/c/<int:cycle_id>/tactics/<int:tactic_id>/move")
def tactic_move(cycle_id: int, tactic_id: int):
    cycle = writable_cycle(cycle_id)
    tactic, _goal = load_tactic(cycle, tactic_id)
    repo().move_tactic(tactic.id, move_delta())
    return to_plan(cycle, f"tactic-{tactic.id}")


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
    values = {
        "obstacles": existing.obstacles if existing else "",
        "fix": existing.fix if existing else "",
    } | {f"lag-{g.id}": fmt_num(r.goal_progress(g.id).get(week_no)) for g in lag_goals}
    error = None

    if request.method == "POST":
        if read_only:
            abort(403, "This cycle is closed and read-only.")
        done = {}
        for row in rows:
            try:
                done[row.tactic_id] = parse_int(request.form.get(f"done-{row.tactic_id}", ""))
            except ValueError:
                done[row.tactic_id] = 0
        entries = week_entries(cycle.id, week_no, rows, done)
        values = {k: request.form.get(k, "") for k in values}
        try:
            progress = {g.id: parse_float(values[f"lag-{g.id}"]) for g in lag_goals}
        except ValueError:
            error = "Lag measures must be numbers."
            for row, entry in zip(rows, entries):
                row.done = entry.done
        else:
            score = save_week(r, cycle, week_no, entries, values["obstacles"], values["fix"], progress)
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
        error=error,
        read_only=read_only,
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
    for g in goals:
        if g.measurable:
            values[f"final-{g.id}"] = fmt_num(g.final_value if g.final_value is not None else r.latest_progress(g.id))
        else:
            values[f"manual-{g.id}"] = g.outcome or ""
    error = None

    if request.method == "POST":
        if read_only:
            abort(403, "This cycle is closed and read-only.")
        values = {k: request.form.get(k, "") for k in values}
        try:
            save_week13(
                r,
                cycle,
                finals={g.id: values[f"final-{g.id}"] for g in goals if g.measurable},
                manual={g.id: values[f"manual-{g.id}"] or None for g in goals if not g.measurable},
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
    for g in goals:
        outcome = None
        if g.measurable:
            try:
                final = parse_float(values[f"final-{g.id}"])
            except ValueError:
                final = None
            outcome = goal_outcome(replace(g, final_value=final))
        goal_rows.append({"goal": g, "outcome": outcome})

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
        tab="more",
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
        "vision.html", long_term=r.get_vision("long_term"), three_year=r.get_vision("three_year"), tab="more"
    )


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    r = repo()
    week_start, max_items, error = r.week_start, str(r.max_weekly_items), None
    if request.method == "POST":
        week_start = request.form.get("week_start", type=int)
        if week_start is None or not 0 <= week_start <= 6:
            abort(400)
        max_items = request.form.get("max_weekly_items", "")
        try:
            limit = parse_max_weekly_items(max_items)
        except FormError as e:
            error = str(e)
        else:
            r.set_setting("week_start", str(week_start))
            r.set_setting("max_weekly_items", str(limit))
            flash(f"Settings saved. Weeks start on {WEEKDAY_NAMES[week_start]}.")
            return redirect(url_for(".settings"))
    return render_template(
        "settings.html",
        weekdays=WEEKDAY_NAMES,
        week_start=week_start,
        max_weekly_items=max_items,
        error=error,
        db_path=current_app.config["TWY_DB"],
        tab="more",
    )


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
