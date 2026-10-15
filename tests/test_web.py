"""Web app tests through Flask's test client, against a temporary database file."""

import sqlite3
from datetime import date

import pytest

from twy import calendars
from twy.db import connect
from twy.models import Goal, Tactic, WeekItem
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


def test_login_locks_out_after_repeated_wrong_passwords(client):
    for _ in range(5):
        assert post(client, "/login", {"password": "nope"}).status_code == 401
    response = post(client, "/login", {"password": "secret"})  # right password, but locked out for now
    assert response.status_code == 429 and "Too many wrong passwords" in response.text
    other = post(client, "/login", {"password": "secret"}, environ_base={"REMOTE_ADDR": "10.0.0.9"})
    assert other.status_code == 302  # only the guessing address is locked


def test_login_ignores_offsite_next(client):
    response = post(client, "/login?next=//evil.example/", {"password": "secret"})
    assert response.location == "/"


def test_full_cycle_flow(client, db_path):
    login(client)
    assert client.get("/").location == "/setup"

    response = post(client, "/setup", {"week_start": "0", "long_term": "Healthy", "three_year": "Fit"})
    assert response.location == "/cycles/new"
    page = client.get("/cycles/new").text
    # Wednesday is early enough to start this week (Mon 12 Oct); both weeks are one tap away.
    assert 'value="2026-10-12"' in page and 'data-set-date="2026-10-19"' in page
    assert 'aria-current="step"' in page and 'class="tabbar"' not in page  # first-run chrome

    # Start date snaps forward to the week start; an earlier Monday stays as is.
    response = post(client, "/cycles/new", {"name": "Autumn", "start": "2026-10-05"})
    assert response.status_code == 302
    repo = repo_for(db_path)
    cycle = repo.current_cycle()
    assert (cycle.name, cycle.start_date, cycle.status) == ("Autumn", date(2026, 10, 5), "planning")
    base = f"/c/{cycle.id}"

    # Invalid goal is re-shown with the shared validation message.
    response = post(client, f"{base}/goals/new", {"title": "Lose 5 kg", "baseline": "82"})
    assert response.status_code == 422 and "Add a target, or clear the starting value." in response.text

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
    response = post(client, f"{base}/review/1", {f"done-{run.id}": "2", f"lag-{goal.id}": "nan"})
    assert response.status_code == 422  # not a 500 from SQLite turning NaN into NULL
    response = post(
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
    assert response.location == f"{base}/week/2/plan"  # nothing else to score: plan this week next
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
    assert client.get("/static/manifest.json").status_code == 200
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
    assert "Heavy weeks (&gt; 4 actions)" in page and "load-cell heavy" in page


def test_plan_a_week_then_week_page_and_calendar_feed(client, db_path):
    login(client)
    post(client, "/setup", {"week_start": "0", "long_term": "", "three_year": ""})
    post(client, "/cycles/new", {"name": "Q4", "start": "2026-10-12"})  # TODAY (Wed 14 Oct) is in week 1
    repo = repo_for(db_path)
    cycle = repo.current_cycle()
    base = f"/c/{cycle.id}"
    post(client, f"{base}/goals/new", {"title": "AI engineer offer"})
    goal = repo.list_goals(cycle.id)[0]
    post(client, f"{base}/goals/{goal.id}/tactics/new", {"title": "Study", "times": "5", "start": "1", "end": "12"})
    study = repo.list_tactics(cycle.id)[0]
    assert 'name="slot_time"' not in client.get(f"{base}/tactics/{study.id}/edit").text  # no fixed times

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
    post(client, f"{base}/blocks/new", admin)
    assert [b.title for b in repo.list_blocks(cycle.id)] == ["Weekly review", "Admin"]

    # Before planning: blocks only, and a nudge to plan the week.
    assert "Week 1 isn&#39;t planned yet" in client.get(base).text
    page = client.get(f"{base}/week/1").text
    assert "Week 1 isn't planned yet" in page and "2 sessions" in page and f"{base}/week/1/plan" in page
    token = repo.calendar_token
    calendar_app = client.application.test_client()
    assert calendar_app.get(f"/calendar/{token}.ics").text.count("BEGIN:VEVENT") == 2

    # The planning page lists each tactic to place, beside what's already on.
    page = client.get(f"{base}/week/1/plan").text
    assert "Study" in page and "0</span> of 5 placed" in page and "Admin" in page
    assert "Copy week" not in page  # week 1 has no week before it
    assert client.get(f"{base}/week/3/plan").status_code == 404  # only this week and next
    assert client.get(f"{base}/week/2/plan").status_code == 200

    rows = {"slot_kind": ["tactic"] * 3, "slot_ref": [str(study.id)] * 3, "slot_minutes": ["60"] * 3}
    bad = {**rows, "slot_day": ["0", "2", "4"], "slot_time": ["07:00", "7am", ""]}
    response = post(client, f"{base}/week/1/plan", bad)
    assert response.status_code == 422 and "Study, time 2: use a time like 07:30." in response.text
    assert 'value="7am"' in response.text and repo.planned_weeks(cycle.id) == set()
    stale = {**bad, "slot_ref": ["999"] * 3}
    assert "Reload the page" in post(client, f"{base}/week/1/plan", stale).text

    good = {**rows, "slot_day": ["0", "2", "4"], "slot_time": ["07:00", "07:00", ""]}
    response = post(client, f"{base}/week/1/plan", good)
    assert response.location == f"{base}/week/1"
    page = client.get(f"{base}/week/1").text
    assert "Week 1 planned. Not all placed: Study 2 of 5." in page
    assert "Not placed this week:" in page and "Study (2 of 5)" in page
    assert "4 sessions" in page and "overlaps Admin" in page and "overlaps Study" in page
    assert "Week 1 isn&#39;t planned yet" not in client.get(base).text
    assert client.get(f"{base}/week/14").status_code == 404

    # The feed: secret link, no login, this week only (today is before the Sunday review)...
    assert f"webcal://localhost/calendar/{token}.ics" in client.get("/settings").text
    assert calendar_app.get("/calendar/wrong.ics").status_code == 404
    feed = calendar_app.get(f"/calendar/{token}.ics")
    assert feed.status_code == 200 and feed.mimetype == "text/calendar"
    assert feed.text.count("BEGIN:VEVENT") == 4
    assert "SUMMARY:Study" in feed.text and "TRIGGER:-PT10M" in feed.text
    assert f"UID:tactic-{study.id}-w1-1@twy" in feed.text

    # ...until next week is planned. Copying last week fills in its times without saving anything.
    page = client.get(f"{base}/week/2/plan?copy=prev").text
    assert "Week 1's times are filled in" in page and page.count('value="07:00"') == 2
    assert repo.planned_weeks(cycle.id) == {1}
    post(client, f"{base}/week/2/plan", {**good, "slot_time": ["06:30", "", ""]})
    assert calendar_app.get(f"/calendar/{token}.ics").text.count("BEGIN:VEVENT") == 4 + 3

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
    post(client, f"{base}/goals/{goal.id}/tactics/new", {"title": "Study", "times": "2", "start": "1", "end": "12"})
    study = repo.list_tactics(cycle.id)[0]
    gym = {"title": "Gym", "kind": "other", "slot_day": ["0"], "slot_time": ["07:00"], "slot_minutes": ["60"]}
    post(client, f"{base}/blocks/new", gym)
    gym_id = repo.list_blocks(cycle.id)[0].id
    assert f'data-check-url="{base}/schedule/check"' in client.get(f"{base}/blocks/new").text
    assert f'data-check-url="{base}/week/1/plan/check"' in client.get(f"{base}/week/1/plan").text

    def check(times, refs=None, week=1):
        form = {
            "slot_kind": ["tactic"] * len(times),
            "slot_ref": refs or [str(study.id)] * len(times),
            "slot_day": ["0"] * len(times),
            "slot_time": times,
            "slot_minutes": ["60"] * len(times),
        }
        response = post(client, f"{base}/week/{week}/plan/check", form)
        assert response.status_code == 200
        return response.get_json()

    # A session at 07:30 clashes with Gym; the blank row and a clear row get nothing.
    data = check(["07:30", "", "18:00"])
    rows = data["rows"]
    assert rows[1] is None and rows[2] is None
    assert rows[0]["clashes"] == ["Overlaps Gym · Mon 07:00–08:00"]
    assert (rows[0]["earlier"], rows[0]["later"]) == ("06:00", "08:00")
    assert data["placed"] == {str(study.id): 2}
    # Two sessions of the plan clash with each other too.
    assert check(["18:00", "18:30"])["rows"][1]["clashes"] == ["Overlaps Study · Mon 18:00–19:00"]
    assert check(["07:30"], refs=["999"])["rows"] == [None]  # not a tactic of this week

    # The block form checks against the other blocks' weekly times.
    def block_check(times, owner_id=""):
        form = {
            "owner": "block",
            "owner_id": owner_id,
            "slot_day": ["0"] * len(times),
            "slot_time": times,
            "slot_minutes": ["60"] * len(times),
        }
        return post(client, f"{base}/schedule/check", form).get_json()["rows"]

    assert block_check(["07:15", "07:45"])[1]["clashes"] == [
        "Overlaps slot 1 · Mon 07:15–08:15",
        "Overlaps Gym · Mon 07:00–08:00 · weeks 1–13",
    ]
    assert block_check(["07:00"], owner_id=str(gym_id)) == [None]  # Gym doesn't clash with itself
    assert (
        post(client, f"{base}/schedule/check", {"owner": "tactic"}).status_code == 400
    )  # tactics have no weekly times
    assert client.post(f"{base}/schedule/check", data={"owner": "block"}).status_code == 400  # no CSRF token
    assert post(client, f"{base}/week/3/plan/check", {}).status_code == 404  # not plannable yet

    # Saved anyway: the Plan page lists the pair once, linking to the first item.
    chores = {"title": "Chores", "kind": "chores", "slot_day": ["0", "0"], "slot_time": ["07:30", "07:45"]}
    post(client, f"{base}/blocks/new", {**chores, "slot_minutes": ["60", "60"]})
    page = client.get(f"{base}/plan").text
    assert "“Gym” overlaps “Chores” on Mon (weeks 1–13)." in page
    assert "“Chores” overlaps itself on Mon (weeks 1–13)." in page
    assert f'href="{base}/blocks/{gym_id}/edit">“Gym” overlaps' in page

    repo.set_cycle_status(cycle.id, "closed")
    assert post(client, f"{base}/schedule/check", {"owner": "block"}).status_code == 403
    assert post(client, f"{base}/week/1/plan/check", {}).status_code == 403


def test_week_plan_skips_moves_blocks_and_adds_events(client, db_path):
    login(client)
    repo = repo_for(db_path)
    repo.set_setting("week_start", "0")
    cycle = repo.create_cycle("Q4", date(2026, 10, 12), 0)  # TODAY (Wed 14 Oct) is in week 1
    goal = repo.save_goal(Goal(None, cycle.id, "Fit"))
    run = repo.save_tactic(Tactic(None, goal.id, "Run", times_per_week=1))
    base = f"/c/{cycle.id}"
    admin = {"title": "Admin", "kind": "admin", "slot_day": ["0", "2"], "slot_time": ["08:00"] * 2}
    post(client, f"{base}/blocks/new", {**admin, "slot_minutes": ["30", "30"]})
    admin_id = repo.list_blocks(cycle.id)[0].id

    page = client.get(f"{base}/week/1/plan").text
    assert "Usually Mon 08:00" in page and "Usually Wed 08:00" in page and "Events this week" in page

    form = {
        "slot_kind": ["tactic", "block", "block", "event", "event"],
        "slot_ref": [str(run.id), str(admin_id), str(admin_id), "", ""],
        "slot_origin": ["", "0:480", "2:480", "", ""],
        "slot_title": ["", "", "", "Dinner with Sam", ""],
        "slot_day": ["1", "0", "2", "4", "0"],
        "slot_time": ["07:00", "08:30", "", "19:00", ""],
        "slot_minutes": ["30", "30", "30", "120", "60"],
    }
    bad = {**form, "slot_title": ["", "", "", "", ""]}
    response = post(client, f"{base}/week/1/plan", bad)
    assert response.status_code == 422 and "Event 1: give it a name." in response.text
    assert "Skipped this week · usually Wed 08:00" in response.text  # the skip survives the error
    assert post(client, f"{base}/week/1/plan", form).location == f"{base}/week/1"

    page = client.get(f"{base}/week/1").text
    assert "Dinner with Sam" in page and "08:30" in page
    week = [(i.kind, i.title, i.weekday, i.start_min, i.skipped) for i in repo.week_items(cycle.id, 1)]
    assert week == [
        ("block", "Admin", 0, 510, False),
        ("tactic", "Run", 1, 420, False),
        ("block", "Admin", 2, 480, True),
        ("event", "Dinner with Sam", 4, 1140, False),
    ]
    # Re-opening the plan shows the move, the skip and the event, plus a blank row for another event.
    page = client.get(f"{base}/week/1/plan").text
    assert "Skipped this week · usually Wed 08:00" in page and 'value="Dinner with Sam"' in page

    # The calendar: Run, Monday's moved Admin and the dinner; the skipped Wednesday is left out.
    feed = client.application.test_client().get(f"/calendar/{repo.calendar_token}.ics").text
    assert feed.count("BEGIN:VEVENT") == 3
    assert "SUMMARY:Dinner with Sam" in feed and "CATEGORIES:Event" in feed
    assert f"UID:block-{admin_id}-20261012-480@twy" in feed  # moved, but still Monday's 08:00 slot

    # Changing Admin's weekly times keeps this week's skip and move.
    post(client, f"{base}/blocks/{admin_id}/edit", {**admin, "slot_minutes": ["45", "45"]})
    assert [(i.weekday, i.start_min, i.skipped) for i in repo.week_items(cycle.id, 1) if i.kind == "block"] == [
        (0, 510, False),
        (2, 480, True),
    ]


def test_plan_around_an_imported_calendar(client, db_path, monkeypatch):
    login(client)
    repo = repo_for(db_path)
    repo.set_setting("week_start", "0")
    cycle = repo.create_cycle("Q4", date(2026, 10, 12), 0)  # TODAY (Wed 14 Oct) is in week 1
    goal = repo.save_goal(Goal(None, cycle.id, "Fit"))
    run = repo.save_tactic(Tactic(None, goal.id, "Run", times_per_week=2))
    base = f"/c/{cycle.id}"
    feed = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:test\r\n"
        "BEGIN:VEVENT\r\nUID:a\r\nSUMMARY:Team offsite\r\nDTSTART:20261014T090000\r\nDURATION:PT3H\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nUID:b\r\nSUMMARY:Mum's birthday\r\nDTSTART;VALUE=DATE:20261015\r\nEND:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    fetched = []

    def fake_fetch(url):
        fetched.append(url)
        if "broken" in url:
            raise OSError("timed out")
        return feed.encode()

    monkeypatch.setattr(calendars, "fetch", fake_fetch)

    # Settings: links one per line; webcal:// is read as https://, and a bad line is refused.
    assert 'id="imports"' in client.get("/settings").text
    response = post(client, "/settings/imports", {"import_urls": "webcal://cal.test/me.ics\nnot a link"})
    assert response.status_code == 422 and "Line 2 isn&#39;t a calendar link" in response.text
    assert "not a link" in response.text and repo.import_urls == []
    links = "webcal://cal.test/me.ics\n\nhttps://broken.test/secret-token.ics"
    assert post(client, "/settings/imports", {"import_urls": links}).location == "/settings#imports"
    assert repo.import_urls == ["https://cal.test/me.ics", "https://broken.test/secret-token.ics"]

    # Shown greyed on the planning page, the Week page and Today, with the failing link named by host only.
    page = client.get(f"{base}/week/1/plan").text
    assert "Team offsite" in page and "Mum&#39;s birthday" in page and "+ your calendar" in page
    assert "Couldn&#39;t load your calendar from broken.test." in page and "secret-token" not in page
    page = client.get(f"{base}/week/1").text
    assert 'class="session kind-import' in page and "Your calendar" in page and 'class="allday small"' in page
    assert "0 sessions" in page  # imported events aren't part of the plan
    page = client.get(f"{base}/day/2026-10-14").text
    assert "Team offsite" in page
    assert fetched.count("https://cal.test/me.ics") == 1  # cached for 15 minutes

    # They count in clash checks; the all-day birthday doesn't.
    def check(day, time):
        form = {"slot_kind": ["tactic"], "slot_ref": [str(run.id)], "slot_day": [day], "slot_time": [time]}
        return post(client, f"{base}/week/1/plan/check", {**form, "slot_minutes": ["60"]}).get_json()["rows"][0]

    hint = check("2", "10:00")
    assert hint["clashes"] == ["Overlaps Team offsite · Wed 09:00–12:00"]
    assert (hint["earlier"], hint["later"]) == ("08:00", "12:00")
    assert check("3", "10:00") is None

    # And they never go into twy's own calendar feed.
    post(
        client,
        f"{base}/week/1/plan",
        {
            "slot_kind": ["tactic"],
            "slot_ref": [str(run.id)],
            "slot_day": ["2"],
            "slot_time": ["07:00"],
            "slot_minutes": ["30"],
        },
    )
    ics_text = client.application.test_client().get(f"/calendar/{repo.calendar_token}.ics").text
    assert ics_text.count("BEGIN:VEVENT") == 1 and "Team offsite" not in ics_text

    assert post(client, "/settings/imports", {"import_urls": ""}).status_code == 302
    assert repo.import_urls == [] and "Team offsite" not in client.get(f"{base}/week/1").text


def test_record_what_happened_and_compare_on_the_week_page(client, db_path):
    login(client)
    repo = repo_for(db_path)
    repo.set_setting("week_start", "0")
    cycle = repo.create_cycle("Q4", date(2026, 10, 5), 0)  # week 1 ended on Sunday; TODAY is in week 2
    goal = repo.save_goal(Goal(None, cycle.id, "Fit"))
    run = repo.save_tactic(Tactic(None, goal.id, "Run", times_per_week=2))
    base = f"/c/{cycle.id}"
    # Week 1 was planned while it was on: Run on Monday and Wednesday at 07:00.
    plan = [WeekItem(cycle.id, 1, "tactic", day, 420, 30, tactic_id=run.id) for day in (0, 2)]
    repo.replace_week_items(cycle.id, 1, "plan", plan)
    mon, wed = (i.id for i in repo.week_items(cycle.id, 1))

    page = client.get(f"{base}/week/1").text
    assert "Not recorded, so taken as planned." in page and f'href="{base}/week/1/actual"' in page
    assert client.get(f"{base}/week/2/actual").status_code == 404  # still on
    page = client.get(f"{base}/week/1/actual").text
    assert "Planned Mon 07:00" in page and "Planned Wed 07:00" in page and "1 of 2" not in page

    rows = {
        "slot_kind": ["tactic"] * 3,
        "slot_ref": [str(run.id)] * 3,
        "slot_plan": [str(mon), str(wed), ""],
        "slot_day": ["1", "2", "5"],
        "slot_time": ["07:00", "", "09:00"],
        "slot_minutes": ["30"] * 3,
    }
    stale = {**rows, "slot_plan": [str(mon), str(mon), ""]}
    response = post(client, f"{base}/week/1/actual", stale)
    assert response.status_code == 422 and "Reload the page" in response.text
    response = post(client, f"{base}/week/1/actual", rows)
    assert response.location == f"{base}/week/1"
    page = client.get(f"{base}/week/1").text
    assert "Week 1: 1 moved, 1 didn&#39;t happen, 1 added." in page  # the flash
    assert "Against the plan: 1 moved, 1 didn&#39;t happen, 1 added." in page
    assert "moved from Mon 07:00" in page and 'class="badge">added' in page
    assert 'class="session ghost kind-tactic"' in page and "Planned · didn&#39;t happen" in page
    assert "moved to Tue 07:00" in page and "Edit what happened" in page and "2 sessions" in page
    assert repo.week_items(cycle.id, 1)[0].start_min == 420  # the plan itself is untouched

    # From Review, recording comes back to the review; "Back to as planned" forgets the record.
    response = post(client, f"{base}/week/1/actual", {**rows, "next": f"{base}/review/1"})
    assert response.location == f"{base}/review/1"
    assert post(client, f"{base}/week/1/actual", {**rows, "next": "https://evil.test/"}).location == f"{base}/week/1"
    assert post(client, f"{base}/week/1/actual", {"action": "reset"}).location == f"{base}/week/1"
    assert repo.recorded_weeks(cycle.id) == set()
    assert "Not recorded, so taken as planned." in client.get(f"{base}/week/1").text

    repo.set_cycle_status(cycle.id, "closed")
    assert client.get(f"{base}/week/1/actual").status_code == 403
    assert "Record what happened" not in client.get(f"{base}/week/1").text


def test_open_plans_follow_tactic_and_block_changes(client, db_path):
    login(client)
    repo = repo_for(db_path)
    repo.set_setting("week_start", "0")
    cycle = repo.create_cycle("Q4", date(2026, 10, 5), 0)  # TODAY is in week 2; week 1 has ended
    goal = repo.save_goal(Goal(None, cycle.id, "Fit"))
    run = repo.save_tactic(Tactic(None, goal.id, "Run", times_per_week=2))
    base = f"/c/{cycle.id}"
    rows = {"slot_kind": ["tactic"], "slot_ref": [str(run.id)], "slot_day": ["1"], "slot_minutes": ["30"]}
    for week in (2, 3):
        assert post(client, f"{base}/week/{week}/plan", {**rows, "slot_time": ["07:00"]}).status_code == 302
    # Ended weeks can't be re-planned: their plan URL goes on to recording what happened.
    assert client.get(f"{base}/week/1/plan").location == f"{base}/week/1/actual"
    assert post(client, f"{base}/week/1/plan", {}).status_code == 404
    post(
        client,
        f"{base}/blocks/new",
        {"title": "Admin", "kind": "admin", "slot_day": ["4"], "slot_time": ["16:00"], "slot_minutes": ["30"]},
    )
    assert [i.title for i in repo.week_items(cycle.id, 3)] == ["Run", "Admin"]  # added to open plans

    # Run now starts in week 3: it leaves week 2's plan but stays in week 3's.
    post(client, f"{base}/tactics/{run.id}/edit", {"title": "Run", "times": "2", "start": "3", "end": "12"})
    assert [i.title for i in repo.week_items(cycle.id, 2)] == ["Admin"]
    assert [i.title for i in repo.week_items(cycle.id, 3)] == ["Run", "Admin"]
    post(client, f"{base}/goals/{goal.id}/delete")
    admin = repo.list_blocks(cycle.id)[0]
    post(client, f"{base}/blocks/{admin.id}/delete")
    assert repo.week_items(cycle.id, 3) == []


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
    page = client.get(f"{base}/day/2026-10-14").text
    assert "<span data-week>1</span> / 3 this week" in page
    # The review link points at the oldest week still to score, not the week in progress.
    assert f'href="{base}/review/1">Review week 1 ›</a>' in page
    # Home leads with today's logging.
    home = client.get(base).text
    assert "<strong>1 of 1</strong> tactics logged today." in home and "Log today ›" in home

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


def test_navigation_highlights_and_back_links(client, db_path):
    login(client)
    post(client, "/setup", {"week_start": "0", "long_term": "", "three_year": ""})
    # No cycle yet: no tabs that would only loop back home.
    home = client.get("/").text
    assert "No active cycle" in home and ">Today</span>" not in home and "Week schedule" not in client.get("/more").text
    post(client, "/cycles/new", {"name": "Q4", "start": "2026-10-05"})
    plan = client.get("/c/1/plan").text
    assert 'href="/c/1/plan" class=" on" aria-current="page"' in plan
    settings = client.get("/settings").text
    # Settings sits under More on phones and has its own sidebar item on wide screens.
    assert 'class="mobile-only on"' in settings
    assert 'class="desktop-only on" aria-current="page"><svg' in settings
    assert 'class="back mobile-only" href="/more">‹ More</a>' in settings
    goal_form = client.get("/c/1/goals/new").text
    assert 'class="back" href="/c/1/plan">‹ Plan</a>' in goal_form


def test_review_catch_up_and_plan_reorder(client, db_path):
    login(client)
    repo = repo_for(db_path)
    repo.set_setting("week_start", "0")
    cycle = repo.create_cycle("Autumn", date(2026, 9, 28), 0)  # weeks 1 and 2 are finished
    repo.set_cycle_status(cycle.id, "active")
    goal = repo.save_goal(Goal(None, cycle.id, "Fit"))
    run = repo.save_tactic(Tactic(None, goal.id, "Run", times_per_week=3))
    lift = repo.save_tactic(Tactic(None, goal.id, "Lift", times_per_week=2))
    base = f"/c/{cycle.id}"

    # Saving a week goes straight on to the next one still to score, then home.
    response = post(client, f"{base}/review/1", {f"done-{run.id}": "3", f"done-{lift.id}": "2"})
    assert response.location == f"{base}/review/2"
    page = client.get(f"{base}/review/2").text
    assert 'class="chip now"' in page and "One more: Run" in page
    # The last one goes on to planning the current week (week 3), the book's weekly routine.
    assert post(client, f"{base}/review/2", {f"done-{run.id}": "3"}).location == f"{base}/week/3/plan"

    # Reordering answers JSON for the in-place move, and redirects without JavaScript.
    response = post(client, f"{base}/tactics/{lift.id}/move", {"delta": "-1"}, headers={"Accept": "application/json"})
    assert response.json == {"ok": True}
    assert [t.title for t in repo.list_tactics(cycle.id)] == ["Lift", "Run"]
    assert post(client, f"{base}/tactics/{lift.id}/move", {"delta": "1"}).location == f"{base}/plan#tactic-{lift.id}"

    # Rows open their edit page; delete lives there.
    plan = client.get(f"{base}/plan").text
    assert f'class="row-link" href="{base}/tactics/{run.id}/edit"' in plan and "Delete tactic" not in plan
    assert f'action="{base}/tactics/{run.id}/delete"' in client.get(f"{base}/tactics/{run.id}/edit").text

    # A new cycle can't be started while this one runs, so Cycles doesn't offer it.
    assert "+ New" not in client.get("/cycles").text
