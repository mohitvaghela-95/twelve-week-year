from dataclasses import replace
from datetime import date, timedelta

import pytest

from twy.models import Block, Goal, Slot, Tactic, WeekItem
from twy.services import FormError
from twy.week_plans import (
    FormRow,
    copy_prev_items,
    form_rows,
    next_week_to_plan,
    parse_plan_rows,
    plan_form,
    plannable_weeks,
    posted_form,
    resolved_items,
    save_week_plan,
    shortfall,
    tactic_groups,
    unplanned_reminders,
    week_ended,
    week_plan_check,
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


def tactic_row(tactic, day, time, minutes="30"):
    return FormRow("tactic", str(tactic.id), day=day, time=time, minutes=minutes)


def test_form_rows_zip_the_parallel_lists():
    rows = form_rows({"slot_kind": ["tactic", "event"], "slot_title": ["", "Dinner"], "slot_time": ["07:00"]})
    assert rows == [
        FormRow("tactic", time="07:00", day="", minutes=""),
        FormRow("event", title="Dinner", day="", minutes=""),
    ]


def test_parse_plan_rows_only_takes_this_weeks_tactics(repo):
    cycle, run, mocks = setup(repo)
    tactics = [run]  # week 1: the mock race starts in week 3
    rows = parse_plan_rows(cycle, 1, tactics, [], [tactic_row(run, "0", "07:00"), tactic_row(run, "2", "")])
    assert [(i.weekday, i.start_min, i.duration_min, i.title) for i in rows] == [(0, 420, 30, "Run")]
    with pytest.raises(FormError, match="Reload the page"):
        parse_plan_rows(cycle, 1, tactics, [], [tactic_row(mocks, "0", "07:00")])
    with pytest.raises(FormError, match="Run, time 2: must end by midnight"):
        parse_plan_rows(cycle, 1, tactics, [], [tactic_row(run, "0", "07:00"), tactic_row(run, "0", "23:30", "60")])
    with pytest.raises(FormError, match="Reload the page"):
        parse_plan_rows(cycle, 1, tactics, [], [FormRow("meeting", time="07:00")])


def test_blocks_can_be_skipped_or_moved_for_one_week(repo):
    cycle, _run, _mocks = setup(repo)
    admin = repo.save_block(Block(None, cycle.id, "Admin", "admin", slots=[Slot(0, 480, 30), Slot(2, 480, 30)]))

    def block_row(origin, day, time, minutes="30"):
        return FormRow("block", str(admin.id), origin, day=day, time=time, minutes=minutes)

    rows = [block_row("0:480", "0", "08:00"), block_row("2:480", "2", ""), block_row("0:480", "1", "09:00", "45")]
    kept, skipped, moved = parse_plan_rows(cycle, 1, [], [admin], rows)
    assert (kept.custom, kept.skipped, kept.weekday, kept.start_min) == (False, False, 0, 480)
    assert (skipped.custom, skipped.skipped, skipped.weekday, skipped.start_min, skipped.duration_min) == (
        True,
        True,
        2,
        480,
        30,
    )
    assert (moved.custom, moved.weekday, moved.start_min, moved.duration_min) == (True, 1, 540, 45)
    assert (moved.orig_weekday, moved.orig_start_min) == (0, 480)
    with pytest.raises(FormError, match=r"Admin \(usually Mon 08:00\): use a time like 07:30"):
        parse_plan_rows(cycle, 1, [], [admin], [block_row("0:480", "0", "8")])
    with pytest.raises(FormError, match="Reload the page"):
        parse_plan_rows(cycle, 1, [], [admin], [block_row("9:99999", "0", "08:00")])

    # The form shows a skipped occurrence with a blank time and its usual slot, and posts it back the same.
    save_week_plan(repo, cycle, 1, MON, [kept, skipped])
    form = plan_form(repo, cycle, 1, repo.week_items(cycle.id, 1))
    [group] = form.blocks
    assert [(r["time"], r["origin"], r["usual"]) for r in group.rows] == [
        ("08:00", "0:480", "Mon 08:00"),
        ("", "2:480", "Wed 08:00"),
    ]


def test_events_need_a_name_and_a_time(repo):
    cycle, _run, _mocks = setup(repo)

    def event(title, time, day="4"):
        return FormRow("event", title=title, day=day, time=time, minutes="120")

    [dinner] = parse_plan_rows(cycle, 1, [], [], [event("  Dinner with Sam ", "19:00"), event("", "")])
    assert (dinner.kind, dinner.title, dinner.weekday, dinner.start_min, dinner.duration_min) == (
        "event",
        "Dinner with Sam",
        4,
        1140,
        120,
    )
    with pytest.raises(FormError, match="Event 1: give it a name"):
        parse_plan_rows(cycle, 1, [], [], [event("", "19:00")])
    with pytest.raises(FormError, match="“Dinner” needs a start time"):
        parse_plan_rows(cycle, 1, [], [], [event("Dinner", "")])
    with pytest.raises(FormError, match="“Dinner”: must end by midnight"):
        parse_plan_rows(cycle, 1, [], [], [event("Dinner", "23:00")])

    # Shown again with an error, a posted event keeps what was typed.
    form = posted_form(repo, cycle, 1, [event("Dinner", "")])
    assert form.events == [{"title": "Dinner", "day": "4", "time": "", "minutes": "120"}]
    assert plan_form(repo, cycle, 1, []).events == [{"title": "", "day": "0", "time": "", "minutes": "60"}]


def test_week_plan_check_covers_every_row(repo):
    cycle, run, _mocks = setup(repo)
    admin = repo.save_block(Block(None, cycle.id, "Admin", "admin", slots=[Slot(0, 480, 30)]))
    rows = [
        tactic_row(run, "0", "08:15"),
        FormRow("block", str(admin.id), "0:480", day="0", time="08:00", minutes="30"),
        FormRow("event", title="", day="0", time="08:20", minutes="10"),
        tactic_row(run, "1", ""),
    ]
    data = week_plan_check(repo, cycle, 1, rows)
    assert data["placed"] == {str(run.id): 1}
    assert data["rows"][0]["clashes"] == ["Overlaps Admin · Mon 08:00–08:30", "Overlaps an event · Mon 08:20–08:30"]
    assert data["rows"][1]["clashes"] == ["Overlaps Run · Mon 08:15–08:45", "Overlaps an event · Mon 08:20–08:30"]
    assert data["rows"][3] is None


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
