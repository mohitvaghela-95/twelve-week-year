from datetime import date, timedelta

import pytest

from twy.models import Answer, Block, Goal, Prompt, Tactic
from twy.services import (
    FormError,
    create_cycle,
    cycle_status,
    default_review_week,
    loggable,
    parse_alert_minutes,
    parse_answers,
    parse_block_form,
    parse_day_window,
    parse_goal_form,
    parse_max_weekly_items,
    parse_prompt_form,
    parse_slot_row,
    parse_slots,
    parse_tactic_form,
    reflection_items,
    review_rows,
    reviewable_weeks,
    save_week,
    save_week13,
    snap_start,
    today_rows,
    week_entries,
    week_progress,
)

MON = date(2026, 10, 5)


def make_plan(repo):
    cycle = repo.create_cycle("Q4", MON, 0)
    goal = repo.save_goal(Goal(id=None, cycle_id=cycle.id, title="Lose 5 kg", unit="kg", baseline=80, target=75))
    books = repo.save_goal(Goal(id=None, cycle_id=cycle.id, title="Ship it"))
    run = repo.save_tactic(Tactic(id=None, goal_id=goal.id, title="Run", times_per_week=3))
    late = repo.save_tactic(Tactic(id=None, goal_id=books.id, title="Launch", start_week=10, end_week=12))
    return cycle, goal, books, run, late


def test_snap_start_moves_to_week_start():
    assert snap_start("2026-10-07", 0) == date(2026, 10, 12)  # Wed -> next Mon
    assert snap_start("2026-10-05", 0) == MON
    assert snap_start("2026-10-07", 6) == date(2026, 10, 11)
    assert snap_start("not a date", 0) is None


def test_review_rows_respect_week_range_and_snapshot(repo):
    cycle, goal, _books, run, _late = make_plan(repo)
    assert [r.title for r in review_rows(repo, cycle, 1)] == ["Run"]
    assert [r.title for r in review_rows(repo, cycle, 10)] == ["Run", "Launch"]

    rows = review_rows(repo, cycle, 1)
    save_week(repo, cycle, 1, week_entries(cycle.id, 1, rows, {run.id: 5}), [], {})
    assert repo.get_cycle(cycle.id).status == "active"  # first review starts the cycle

    # A tactic added after week 1 was reviewed doesn't appear in week 1's snapshot.
    repo.save_tactic(Tactic(id=None, goal_id=goal.id, title="Stretch"))
    rows = review_rows(repo, cycle, 1)
    assert [(r.title, r.planned, r.done) for r in rows] == [("Run", 3, 3)]  # done clamped to planned


def test_default_review_week_and_status(repo):
    cycle, *_ = make_plan(repo)
    repo.set_cycle_status(cycle.id, "active")
    cycle = repo.get_cycle(cycle.id)
    today = MON + timedelta(days=16)  # week 3, weeks 1-2 finished
    assert reviewable_weeks(cycle, today) == [1, 2, 3]
    assert default_review_week(repo, cycle, today) == 1
    status = cycle_status(repo, cycle, today)
    assert status.banner == "pending" and status.pending == [1, 2]
    assert status.where.startswith("Week 3 of 12")
    assert reviewable_weeks(cycle, date(2026, 10, 1)) == []


def test_parse_goal_form():
    blank = Goal(id=None, cycle_id=1, title="")
    goal = parse_goal_form(blank, {"title": " Lose 5 kg ", "unit": "kg", "baseline": "80", "target": "75"})
    assert (goal.title, goal.unit, goal.baseline, goal.target) == ("Lose 5 kg", "kg", 80, 75)
    with pytest.raises(FormError, match="title is required"):
        parse_goal_form(blank, {"title": " "})
    with pytest.raises(FormError, match="must be numbers"):
        parse_goal_form(blank, {"title": "x", "target": "lots"})
    with pytest.raises(FormError, match="Add a target"):
        parse_goal_form(blank, {"title": "x", "baseline": "3"})


