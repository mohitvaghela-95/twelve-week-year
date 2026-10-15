import sqlite3
from dataclasses import replace
from datetime import date

import pytest

from twy.db import DEFAULT_PROMPTS, MIGRATIONS, connect, migrate
from twy.models import (
    Answer,
    Block,
    CycleReview,
    DayReview,
    Goal,
    Prompt,
    Slot,
    Tactic,
    WeekEntry,
    WeekItem,
    WeekReview,
)
from twy.repo import Repo

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


def test_migration_v5_drops_tactic_slots_and_keeps_block_slots(tmp_path):
    path = tmp_path / "v4.db"
    old = sqlite3.connect(path)
    old.execute("PRAGMA foreign_keys = ON")
    for script in MIGRATIONS[:4]:
        old.executescript(script)
    old.execute("PRAGMA user_version = 4")
    old.executescript(
        """
        INSERT INTO cycle (id, name, start_date, week_start) VALUES (1, 'Q4', '2026-10-05', 0);
        INSERT INTO goal (id, cycle_id, title) VALUES (1, 1, 'Fit');
        INSERT INTO tactic (id, goal_id, title, times_per_week) VALUES (1, 1, 'Run', 3);
        INSERT INTO block (id, cycle_id, title, kind) VALUES (1, 1, 'Admin', 'admin');
        INSERT INTO slot (tactic_id, weekday, start_min, duration_min) VALUES (1, 0, 420, 60);
        INSERT INTO slot (block_id, weekday, start_min, duration_min) VALUES (1, 4, 960, 30);
        """
    )
    old.commit()
    old.close()
    repo = Repo(connect(path))
    assert repo.conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    assert [s.label for s in repo.get_block(1).slots] == ["Fri 16:00–16:30"]
    assert repo.conn.execute("SELECT COUNT(*) FROM slot").fetchone()[0] == 1
    assert repo.planned_weeks(1) == set()


def test_blocks_and_copy_plan(repo):
    cycle = repo.create_cycle("Q4", MON, 0)
    goal = repo.save_goal(Goal(id=None, cycle_id=cycle.id, title="AI engineer offer"))
    repo.save_tactic(Tactic(id=None, goal_id=goal.id, title="Study", times_per_week=5))

    block = repo.save_block(Block(id=None, cycle_id=cycle.id, title="Admin", kind="admin", slots=[Slot(4, 960, 30)]))
    block.slots = [Slot(5, 600, 60)]
    repo.save_block(block)
    assert [s.label for s in repo.get_block(block.id).slots] == ["Sat 10:00–11:00"]
    repo.replace_week_items(cycle.id, 1, "plan", [WeekItem(cycle.id, 1, "block", 5, 600, 60, block_id=block.id)])

    nxt = repo.create_cycle("Q1", date(2027, 1, 4), 0)
    repo.copy_plan(cycle.id, nxt.id)
    assert [t.title for t in repo.list_tactics(nxt.id)] == ["Study"]
    assert [(b.title, b.kind, len(b.slots)) for b in repo.list_blocks(nxt.id)] == [("Admin", "admin", 1)]
    assert repo.planned_weeks(nxt.id) == set()  # week plans aren't copied

    repo.delete_block(block.id)
    assert repo.conn.execute("SELECT COUNT(*) FROM slot").fetchone()[0] == 1  # the copy's


def test_week_items_round_trip(repo):
    cycle, _goal, run, _log = make_plan(repo)
    admin = repo.save_block(Block(id=None, cycle_id=cycle.id, title="Admin", kind="admin", slots=[Slot(4, 960, 30)]))
    items = [
        WeekItem(cycle.id, 2, "tactic", 2, 1080, 45, tactic_id=run.id),
        WeekItem(cycle.id, 2, "tactic", 0, 420, 30, tactic_id=run.id),
        WeekItem(cycle.id, 2, "block", 4, 960, 30, block_id=admin.id, orig_weekday=4, orig_start_min=960),
        WeekItem(cycle.id, 2, "tactic", 2, None, None, tactic_id=run.id),  # Wednesday, no set time
    ]
    assert repo.planned_weeks(cycle.id) == set()
    repo.replace_week_items(cycle.id, 2, "plan", items)
    assert repo.planned_weeks(cycle.id) == {2}
    saved = repo.week_items(cycle.id, 2)
    assert [(i.title, i.weekday, i.start_min, i.duration_min) for i in saved] == [
        ("Run", 0, 420, 30),
        ("Run", 2, None, None),
        ("Run", 2, 1080, 45),
        ("Admin", 4, 960, 30),
    ]
    assert not saved[1].timed and saved[2].timed
    assert repo.week_items(cycle.id, 2, "actual") == []

    # An empty plan still counts as planned; replacing a week leaves other weeks alone.
    repo.replace_week_items(cycle.id, 3, "plan", [])
    assert repo.planned_weeks(cycle.id) == {2, 3}

    # Titles follow the tactic while it exists, and keep the snapshot once it's deleted.
    run.title = "Run 5k"
    repo.save_tactic(run)
    assert repo.week_items(cycle.id, 2)[0].title == "Run 5k"
    repo.delete_tactic(run.id)
    orphan = repo.week_items(cycle.id, 2)[0]
    assert (orphan.tactic_id, orphan.title) == (None, "Run")

    repo.prune_plan_rows(cycle.id, "block", admin.id, [2])
    assert [i.kind for i in repo.week_items(cycle.id, 2)] == ["tactic", "tactic", "tactic"]


