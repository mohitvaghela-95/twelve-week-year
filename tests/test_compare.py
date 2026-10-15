from dataclasses import replace

from twy.domain.compare import compare, log_hints, summary
from twy.models import WeekItem

MON, TUE, WED = 0, 1, 2


def run(day, start, item_id=None, plan_item_id=None, layer="plan"):
    return WeekItem(
        1, 1, "tactic", day, start, 30, layer=layer, tactic_id=7, title="Run", id=item_id, plan_item_id=plan_item_id
    )


def test_each_planned_session_is_kept_moved_or_dropped_and_extras_are_added():
    plan = [run(MON, 420, 1), run(WED, 420, 2), run(MON, 450, 3)]
    actual = [
        run(MON, 420, 11, plan_item_id=1, layer="actual"),  # kept
        run(TUE, 420, 12, plan_item_id=3, layer="actual"),  # the 07:30 one moved, not the 07:00 one
        run(TUE, 1080, 13, layer="actual"),  # not planned
    ]
    changes = compare(plan, actual)
    assert [(c.status, c.plan and c.plan.id, c.actual and c.actual.id) for c in changes] == [
        ("kept", 1, 11),
        ("moved", 3, 12),
        ("added", None, 13),
        ("dropped", 2, None),
    ]
    assert [c.label for c in changes] == ["", "moved from Mon 07:30", "added", "didn't happen"]
    assert changes[1].ghost_label == "moved to Tue 07:00" and changes[3].ghost_label == "didn't happen"
    assert summary(changes) == "1 moved, 1 didn't happen, 1 added"


def test_a_changed_length_counts_as_moved():
    [change] = compare(
        [run(MON, 420, 1)], [replace(run(MON, 420, 11, plan_item_id=1, layer="actual"), duration_min=45)]
    )
    assert change.status == "moved"


def test_unrecorded_weeks_went_to_plan():
    changes = compare([run(MON, 420, 1), run(WED, 420, 2)], None)
    assert {c.status for c in changes} == {"kept"} and summary(changes) == "Went to plan"


def test_skipped_blocks_events_and_deleted_tactics_are_not_compared():
    skipped = WeekItem(1, 1, "block", TUE, 480, 30, block_id=4, skipped=True, id=5)
    dinner = WeekItem(1, 1, "event", WED, 1140, 120, title="Dinner", id=6)
    orphan = replace(run(MON, 420, 8), tactic_id=None)
    assert compare([skipped, dinner, orphan], []) == []


def test_a_row_linked_to_a_plan_row_that_is_gone_counts_as_added():
    [change] = compare([], [run(MON, 420, 11, plan_item_id=99, layer="actual")])
    assert change.status == "added"


def test_log_hints_only_when_fewer_were_logged_than_happened():
    plan = [run(MON, 420, 1), run(WED, 420, 2)]
    titles = {7: "Run"}
    assert log_hints(compare(plan, None), {7: 1}, titles) == [
        "Run: 2 in the calendar, 1 logged on Today. 1 may not have happened."
    ]
    assert log_hints(compare(plan, None), {7: 2}, titles) == []
    assert log_hints(compare(plan, None), {7: 5}, titles) == []  # logging more than planned is fine
    # Once recorded, only what happened counts: one session didn't, so one log is enough.
    actual = [run(MON, 420, 11, plan_item_id=1, layer="actual")]
    assert log_hints(compare(plan, actual), {7: 1}, titles) == []
    assert log_hints(compare(plan, actual), {}, titles) == [
        "Run: 1 in the calendar, 0 logged on Today. 1 may not have happened."
    ]
