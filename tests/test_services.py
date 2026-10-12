from datetime import date, timedelta

import pytest

from twy.models import Goal, Tactic
from twy.services import (
    FormError,
    create_cycle,
    cycle_status,
    default_review_week,
    parse_goal_form,
    parse_max_weekly_items,
    parse_tactic_form,
    review_rows,
    reviewable_weeks,
    save_week,
    save_week13,
    snap_start,
    week_entries,
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
    cycle, goal, books, run, late = make_plan(repo)
    assert [r.title for r in review_rows(repo, cycle, 1)] == ["Run"]
    assert [r.title for r in review_rows(repo, cycle, 10)] == ["Run", "Launch"]

    rows = review_rows(repo, cycle, 1)
    save_week(repo, cycle, 1, week_entries(cycle.id, 1, rows, {run.id: 5}), "", "", {})
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