def test_resync_block_rows_keeps_one_week_changes(repo):
    cycle = repo.create_cycle("Q4", MON, 0)
    admin = repo.save_block(Block(id=None, cycle_id=cycle.id, title="Admin", kind="admin", slots=[Slot(0, 960, 30)]))
    template = WeekItem(cycle.id, 1, "block", 0, 960, 30, block_id=admin.id, orig_weekday=0, orig_start_min=960)
    moved = replace(template, weekday=1, custom=True)
    repo.replace_week_items(cycle.id, 1, "plan", [moved])
    repo.replace_week_items(cycle.id, 2, "plan", [template])

    admin.slots = [Slot(0, 960, 30), Slot(3, 960, 30)]
    repo.save_block(admin)
    repo.resync_block_rows(admin, [1, 2])
    assert [(i.weekday, i.custom) for i in repo.week_items(cycle.id, 1)] == [(1, True), (3, False)]
    assert [(i.weekday, i.custom) for i in repo.week_items(cycle.id, 2)] == [(0, False), (3, False)]


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
    old.executescript(MIGRATIONS[2])  # v3 alone: later versions replace these default prompts
    weekly = old.execute("SELECT id, label FROM prompt WHERE scope = 'weekly' ORDER BY position").fetchall()
    assert [label for _, label in weekly] == ["What got in the way?", "One fix for next week"]
    assert old.execute("SELECT COUNT(*) FROM prompt WHERE scope = 'daily'").fetchone()[0] == 3
    answers = old.execute("SELECT period, prompt_id, label, value FROM answer ORDER BY period, position").fetchall()
    assert answers == [
        ("1", weekly[0][0], "What got in the way?", "Rain"),
        ("1", weekly[1][0], "One fix for next week", "Gym"),
        ("2", weekly[1][0], "One fix for next week", "Sleep"),
    ]
    old.close()


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


def test_a_new_database_starts_with_the_default_prompts(repo):
    prompts = [(p.scope, p.label, p.kind) for scope in ("daily", "weekly") for p in repo.list_prompts(scope)]
    assert prompts == DEFAULT_PROMPTS


def test_migration_v6_swaps_untouched_default_prompts(tmp_path):
    path = tmp_path / "v5.db"
    old = sqlite3.connect(path)
    for script in MIGRATIONS[:5]:
        old.executescript(script)
    old.execute("PRAGMA user_version = 5")
    old.executescript(
        "INSERT INTO cycle (id, name, start_date, week_start) VALUES (1, 'Q4', '2026-10-05', 0);"
        "INSERT INTO week_review (cycle_id, week_no) VALUES (1, 1);"
        "UPDATE prompt SET label = 'What went really well?' WHERE label = 'What went well?';"  # edited: kept
        "UPDATE prompt SET active = 0 WHERE label = 'Top tactics for tomorrow';"  # hidden but untouched: goes
        "INSERT INTO prompt (scope, position, label, kind) VALUES ('daily', 3, 'Energy', 'rating');"
        "INSERT INTO prompt (scope, position, label, kind) VALUES ('weekly', 2, 'What will I do differently next week?', 'text');"
        "INSERT INTO answer (cycle_id, scope, period, prompt_id, position, label, kind, value) "
        "SELECT 1, 'weekly', '1', id, 0, label, kind, 'Rain' FROM prompt WHERE label = 'One fix for next week';"
    )
    old.commit()
    old.close()

    repo = Repo(connect(path))
    assert [(p.label, p.kind) for p in repo.list_prompts("daily")] == [
        ("Did I review my weekly plan this morning?", "yesno"),
        ("What got in the way of today's tactics?", "text"),
        ("Did I keep the commitments I made to myself today?", "yesno"),
        ("What went really well?", "text"),
        ("Energy", "rating"),
    ]
    # The prompt already there with a new default's wording is kept, not added twice.
    assert [p.label for p in repo.list_prompts("weekly")] == [
        "What did I accomplish this week?",
        "If I executed under 85%, what got in the way?",
        "Are my tactics moving my lag measures, or do they need to change?",
        "What will I do differently next week?",
    ]
    answers = repo.get_week_review(1, 1).answers
    assert [(a.prompt_id, a.label, a.value) for a in answers] == [(None, "One fix for next week", "Rain")]


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
        "Did I review my weekly plan this morning?",
        "What got in the way of today's tactics?",
        "Did I keep the commitments I made to myself today?",
    ]
    repo.delete_prompt(energy.id)
    review = repo.get_day_review(cycle.id, day)
    assert [(a.prompt_id, a.label, a.value) for a in review.answers] == [(None, "Energy", "4")]
    assert repo.reviewed_days(cycle.id, MON, date(2026, 10, 11)) == {day}


