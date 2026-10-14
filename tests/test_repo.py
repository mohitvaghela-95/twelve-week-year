import sqlite3
from datetime import date

import pytest

from twy.db import MIGRATIONS, connect, migrate
from twy.models import Answer, Block, CycleReview, DayReview, Goal, Prompt, Slot, Tactic, WeekEntry, WeekReview

MON = date(2026, 10, 5)


def make_plan(repo):
    cycle = repo.create_cycle("Q4", MON, 0)
    goal = repo.save_goal(Goal(id=None, cycle_id=cycle.id, title="Lose 5 kg", unit="kg", baseline=80, target=75))
    run = repo.save_tactic(Tactic(id=None, goal_id=goal.id, title="Run", times_per_week=3))
    log = repo.save_tactic(Tactic(id=None, goal_id=goal.id, title="Log food"))
    return cycle, goal, run, log


def test_settings_and_vision(repo):
    assert not repo.is_configured
    repo.set_setting("week_start", "6")
    assert repo.is_configured and repo.week_start == 6
    repo.set_vision("long_term", "Healthy and free")
    repo.set_vision("long_term", "Healthier")
    assert repo.get_vision("long_term") == "Healthier"
    assert repo.get_vision("three_year") == ""


def test_cycle_goal_tactic_round_trip(repo):
    cycle, goal, run, log = make_plan(repo)
    assert repo.current_cycle().id == cycle.id
    assert [g.title for g in repo.list_goals(cycle.id)] == ["Lose 5 kg"]
    assert [t.title for t in repo.list_tactics(cycle.id)] == ["Run", "Log food"]

    repo.move_tactic(log.id, -1)
    assert [t.title for t in repo.list_tactics(cycle.id)] == ["Log food", "Run"]

    run.times_per_week = 4
    repo.save_tactic(run)
    assert repo.get_tactic(run.id).times_per_week == 4

    repo.delete_goal(goal.id)
    assert repo.list_tactics(cycle.id) == []


def test_week_review_snapshot_and_scores(repo):
    cycle, goal, run, log = make_plan(repo)
    review = WeekReview(
        cycle_id=cycle.id,
        week_no=1,
        answers=[Answer(None, "What got in the way?", "text", "Travel")],
        entries=[WeekEntry(cycle.id, 1, run.id, 3, 2), WeekEntry(cycle.id, 1, log.id, 1, 1)],
    )
    repo.save_week_review(review, progress={goal.id: 79.0})
    assert repo.weekly_scores(cycle.id) == {1: 0.75}
    assert repo.latest_progress(goal.id) == 79.0
    assert [a.value for a in repo.get_week_review(cycle.id, 1).answers] == ["Travel"]

    # Editing the tactic later does not rewrite the snapshot.
    run.times_per_week = 5
    repo.save_tactic(run)
    planned = {e.tactic_id: e.planned for e in repo.get_week_review(cycle.id, 1).entries}
    assert planned == {run.id: 3, log.id: 1}
    assert repo.weekly_scores(cycle.id) == {1: 0.75}

    # Re-saving replaces the week, and None clears a lag reading.
    review.entries = [WeekEntry(cycle.id, 1, run.id, 3, 3), WeekEntry(cycle.id, 1, log.id, 1, 1)]
    repo.save_week_review(review, progress={goal.id: None})
    assert repo.weekly_scores(cycle.id) == {1: 1.0}
    assert repo.latest_progress(goal.id) is None
    assert repo.reviewed_weeks(cycle.id) == {1}

    # A reviewed week with nothing planned has no score.
    repo.save_week_review(WeekReview(cycle.id, 2))
    assert repo.weekly_scores(cycle.id) == {1: 1.0, 2: None}


