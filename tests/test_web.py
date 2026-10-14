"""Web app tests through Flask's test client, against a temporary database file."""

import sqlite3
from datetime import date

import pytest

from twy.db import connect
from twy.models import Goal, Tactic
from twy.repo import Repo
from twy.web import create_app

DAY = {"day_start": "06:00", "day_end": "22:00"}  # the settings form's suggestion hours
TODAY = date(2026, 10, 14)  # a Wednesday: week 2 of a cycle starting Mon 5 Oct


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "twy.db"


@pytest.fixture
def client(db_path):
    app = create_app(db_path=db_path, today=TODAY, password="secret", secret_key="test-key")
    app.config["TESTING"] = True
    return app.test_client()


def csrf(client) -> str:
    with client.session_transaction() as session:
        return session.setdefault("csrf", "test-token")


def post(client, url, data=None, **kwargs):
    return client.post(url, data={"csrf": csrf(client), **(data or {})}, **kwargs)


def login(client):
    assert post(client, "/login", {"password": "secret"}).status_code == 302


def repo_for(db_path) -> Repo:
    return Repo(connect(db_path))


def test_create_app_requires_password(db_path, monkeypatch):
    monkeypatch.delenv("TWY_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="TWY_PASSWORD"):
        create_app(db_path=db_path)


def test_login_and_csrf(client):
    assert client.get("/healthz").text == "ok"  # public, for the container health check
    response = client.get("/c/1/plan")
    assert response.status_code == 302 and response.location.startswith("/login?next=/c/1/plan")

    assert post(client, "/login", {"password": "nope"}).status_code == 401
    response = post(client, "/login?next=/settings", {"password": "secret"})
    assert response.status_code == 302 and response.location == "/settings"
    assert client.get("/settings").status_code == 200

    assert client.post("/vision", data={"long_term": "x"}).status_code == 400  # no CSRF token
    assert post(client, "/logout").status_code == 302
    assert client.get("/settings").status_code == 302


def test_login_ignores_offsite_next(client):
    response = post(client, "/login?next=//evil.example/", {"password": "secret"})
    assert response.location == "/"


def test_full_cycle_flow(client, db_path):
    login(client)
    assert client.get("/").location == "/setup"

    response = post(client, "/setup", {"week_start": "0", "long_term": "Healthy", "three_year": "Fit"})
    assert response.location == "/cycles/new"
    page = client.get("/cycles/new").text
    assert 'value="2026-10-19"' in page  # defaults to the next Monday

    # Start date snaps forward to the week start; an earlier Monday stays as is.
    response = post(client, "/cycles/new", {"name": "Autumn", "start": "2026-10-05"})
    assert response.status_code == 302
    repo = repo_for(db_path)
    cycle = repo.current_cycle()
    assert (cycle.name, cycle.start_date, cycle.status) == ("Autumn", date(2026, 10, 5), "planning")
    base = f"/c/{cycle.id}"

    # Invalid goal is re-shown with the shared validation message.
    response = post(client, f"{base}/goals/new", {"title": "Lose 5 kg", "baseline": "82"})
    assert response.status_code == 422 and "Add a target, or clear the baseline." in response.text

    post(client, f"{base}/goals/new", {"title": "Lose 5 kg", "unit": "kg", "baseline": "82", "target": "77"})
    goal = repo.list_goals(cycle.id)[0]
    post(client, f"{base}/goals/{goal.id}/tactics/new", {"title": "Run", "times": "3", "start": "1", "end": "12"})
    post(client, f"{base}/goals/{goal.id}/tactics/new", {"title": "Log food", "times": "1", "start": "1", "end": "12"})
    run, log = repo.list_tactics(cycle.id)
    post(client, f"{base}/tactics/{log.id}/move", {"delta": "-1"})
    assert [t.title for t in repo.list_tactics(cycle.id)] == ["Log food", "Run"]
    assert "Run" in client.get(f"{base}/plan").text

    response = post(client, f"{base}/start")
    assert response.location == base and repo.get_cycle(cycle.id).status == "active"

    # Week 1 is finished and pending, so Review opens it.
    assert "pending for week 1" in client.get(base).text
    assert client.get(f"{base}/review").location == f"{base}/review/1"
    assert client.get(f"{base}/review/5").status_code == 404  # not reachable yet
    obstacles, fix = (p.id for p in repo.list_prompts("weekly"))

    response = post(
        client,
        f"{base}/review/1",
        {
            f"done-{run.id}": "2",
            f"done-{log.id}": "9",
            f"lag-{goal.id}": "x",
            f"p-{obstacles}": "Rain",
            f"p-{fix}": "Gym",
        },
    )
    assert response.status_code == 422 and "Lag measures must be numbers." in response.text
    assert ">Rain</textarea>" in response.text  # answers survive the error
    post(
        client,
        f"{base}/review/1",
        {
            f"done-{run.id}": "2",
            f"done-{log.id}": "9",
            f"lag-{goal.id}": "80.5",
            f"p-{obstacles}": "Rain",
            f"p-{fix}": "Gym",
        },
    )
    assert repo.weekly_scores(cycle.id) == {1: 0.75}  # (2 + 1 clamped) / 4
    assert repo.latest_progress(goal.id) == 80.5
    dashboard = client.get(base).text
    assert "75%" in dashboard and "pending" not in dashboard
    trend = client.get(f"{base}/trend").text
    assert trend.count('class="bar band-warn"') == 1 and "<dd>Rain</dd>" in trend

    # Week 13: save, then close and start the next cycle with the plan copied.
    response = post(client, f"{base}/week13", {f"final-{goal.id}": "76", "lessons": "Earlier", "action": "save"})
    assert response.location == f"{base}/week13"
    assert repo.get_goal(goal.id).outcome == "hit" and repo.get_cycle(cycle.id).status == "review"

    response = post(client, f"{base}/week13", {f"final-{goal.id}": "76", "lessons": "Earlier", "action": "close"})
    assert response.location == f"/cycles/new?copy_from={cycle.id}"
    assert repo.get_cycle(cycle.id).status == "closed"

    post(client, "/cycles/new", {"name": "Winter", "start": "2027-01-04", "copy": "on", "copy_from": str(cycle.id)})
    nxt = repo.current_cycle()
    assert nxt.name == "Winter" and [t.title for t in repo.list_tactics(nxt.id)] == ["Log food", "Run"]

    cycles = client.get("/cycles").text
    assert "Autumn" in cycles and "Winter" in cycles and "goals hit 1/1" in cycles


def test_closed_cycle_is_read_only(client, db_path):
    login(client)
    repo = repo_for(db_path)
    repo.set_setting("week_start", "0")
    cycle = repo.create_cycle("Old", date(2026, 6, 1), 0)
    repo.set_cycle_status(cycle.id, "closed")
    base = f"/c/{cycle.id}"

    assert post(client, f"{base}/goals/new", {"title": "x"}).status_code == 403
    assert post(client, f"{base}/review/1", {}).status_code == 403
    assert post(client, f"{base}/day/2026-06-02", {}).status_code == 403
    assert post(client, f"{base}/day/2026-06-02/log", {"tactic_id": "1"}).status_code == 403
    assert post(client, f"{base}/week13", {"action": "save"}).status_code == 403
    assert "read-only" in client.get(base).text
    assert client.get(f"{base}/review/1").status_code == 200


def test_backup_download_is_a_valid_database(client, tmp_path):
    login(client)
    post(client, "/setup", {"week_start": "6", "long_term": "", "three_year": ""})
    response = client.get("/settings/backup")
    assert response.status_code == 200
    assert response.headers["Content-Disposition"].startswith("attachment; filename=twy-")
    assert response.data.startswith(b"SQLite format 3\x00")

    copy = tmp_path / "copy.db"
    copy.write_bytes(response.data)
    conn = sqlite3.connect(copy)
    assert conn.execute("SELECT value FROM settings WHERE key = 'week_start'").fetchone() == ("6",)
    assert conn.execute("PRAGMA journal_mode").fetchone() == ("delete",)


def test_every_page_renders(client, db_path):
    login(client)
    post(client, "/setup", {"week_start": "0", "long_term": "", "three_year": ""})
    post(client, "/cycles/new", {"name": "Q4", "start": "2026-10-05"})
    for url in [
        "/c/1",
        "/c/1/day/2026-10-14",
        "/c/1/plan",
        "/c/1/review/1",
        "/c/1/trend",
        "/c/1/week13",
        "/c/1/week/1",
        "/c/1/blocks/new",
        "/cycles",
        "/vision",
        "/settings",
        "/more",
    ]:
        assert client.get(url).status_code == 200, url
    assert client.get("/c/1/today").location == "/c/1/day/2026-10-14"
    later = repo_for(db_path).create_cycle("Later", date(2027, 1, 4), 0)
    assert "Nothing to track today." in client.get(f"/c/{later.id}/today").text


def test_heavy_week_limit_setting(client, db_path):
    login(client)
    post(client, "/setup", {"week_start": "0", "long_term": "", "three_year": ""})
    post(client, "/cycles/new", {"name": "Q4", "start": "2026-10-05"})
    repo = repo_for(db_path)
    cycle = repo.current_cycle()
    post(client, f"/c/{cycle.id}/goals/new", {"title": "AI engineer offer"})
    goal = repo.list_goals(cycle.id)[0]
    post(
        client,
        f"/c/{cycle.id}/goals/{goal.id}/tactics/new",
        {"title": "Study", "times": "5", "start": "1", "end": "12"},
    )

    assert repo.max_weekly_items == 25
    assert 'value="25"' in client.get("/settings").text
    page = client.get(f"/c/{cycle.id}/plan").text
    assert "Heavy weeks" not in page and "load-cell heavy" not in page

    response = post(client, "/settings", {"week_start": "0", "max_weekly_items": "abc", "alert_minutes": "10", **DAY})
    assert response.status_code == 200 and "whole number" in response.text
    assert repo.max_weekly_items == 25  # nothing saved

    assert (
        post(
            client, "/settings", {"week_start": "0", "max_weekly_items": "4", "alert_minutes": "10", **DAY}
        ).status_code
        == 302
    )
    assert repo.max_weekly_items == 4
    page = client.get(f"/c/{cycle.id}/plan").text
    assert "Heavy weeks (&gt; 4 items)" in page and "load-cell heavy" in page


def test_schedule_week_page_and_calendar_feed(client, db_path):
    login(client)
    post(client, "/setup", {"week_start": "0", "long_term": "", "three_year": ""})
    post(client, "/cycles/new", {"name": "Q4", "start": "2026-10-12"})  # TODAY (Wed 14 Oct) is in week 1
    repo = repo_for(db_path)
    cycle = repo.current_cycle()
    base = f"/c/{cycle.id}"
    post(client, f"{base}/goals/new", {"title": "AI engineer offer"})
    goal = repo.list_goals(cycle.id)[0]
    tactic = {"title": "Study", "times": "5", "start": "1", "end": "12"}

    # A bad slot re-shows the form with what was typed.
    bad = {**tactic, "slot_day": ["0"], "slot_time": ["7am"], "slot_minutes": ["60"]}
    response = post(client, f"{base}/goals/{goal.id}/tactics/new", bad)
    assert response.status_code == 422 and "Slot 1" in response.text and 'value="7am"' in response.text
    assert repo.list_tactics(cycle.id) == []

    good = {**tactic, "slot_day": ["0", "2", "4"], "slot_time": ["07:00", "07:00", ""], "slot_minutes": ["60"] * 3}
    post(client, f"{base}/goals/{goal.id}/tactics/new", good)
    study = repo.list_tactics(cycle.id)[0]
    assert [s.label for s in repo.list_slots("tactic_id", study.id)] == ["Mon 07:00–08:00", "Wed 07:00–08:00"]
    plan = client.get(f"{base}/plan").text
    assert "Mon 07:00–08:00 · Wed 07:00–08:00" in plan
    assert "has 2 slots in the calendar but is planned 5×/week" in plan

    assert 'value="Weekly review"' in client.get(f"{base}/blocks/new?kind=review").text
    review = {
        "title": "Weekly review",
        "kind": "review",
        "slot_day": ["6"],
        "slot_time": ["18:00"],
        "slot_minutes": ["30"],
    }
    assert post(client, f"{base}/blocks/new", review).status_code == 302
    admin = {"title": "Admin", "kind": "admin", "slot_day": ["0"], "slot_time": ["07:30"], "slot_minutes": ["30"]}
    post(client, f"{base}/blocks/new", admin)  # clashes with Monday's study session
    assert [b.title for b in repo.list_blocks(cycle.id)] == ["Weekly review", "Admin"]

    assert client.get(f"{base}/week").location == f"{base}/week/1"
    page = client.get(f"{base}/week/1").text
    assert "Weekly review" in page and "2 overlapping" in page and "4 sessions" in page
    assert "overlaps Admin" in page and "overlaps Study" in page
    assert client.get(f"{base}/week/14").status_code == 404

    # The feed: secret link, no login, this week only (today is before the Sunday review).
    token = repo.calendar_token
    assert f"webcal://localhost/calendar/{token}.ics" in client.get("/settings").text
    calendar_app = client.application.test_client()
    assert calendar_app.get("/calendar/wrong.ics").status_code == 404
    feed = calendar_app.get(f"/calendar/{token}.ics")
    assert feed.status_code == 200 and feed.mimetype == "text/calendar"
    assert feed.text.count("BEGIN:VEVENT") == 4
    assert "SUMMARY:Study" in feed.text and "TRIGGER:-PT10M" in feed.text

    post(client, "/settings/calendar/reset")
    assert calendar_app.get(f"/calendar/{token}.ics").status_code == 404


def test_clash_hints_and_plan_warnings(client, db_path):
    login(client)
    post(client, "/setup", {"week_start": "0", "long_term": "", "three_year": ""})
    post(client, "/cycles/new", {"name": "Q4", "start": "2026-10-12"})
    repo = repo_for(db_path)
    cycle = repo.current_cycle()
    base = f"/c/{cycle.id}"
    post(client, f"{base}/goals/new", {"title": "Fitness"})
    goal = repo.list_goals(cycle.id)[0]
    gym = {
        "title": "Gym",
        "times": "1",
        "start": "7",
        "end": "12",
        "slot_day": ["0"],
        "slot_time": ["07:00"],
        "slot_minutes": ["60"],
    }
    post(client, f"{base}/goals/{goal.id}/tactics/new", gym)
    assert f'data-check-url="{base}/schedule/check"' in client.get(f"{base}/goals/{goal.id}/tactics/new").text

    def check(start, end, times, owner="tactic", owner_id=""):
        form = {
            "owner": owner,
            "owner_id": owner_id,
            "start": start,
            "end": end,
            "slot_day": ["0"] * len(times),
            "slot_time": times,
            "slot_minutes": ["60"] * len(times),
        }
        response = post(client, f"{base}/schedule/check", form)
        assert response.status_code == 200
        return response.get_json()["rows"]

    # A new tactic at 07:30 clashes with Gym in weeks 7–12; the blank row and a clear row get nothing.
    rows = check("1", "12", ["07:30", "", "18:00"])
    assert rows[1] is None and rows[2] is None
    assert rows[0]["clashes"] == ["Overlaps Gym · Mon 07:00–08:00 · weeks 7–12"]
    assert (rows[0]["earlier"], rows[0]["later"]) == ("06:00", "08:00")
    assert check("1", "6", ["07:30"]) == [None]  # no shared weeks
    assert check("1", "", ["07:30"])[0]["clashes"]  # mid-typing week range: falls back to 1–12
    assert check("", "", ["07:15", "07:45"], owner="block")[1]["clashes"] == [
        "Overlaps slot 1 · Mon 07:15–08:15",
        "Overlaps Gym · Mon 07:00–08:00 · weeks 7–12",
    ]
    gym_id = repo.list_tactics(cycle.id)[0].id
    assert check("7", "12", ["07:00"], owner_id=str(gym_id)) == [None]  # Gym doesn't clash with itself
    assert client.post(f"{base}/schedule/check", data={"owner": "tactic"}).status_code == 400  # no CSRF token

    # Saved anyway: the Plan page lists the pair once, linking to the first item.
    study = {
        "title": "Study",
        "times": "2",
        "start": "1",
        "end": "12",
        "slot_day": ["0", "1"],
        "slot_time": ["07:30", "07:30"],
        "slot_minutes": ["60", "60"],
    }
    post(client, f"{base}/goals/{goal.id}/tactics/new", {**study, "slot_day": ["0", "0"]})
    page = client.get(f"{base}/plan").text
    assert "“Gym” overlaps “Study” on Mon (weeks 7–12)." in page
    assert "“Study” overlaps itself on Mon (weeks 1–12)." in page
    assert f'href="{base}/tactics/{gym_id}/edit">“Gym” overlaps' in page

    repo.set_cycle_status(cycle.id, "closed")
    assert post(client, f"{base}/schedule/check", {"owner": "tactic"}).status_code == 403


def test_suggestion_hours_setting(client, db_path):
    login(client)
    repo = repo_for(db_path)
    assert repo.day_window == (360, 1320)
    assert 'value="06:00"' in client.get("/settings").text
    form = {"week_start": "0", "max_weekly_items": "25", "alert_minutes": "10"}
    response = post(client, "/settings", {**form, "day_start": "20:00", "day_end": "08:00"})
    assert response.status_code == 200 and "30 minutes before" in response.text
    assert post(client, "/settings", {**form, "day_start": "08:00", "day_end": "20:00"}).status_code == 302
    assert repo.day_window == (480, 1200)


def test_today_logging_and_daily_reflection(client, db_path):
    login(client)
    repo = repo_for(db_path)
    repo.set_setting("week_start", "0")
    cycle = repo.create_cycle("Autumn", date(2026, 10, 5), 0)
    repo.set_cycle_status(cycle.id, "active")
    goal = repo.save_goal(Goal(None, cycle.id, "Fit"))
    run = repo.save_tactic(Tactic(None, goal.id, "Run", times_per_week=3))
    base = f"/c/{cycle.id}"

    assert client.get(f"{base}/today").location == f"{base}/day/2026-10-14"
    assert client.get(f"{base}/day/2026-10-15").status_code == 404  # tomorrow
    assert client.get(f"{base}/day/2026-10-04").status_code == 404  # before the cycle
    assert client.get(f"{base}/day/nonsense").status_code == 404

    # Without JavaScript: post and come back to the row.
    response = post(client, f"{base}/day/2026-10-14/log", {"tactic_id": str(run.id), "delta": "1"})
    assert response.location == f"{base}/day/2026-10-14#t-{run.id}"
    # With JavaScript: a JSON answer for updating the row in place.
    data = post(
        client,
        f"{base}/day/2026-10-13/log",
        {"tactic_id": str(run.id), "delta": "1"},
        headers={"Accept": "application/json"},
    ).json
    assert data == {"today": 1, "week": 2, "progress": "67%", "band": "warn"}
    post(client, f"{base}/day/2026-10-13/log", {"tactic_id": str(run.id), "delta": "-1"})
    assert repo.logged_between(cycle.id, date(2026, 10, 12), date(2026, 10, 18)) == {run.id: 1}
    assert post(client, f"{base}/day/2026-10-14/log", {"tactic_id": "999"}).status_code == 404
    assert "<span data-week>1</span> / 3 this week" in client.get(f"{base}/day/2026-10-14").text

    # Daily reflection with a rating prompt added in Settings.
    post(client, "/settings/prompts", {"scope": "daily", "label": "Energy", "kind": "rating"})
    energy = repo.list_prompts("daily")[-1]
    went_well = repo.list_prompts("daily")[0]
    response = post(client, f"{base}/day/2026-10-14", {f"p-{energy.id}": "7"})
    assert response.status_code == 422 and "rating from 1 to 5" in response.text
    response = post(client, f"{base}/day/2026-10-14", {f"p-{went_well.id}": "Ran early", f"p-{energy.id}": "4"})
    assert response.location == f"{base}/day/2026-10-14#reflection"
    review = repo.get_day_review(cycle.id, date(2026, 10, 14))
    assert [(a.label, a.value) for a in review.answers] == [("What went well?", "Ran early"), ("Energy", "4")]
    page = client.get(f"{base}/day/2026-10-14").text
    assert "✓ saved" in page and 'value="4" checked' in page


def test_settings_prompts(client, db_path):
    login(client)
    repo = repo_for(db_path)
    page = client.get("/settings").text
    assert "Reflection prompts" in page and "One fix for next week" in page

    response = post(client, "/settings/prompts", {"scope": "weekly", "label": "Slept well?", "kind": "yesno"})
    slept = repo.list_prompts("weekly")[-1]
    assert response.location == f"/settings#prompt-{slept.id}" and slept.kind == "yesno"
    assert post(client, "/settings/prompts", {"scope": "monthly", "label": "x"}).status_code == 400
    post(client, "/settings/prompts", {"scope": "weekly", "label": " "})
    assert len(repo.list_prompts("weekly")) == 3  # blank label rejected

    post(client, f"/settings/prompts/{slept.id}", {"label": "Slept 7h+?", "kind": "yesno"})
    post(client, f"/settings/prompts/{slept.id}/move", {"delta": "-1"})
    post(client, f"/settings/prompts/{slept.id}/toggle")
    assert [(p.label, p.active) for p in repo.list_prompts("weekly")] == [
        ("What got in the way?", True),
        ("Slept 7h+?", False),
        ("One fix for next week", True),
    ]
    post(client, f"/settings/prompts/{slept.id}/delete")
    assert len(repo.list_prompts("weekly")) == 2
    assert post(client, f"/settings/prompts/{slept.id}/delete").status_code == 404