def test_parse_tactic_form():
    blank = Tactic(id=None, goal_id=1, title="")
    tactic = parse_tactic_form(blank, {"title": "Run", "times": "3", "start": "", "end": "8"})
    assert (tactic.times_per_week, tactic.start_week, tactic.end_week) == (3, 1, 8)
    with pytest.raises(FormError, match="at least 1"):
        parse_tactic_form(blank, {"title": "Run", "times": "0"})
    with pytest.raises(FormError, match="1 ≤ from ≤ to ≤ 12"):
        parse_tactic_form(blank, {"title": "Run", "start": "9", "end": "3"})
    with pytest.raises(FormError, match="whole numbers"):
        parse_tactic_form(blank, {"title": "Run", "times": "two"})


def test_save_week13_outcomes_and_validation(repo):
    cycle, goal, books, *_ = make_plan(repo)
    with pytest.raises(FormError, match="Lose 5 kg"):
        save_week13(repo, cycle, {goal.id: "abc"}, {books.id: "hit"}, "", "")
    assert repo.get_goal(books.id).outcome is None  # nothing written on error

    save_week13(repo, cycle, {goal.id: "74.5"}, {books.id: "missed"}, " Start earlier ", "Dinner")
    assert repo.get_goal(goal.id).outcome == "hit"  # 80 -> 75 is decreasing, 74.5 reaches it
    assert repo.get_goal(books.id).outcome == "missed"
    assert repo.get_cycle_review(cycle.id).lessons == "Start earlier"
    assert repo.get_cycle(cycle.id).status == "review"


def test_create_cycle_copies_plan(repo):
    cycle, *_ = make_plan(repo)
    repo.set_cycle_status(cycle.id, "closed")
    nxt = create_cycle(repo, "Q1", date(2027, 1, 4), copy_from=cycle.id)
    assert [g.title for g in repo.list_goals(nxt.id)] == ["Lose 5 kg", "Ship it"]
    assert [t.title for t in repo.list_tactics(nxt.id)] == ["Run", "Launch"]


def test_parse_max_weekly_items():
    assert parse_max_weekly_items(" 30 ") == 30
    for bad in ("0", "abc", "", "500", "2.5"):
        with pytest.raises(FormError, match="Max items per week"):
            parse_max_weekly_items(bad)


def test_parse_slots():
    slots = parse_slots(["4", "0", "2"], ["18:00:00", "", "07:30"], ["30", "60", "45"])
    assert [(s.weekday, s.start_min, s.duration_min) for s in slots] == [(2, 450, 45), (4, 1080, 30)]
    cases = [
        (["0"], ["7am"], ["60"], "Slot 1: use a time"),
        (["0", "9"], ["07:00", "08:00"], ["60", "60"], "Slot 2: pick a day"),
        (["0"], ["07:00"], ["3"], "5–720"),
        (["0"], ["23:30"], ["60"], "midnight"),
    ]
    for days, times, minutes, message in cases:
        with pytest.raises(FormError, match=message):
            parse_slots(days, times, minutes)


def test_parse_alert_minutes():
    assert parse_alert_minutes("0") == 0
    assert parse_alert_minutes(" 15 ") == 15
    for bad in ("", "x", "-5", "1441"):
        with pytest.raises(FormError, match="Alert"):
            parse_alert_minutes(bad)


def test_parse_block_form():
    block = Block(id=None, cycle_id=1, title="")
    slots = parse_slots(["6"], ["18:00"], ["30"])
    parsed = parse_block_form(block, {"title": " Weekly review ", "kind": "review"}, slots)
    assert (parsed.title, parsed.kind, len(parsed.slots)) == ("Weekly review", "review", 1)
    with pytest.raises(FormError, match="title"):
        parse_block_form(block, {"title": "", "kind": "admin"}, slots)
    with pytest.raises(FormError, match="kind"):
        parse_block_form(block, {"title": "x", "kind": "nap"}, slots)
    with pytest.raises(FormError, match="time slot"):
        parse_block_form(block, {"title": "x", "kind": "admin"}, [])