def test_deleting_a_tactic_keeps_reviewed_scores(repo):
    cycle, goal, run, log = make_plan(repo)
    entries = [WeekEntry(cycle.id, 1, run.id, 3, 0), WeekEntry(cycle.id, 1, log.id, 1, 1)]
    repo.save_week_review(WeekReview(cycle.id, 1, entries=entries))
    assert repo.weekly_scores(cycle.id) == {1: 0.25}

    repo.delete_tactic(run.id)
    assert repo.weekly_scores(cycle.id) == {1: 0.25}
    kept = repo.get_week_review(cycle.id, 1).entries
    assert [(e.tactic_id, e.title, e.planned, e.done) for e in kept] == [
        (None, "Run", 3, 0),
        (log.id, "Log food", 1, 1),
    ]

    # Deleting the whole goal keeps the history too; only deleting the cycle removes it.
    repo.delete_goal(goal.id)
    assert repo.weekly_scores(cycle.id) == {1: 0.25}


def test_close_cycle_and_copy_plan(repo):
    cycle, _goal, _run, _log = make_plan(repo)
    repo.save_cycle_review(CycleReview(cycle.id, lessons="Plan less", celebration="Dinner"))
    repo.set_cycle_status(cycle.id, "closed")
    assert repo.current_cycle() is None

    nxt = repo.create_cycle("Q1", date(2027, 1, 4), 0)
    repo.copy_plan(cycle.id, nxt.id)
    assert [t.title for t in repo.list_tactics(nxt.id)] == ["Run", "Log food"]
    assert repo.get_cycle_review(cycle.id).lessons == "Plan less"
    assert [c.name for c in repo.list_cycles()] == ["Q1", "Q4"]


def test_migration_upgrades_a_v1_database(tmp_path):
    path = tmp_path / "v1.db"
    old = sqlite3.connect(path)
    old.executescript(MIGRATIONS[0])
    old.execute("PRAGMA user_version = 1")
    old.commit()
    old.close()
    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    assert conn.execute("SELECT COUNT(*) FROM slot").fetchone()[0] == 0


def test_slots_blocks_and_copy_plan(repo):
    cycle = repo.create_cycle("Q4", MON, 0)
    goal = repo.save_goal(Goal(id=None, cycle_id=cycle.id, title="AI engineer offer"))
    study = repo.save_tactic(
        Tactic(id=None, goal_id=goal.id, title="Study", times_per_week=5), [Slot(1, 420, 60), Slot(0, 420, 60)]
    )
    assert [s.label for s in repo.tactic_slots(cycle.id)[study.id]] == ["Mon 07:00–08:00", "Tue 07:00–08:00"]

    block = repo.save_block(Block(id=None, cycle_id=cycle.id, title="Admin", kind="admin", slots=[Slot(4, 960, 30)]))
    block.slots = [Slot(5, 600, 60)]
    repo.save_block(block)
    assert [s.label for s in repo.get_block(block.id).slots] == ["Sat 10:00–11:00"]

    nxt = repo.create_cycle("Q1", date(2027, 1, 4), 0)
    repo.copy_plan(cycle.id, nxt.id)
    copied = repo.list_tactics(nxt.id)[0]
    assert len(repo.list_slots("tactic_id", copied.id)) == 2
    assert [(b.title, b.kind, len(b.slots)) for b in repo.list_blocks(nxt.id)] == [("Admin", "admin", 1)]

    repo.delete_tactic(study.id)
    assert repo.tactic_slots(cycle.id) == {}
    repo.delete_block(block.id)
    assert repo.conn.execute("SELECT COUNT(*) FROM slot WHERE block_id IS NOT NULL").fetchone()[0] == 1  # the copy's


def test_calendar_token_is_stable_until_rotated(repo):
    token = repo.calendar_token
    assert len(token) >= 32 and repo.calendar_token == token
    assert repo.rotate_calendar_token() != token


def test_migration_v3_moves_weekly_notes_into_answers(tmp_path):
    path = tmp_path / "v2.db"
    old = sqlite3.connect(path)
    for script in MIGRATIONS[:2]:
        old.executescript(script)
    old.execute("PRAGMA user_version = 2")
    old.execute("INSERT INTO cycle (id, name, start_date, week_start) VALUES (1, 'Q4', '2026-10-05', 0)")
    old.execute(
        "INSERT INTO week_review (cycle_id, week_no, obstacles, fix) VALUES (1, 1, 'Rain', 'Gym'), (1, 2, '', 'Sleep')"
    )
    old.commit()
    old.close()

    from twy.repo import Repo

    repo = Repo(connect(path))
    weekly = repo.list_prompts("weekly")
    assert [p.label for p in weekly] == ["What got in the way?", "One fix for next week"]
    assert len(repo.list_prompts("daily")) == 3
    assert [(a.prompt_id, a.value) for a in repo.get_week_review(1, 1).answers] == [
        (weekly[0].id, "Rain"),
        (weekly[1].id, "Gym"),
    ]
    assert [a.label for a in repo.get_week_review(1, 2).answers] == ["One fix for next week"]