def test_update_and_delete_cycle(repo):
    cycle, _, run, _ = make_plan(repo)
    repo.save_block(
        Block(id=None, cycle_id=cycle.id, title="Admin", slots=[Slot(weekday=0, start_min=540, duration_min=30)])
    )
    repo.log_tactic(run.id, MON, 1)
    repo.save_day_review(DayReview(cycle_id=cycle.id, day=MON))
    repo.save_cycle_review(CycleReview(cycle_id=cycle.id, lessons="x"))
    keep = repo.create_cycle("Next", date(2027, 1, 4), 0)

    repo.update_cycle(replace(cycle, name="Renamed", start_date=date(2026, 10, 4), week_start=6))
    assert repo.get_cycle(cycle.id) == replace(cycle, name="Renamed", start_date=date(2026, 10, 4), week_start=6)

    repo.delete_cycle(cycle.id)
    with pytest.raises(KeyError):
        repo.get_cycle(cycle.id)
    for table in ("goal", "tactic", "tactic_log", "block", "slot", "day_review", "cycle_review"):
        assert repo.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert [c.id for c in repo.list_cycles()] == [keep.id]


def test_cycle_has_activity(repo):
    cycle, _, run, _ = make_plan(repo)
    assert not repo.cycle_has_activity(cycle.id)
    # A week plan is stored by week and weekday, so it moves with the start date: not activity.
    repo.conn.execute(
        "INSERT INTO week_item (cycle_id, week_no, layer, kind, weekday, start_min, duration_min) "
        "VALUES (?, 1, 'plan', 'tactic', 0, 540, 30)",
        (cycle.id,),
    )
    assert not repo.cycle_has_activity(cycle.id)
    repo.log_tactic(run.id, MON, 1)
    assert repo.cycle_has_activity(cycle.id)
    repo.log_tactic(run.id, MON, -1)  # back to nothing logged
    other = repo.create_cycle("Other", date(2027, 1, 4), 0)
    repo.save_day_review(DayReview(cycle_id=other.id, day=date(2027, 1, 4)))
    assert repo.cycle_has_activity(other.id)


def test_factory_reset(repo):
    make_plan(repo)
    repo.set_setting("week_start", "6")
    repo.set_vision("long_term", "Free")
    first = repo.list_prompts("daily")[0]
    repo.delete_prompt(first.id)
    repo.save_prompt(Prompt(id=None, scope="weekly", label="Mine"))

    repo.factory_reset()
    assert not repo.is_configured and repo.list_cycles() == [] and repo.get_vision("long_term") == ""
    prompts = [(p.scope, p.label, p.kind) for scope in ("daily", "weekly") for p in repo.list_prompts(scope)]
    assert prompts == DEFAULT_PROMPTS
    assert [p.position for p in repo.list_prompts("daily")] == [0, 1, 2]


def test_migration_v7_makes_tactic_times_optional(tmp_path):
    path = tmp_path / "v6.db"
    old = sqlite3.connect(path)
    old.execute("PRAGMA foreign_keys = ON")
    for script in MIGRATIONS[:6]:
        old.executescript(script)
    old.execute("PRAGMA user_version = 6")
    old.executescript(
        """
        INSERT INTO cycle (id, name, start_date, week_start) VALUES (1, 'Q4', '2026-10-05', 0);
        INSERT INTO goal (id, cycle_id, title) VALUES (1, 1, 'Fit');
        INSERT INTO tactic (id, goal_id, title, times_per_week) VALUES (1, 1, 'Run', 3);
        INSERT INTO week_item (id, cycle_id, week_no, layer, kind, tactic_id, title, weekday, start_min, duration_min)
            VALUES (7, 1, 1, 'plan', 'tactic', 1, 'Run', 0, 420, 30);
        INSERT INTO week_item (id, cycle_id, week_no, layer, kind, tactic_id, plan_item_id, title, weekday,
            start_min, duration_min) VALUES (8, 1, 1, 'actual', 'tactic', 1, 7, 'Run', 1, 420, 30);
        """
    )
    old.commit()
    old.close()

    repo = Repo(connect(path))
    assert repo.conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    # Rows and the link from what happened to the plan survive the rebuild.
    [actual] = repo.week_items(1, 1, "actual")
    assert (actual.id, actual.plan_item_id, actual.weekday) == (8, 7, 1)
    assert repo.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    # Deleting a plan row still clears the link that points at it.
    repo.conn.execute("DELETE FROM week_item WHERE id = 7")
    assert repo.week_items(1, 1, "actual")[0].plan_item_id is None

    insert = (
        "INSERT INTO week_item (cycle_id, week_no, layer, kind, title, weekday, start_min, duration_min) "
        "VALUES (1, 1, 'plan', ?, 'x', 2, ?, ?)"
    )
    repo.conn.execute(insert, ("tactic", None, None))  # a tactic session with no set time
    with pytest.raises(sqlite3.IntegrityError):
        repo.conn.execute(insert, ("block", None, None))  # blocks and events always have a time
    with pytest.raises(sqlite3.IntegrityError):
        repo.conn.execute(insert, ("tactic", None, 30))  # a length without a start
