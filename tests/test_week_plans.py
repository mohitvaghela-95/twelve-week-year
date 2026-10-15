from dataclasses import replace
from datetime import date, timedelta

import pytest

from twy.models import Block, Goal, Slot, Tactic, WeekItem
from twy.services import FormError
from twy.week_plans import (
    copy_prev_items,
    next_week_to_plan,
    parse_week_rows,
    plannable_weeks,
    resolved_items,
    save_week_plan,
    shortfall,
    tactic_groups,
    unplanned_reminders,
    week_ended,
)

MON = date(2026, 10, 5)


def setup(repo, start=MON):
    cycle = repo.create_cycle("Q4", start, 0)
    goal = repo.save_goal(Goal(None, cycle.id, "Fit"))
    run = repo.save_tactic(Tactic(None, goal.id, "Run", times_per_week=3))
    mocks = repo.save_tactic(Tactic(None, goal.id, "Mock race", times_per_week=1, start_week=3, end_week=4))
    return cycle, run, mocks


def session(cycle, week_no, tactic, weekday, start=420):
    return WeekItem(cycle.id, week_no, "tactic", weekday, start, 60, tactic_id=tactic.id)


def test_plannable_weeks_are_this_week_and_next(repo):
    cycle, _run, _mocks = setup(repo)
    assert plannable_weeks(cycle, MON - timedelta(days=30)) == [1]  # any time before the start
    assert plannable_weeks(cycle, MON + timedelta(days=9)) == [2, 3]
    assert plannable_weeks(cycle, MON + timedelta(weeks=11)) == [12, 13]
    assert plannable_weeks(cycle, MON + timedelta(weeks=12)) == [13]
    assert plannable_weeks(cycle, MON + timedelta(weeks=13)) == []  # after week 13
    assert plannable_weeks(replace(cycle, status="closed"), MON) == []
    assert not week_ended(cycle, 1, MON + timedelta(days=6)) and week_ended(cycle, 1, MON + timedelta(days=7))


def test_save_week_plan_keeps_blocks_and_refuses_other_weeks(repo):
    cycle, run, _mocks = setup(repo)
    admin = repo.save_block(Block(None, cycle.id, "Admin", "admin", slots=[Slot(4, 960, 30)]))
    today = MON + timedelta(days=2)
    assert [i.title for i in resolved_items(repo, cycle, 1)] == ["Admin"]  # unplanned: the template

    save_week_plan(repo, cycle, 1, today, [session(cycle, 1, run, 0)])
    assert [(i.kind, i.title) for i in repo.week_items(cycle.id, 1)] == [("tactic", "Run"), ("block", "Admin")]
    assert repo.week_items(cycle.id, 1)[1].block_id == admin.id
    with pytest.raises(FormError, match="Week 3 can't be planned now"):
        save_week_plan(repo, cycle, 3, today, [])
    with pytest.raises(FormError, match="Week 1 can't be planned now"):
        save_week_plan(repo, cycle, 1, MON + timedelta(days=7), [])  # week 1 is over


def test_parse_week_rows_only_takes_this_weeks_tactics(repo):
    cycle, run, mocks = setup(repo)
    tactics = [run]  # week 1: the mock race starts in week 3
    rows = parse_week_rows(cycle, 1, tactics, ["tactic"] * 2, [str(run.id)] * 2, ["0", "2"], ["07:00", ""], ["30"] * 2)
    assert [(i.weekday, i.start_min, i.duration_min, i.title) for i in rows] == [(0, 420, 30, "Run")]
    with pytest.raises(FormError, match="Reload the page"):
        parse_week_rows(cycle, 1, tactics, ["tactic"], [str(mocks.id)], ["0"], ["07:00"], ["30"])
    with pytest.raises(FormError, match="Run, time 2: must end by midnight"):
        parse_week_rows(
            cycle, 1, tactics, ["tactic"] * 2, [str(run.id)] * 2, ["0", "0"], ["07:00", "23:30"], ["60"] * 2
        )


def test_copy_prev_and_groups(repo):
    cycle, run, mocks = setup(repo)
    today = MON + timedelta(days=15)  # week 3
    assert copy_prev_items(repo, cycle, 1) is None
    assert copy_prev_items(repo, cycle, 3) is None  # week 2 wasn't planned
    repo.replace_week_items(cycle.id, 2, "plan", [session(cycle, 2, run, 1), session(cycle, 2, run, 3, 1080)])
    copied = copy_prev_items(repo, cycle, 3)
    assert [(i.week_no, i.weekday, i.start_min, i.id) for i in copied] == [(3, 1, 420, None), (3, 3, 1080, None)]

    groups = {g.tactic.title: g for g in tactic_groups(repo, cycle, 3, copied)}
    assert groups["Run"].placed == 2 and len(groups["Run"].rows) == 3  # one blank row for the third run
    assert groups["Run"].rows[2] == {"day": "4", "time": "", "minutes": "60"}  # the day after the last
    assert groups["Mock race"].placed == 0 and len(groups["Mock race"].rows) == 1
    assert [(t.title, n) for t, n in shortfall([run, mocks], copied)] == [("Run", 2), ("Mock race", 0)]
    save_week_plan(repo, cycle, 3, today, copied)
    assert next_week_to_plan(repo, cycle, today) == 4


def test_reminders_this_week_and_next_from_review_day(repo):
    cycle, _run, _mocks = setup(repo)
    tue = MON + timedelta(days=1)
    assert unplanned_reminders(repo, cycle, tue) == [1]
    repo.replace_week_items(cycle.id, 1, "plan", [])
    assert unplanned_reminders(repo, cycle, tue) == []
    # No review block: next week's nudge starts on the week's 6th day (Saturday).
    assert unplanned_reminders(repo, cycle, MON + timedelta(days=4)) == []
    assert unplanned_reminders(repo, cycle, MON + timedelta(days=5)) == [2]
    # A Sunday review block moves it to Sunday.
    repo.save_block(Block(None, cycle.id, "Weekly review", "review", slots=[Slot(6, 1080, 30)]))
    assert unplanned_reminders(repo, cycle, MON + timedelta(days=5)) == []
    assert unplanned_reminders(repo, cycle, MON + timedelta(days=6)) == [2]
    # Before the cycle: week 1 from the review day of the week before; never for closed cycles.
    later, _run2, _mocks2 = setup(repo, start=MON + timedelta(weeks=20))
    assert unplanned_reminders(repo, later, later.start_date - timedelta(days=3)) == []  # Friday before
    assert unplanned_reminders(repo, later, later.start_date - timedelta(days=2)) == [1]  # Saturday: day 6
    assert unplanned_reminders(repo, replace(cycle, status="closed"), tue) == []


def test_reminders_skip_weeks_without_tactics(repo):
    cycle = repo.create_cycle("Q4", MON, 0)
    goal = repo.save_goal(Goal(None, cycle.id, "Fit"))
    repo.save_tactic(Tactic(None, goal.id, "Race", start_week=12, end_week=12))
    assert unplanned_reminders(repo, cycle, MON) == []
    assert unplanned_reminders(repo, cycle, MON + timedelta(weeks=11)) == [12]
    assert unplanned_reminders(repo, cycle, MON + timedelta(weeks=12)) == []  # week 13: nothing to place