def test_migration_v4_snapshots_titles_and_unlinks_deleted_tactics(tmp_path):
    path = tmp_path / "v3.db"
    old = sqlite3.connect(path)
    for script in MIGRATIONS[:3]:
        old.executescript(script)
    old.execute("PRAGMA user_version = 3")
    old.executescript(
        "INSERT INTO cycle (id, name, start_date, week_start) VALUES (1, 'Q4', '2026-10-05', 0);"
        "INSERT INTO goal (id, cycle_id, title) VALUES (1, 1, 'Fit');"
        "INSERT INTO tactic (id, goal_id, title, times_per_week) VALUES (1, 1, 'Run', 3);"
        "INSERT INTO week_review (cycle_id, week_no) VALUES (1, 1);"
        "INSERT INTO week_entry (cycle_id, week_no, tactic_id, planned, done) VALUES (1, 1, 1, 3, 2);"
    )
    old.commit()
    old.close()

    from twy.repo import Repo

    repo = Repo(connect(path))
    assert [(e.tactic_id, e.title, e.done) for e in repo.get_week_review(1, 1).entries] == [(1, "Run", 2)]
    repo.delete_tactic(1)
    assert repo.weekly_scores(1) == {1: 2 / 3}


def test_a_failed_migration_leaves_nothing_behind(monkeypatch):
    conn = sqlite3.connect(":memory:")
    broken = "CREATE TABLE half_done (id INTEGER); INSERT INTO no_such_table VALUES (1);"
    monkeypatch.setattr("twy.db.MIGRATIONS", [broken])
    with pytest.raises(sqlite3.OperationalError):
        migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'half_done'").fetchone() is None


def test_tactic_logs(repo):
    cycle, _goal, run, log = make_plan(repo)
    tue, wed = date(2026, 10, 6), date(2026, 10, 7)
    assert repo.log_tactic(run.id, tue, 1) == 1
    assert repo.log_tactic(run.id, tue, 1) == 2
    assert repo.log_tactic(run.id, wed, 1) == 1
    assert repo.log_tactic(log.id, wed, -1) == 0  # never below zero
    assert repo.logged_between(cycle.id, MON, date(2026, 10, 11)) == {run.id: 3}
    assert repo.logged_between(cycle.id, wed, wed) == {run.id: 1}
    assert repo.log_tactic(run.id, wed, -1) == 0
    assert repo.logged_between(cycle.id, wed, wed) == {}


def test_prompts_and_day_reviews_keep_past_wording(repo):
    cycle, *_ = make_plan(repo)
    energy = repo.save_prompt(Prompt(None, "daily", "Energy", "rating"))
    assert [p.label for p in repo.list_prompts("daily")][-1] == "Energy"
    repo.move_prompt(energy.id, -1)
    assert [p.label for p in repo.list_prompts("daily")][-2] == "Energy"

    day = date(2026, 10, 6)
    repo.save_day_review(DayReview(cycle.id, day, answers=[Answer(energy.id, "Energy", "rating", "4")]))
    repo.save_prompt(Prompt(energy.id, "daily", "Energy level", "rating", active=False))
    assert [p.label for p in repo.list_prompts("daily", active_only=True)] == [
        "What went well?",
        "What got in the way?",
        "Top tactics for tomorrow",
    ]
    repo.delete_prompt(energy.id)
    review = repo.get_day_review(cycle.id, day)
    assert [(a.prompt_id, a.label, a.value) for a in review.answers] == [(None, "Energy", "4")]
    assert repo.reviewed_days(cycle.id, MON, date(2026, 10, 11)) == {day}