def test_parse_slot_row():
    slot = parse_slot_row("2", "07:30", "45")
    assert (slot.weekday, slot.start_min, slot.duration_min) == (2, 450, 45)
    with pytest.raises(FormError, match="^pick a day"):
        parse_slot_row("", "07:30", "45")


def test_parse_day_window():
    assert parse_day_window("06:00", "22:00") == (360, 1320)
    for start, end in (("6am", "22:00"), ("06:00", ""), ("22:00", "06:00"), ("06:00", "06:20")):
        with pytest.raises(FormError, match="Suggestion hours|Use times"):
            parse_day_window(start, end)


def test_reflection_items_keep_saved_wording_and_add_new_prompts():
    prompts = [Prompt(1, "daily", "Went well?"), Prompt(2, "daily", "Energy", "rating")]
    assert [(i.key, i.label) for i in reflection_items(prompts, [])] == [("p-1", "Went well?"), ("p-2", "Energy")]

    saved = [Answer(2, "Energy (old)", "rating", "3"), Answer(None, "Deleted prompt", "text", "Hi")]
    items = reflection_items(prompts, saved)
    assert [(i.key, i.label, i.value) for i in items] == [
        ("p-1", "Went well?", ""),
        ("p-2", "Energy (old)", "3"),
        ("a-1", "Deleted prompt", "Hi"),
    ]
    hidden = reflection_items(prompts[:1], saved)  # Energy hidden since: its answer moves to the end
    assert [i.key for i in hidden] == ["p-1", "p-2", "a-1"]


def test_parse_answers_validates_kinds_and_skips_blanks():
    prompts = [
        Prompt(1, "daily", "Notes"),
        Prompt(2, "daily", "Energy", "rating"),
        Prompt(3, "daily", "Planned?", "yesno"),
    ]
    items = reflection_items(prompts, [])
    answers = parse_answers(items, {"p-1": "  ", "p-2": "4", "p-3": "yes"})
    assert [(a.prompt_id, a.value) for a in answers] == [(2, "4"), (3, "yes")]
    with pytest.raises(FormError, match="Energy"):
        parse_answers(items, {"p-2": "9"})
    assert items[1].value == "9"  # kept for re-showing the form
    with pytest.raises(FormError, match="yes or no"):
        parse_answers(items, {"p-3": "maybe"})


def test_parse_prompt_form():
    prompt = parse_prompt_form(Prompt(None, "weekly", ""), {"label": " Energy ", "kind": "rating"})
    assert (prompt.label, prompt.kind) == ("Energy", "rating")
    with pytest.raises(FormError):
        parse_prompt_form(prompt, {"label": ""})
    with pytest.raises(FormError):
        parse_prompt_form(prompt, {"label": "x", "kind": "essay"})


def test_today_rows_and_progress(repo):
    cycle, _goal, _books, run, _late = make_plan(repo)
    tue, wed = MON + timedelta(days=1), MON + timedelta(days=2)
    assert loggable(cycle, tue, wed) and not loggable(cycle, wed + timedelta(days=1), wed)
    assert not loggable(cycle, MON - timedelta(days=1), wed)
    assert not loggable(cycle, MON + timedelta(weeks=12), MON + timedelta(weeks=13))  # week 13 isn't tracked

    for _ in range(4):
        repo.log_tactic(run.id, tue, 1)
    repo.log_tactic(run.id, wed, 1)
    rows = today_rows(repo, cycle, wed)
    assert [(r.title, r.today, r.week, r.planned) for r in rows] == [("Run", 1, 5, 3)]  # Launch starts in week 10
    assert week_progress(rows) == 1.0  # capped at the plan
