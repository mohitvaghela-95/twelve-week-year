from dataclasses import replace
from datetime import date, timedelta

from twy.domain.schedule import (
    Session,
    cycle_week_at,
    feed_weeks,
    lanes,
    overlaps,
    planning_offset,
    sessions_for_week,
    slot_date,
)
from twy.models import Block, Cycle, Slot, Tactic

MON = date(2026, 10, 5)
SUN = MON + timedelta(days=6)


def cycle(id=1, start=MON, week_start=0, status="active"):
    return Cycle(id=id, name=f"C{id}", start_date=start, week_start=week_start, status=status)


def session(day, start, minutes, slot_id):
    return Session(
        day=day, start_min=start, duration_min=minutes, title=f"s{slot_id}", kind="tactic",
        owner="tactic", owner_id=1, slot_id=slot_id, cycle_id=1, week_no=1,
    )


def test_slot_date_follows_the_cycle_week():
    saturday_weeks = cycle(start=date(2026, 10, 3), week_start=5)
    assert slot_date(saturday_weeks, 1, 5) == date(2026, 10, 3)  # Saturday starts the week
    assert slot_date(saturday_weeks, 1, 0) == date(2026, 10, 5)  # the Monday after
    assert slot_date(saturday_weeks, 2, 4) == date(2026, 10, 16)  # Friday ends week 2


def test_sessions_respect_tactic_ranges_and_week_13():
    c = cycle()
    study = Tactic(id=1, goal_id=1, title="Study", times_per_week=2)
    mocks = Tactic(id=2, goal_id=1, title="Mock interview", start_week=10, end_week=12)
    slots = {1: [Slot(0, 420, 60, id=1), Slot(2, 420, 60, id=2)], 2: [Slot(5, 600, 90, id=3)]}
    admin = Block(id=1, cycle_id=1, title="Admin", kind="admin", slots=[Slot(4, 960, 30, id=4)])

    week1 = sessions_for_week(c, 1, [study, mocks], slots, {1: "AI engineer offer"}, [admin])
    assert [(s.title, s.day) for s in week1] == [
        ("Study", date(2026, 10, 5)),
        ("Study", date(2026, 10, 7)),
        ("Admin", date(2026, 10, 9)),
    ]
    assert week1[0].context == "AI engineer offer"
    assert week1[0].uid == "tactic-1-20261005@twy"
    assert {s.title for s in sessions_for_week(c, 10, [study, mocks], slots, {}, [admin])} == {
        "Study", "Mock interview", "Admin",
    }
    assert [s.title for s in sessions_for_week(c, 13, [study, mocks], slots, {}, [admin])] == ["Admin"]


def test_overlaps_and_lanes():
    a = session(MON, 420, 60, 1)  # 07:00–08:00
    b = session(MON, 450, 60, 2)  # 07:30–08:30, clashes with a
    c = session(MON, 480, 30, 3)  # 08:00–08:30, touches a, clashes with b
    d = session(MON, 600, 30, 4)  # on its own
    e = session(MON + timedelta(days=1), 420, 60, 5)  # same time, another day
    assert overlaps([a, b, c, d, e]) == {a.uid: ["s2"], b.uid: ["s1", "s3"], c.uid: ["s2"]}
    layout = lanes([a, b, c, d, e])
    assert (layout[a.uid], layout[b.uid], layout[c.uid]) == ((0, 2), (1, 2), (0, 2))
    assert layout[d.uid] == layout[e.uid] == (0, 1)


def test_cycle_week_at_prefers_the_newer_cycle():
    old = cycle(1)
    new = cycle(2, MON + timedelta(weeks=12), status="planning")
    assert cycle_week_at([old, new], MON + timedelta(days=3)) == (old, 1)
    assert cycle_week_at([old, new], MON + timedelta(weeks=12, days=1)) == (new, 1)  # old's week 13
    assert cycle_week_at([old], MON - timedelta(days=1)) is None
    assert cycle_week_at([replace(old, status="closed")], MON) is None


def test_planning_offset_uses_review_blocks_only():
    c = cycle()
    chores = Block(id=2, cycle_id=1, title="Chores", kind="chores", slots=[Slot(5, 600, 60)])
    review = Block(id=1, cycle_id=1, title="Weekly review", kind="review", slots=[Slot(6, 1080, 30)])
    assert planning_offset(c, [chores]) is None
    assert planning_offset(c, [chores, review]) == 6  # Sunday in a Monday-start week
    assert planning_offset(cycle(start=date(2026, 10, 4), week_start=6), [review]) == 0  # Sunday-start week


# -- feed_weeks: which weeks the calendar publishes -------------------------------


def test_feed_is_the_current_week_without_a_review_block():
    c = cycle()
    assert feed_weeks([c], {1: None}, MON + timedelta(days=2)) == [(c, 1)]
    assert feed_weeks([c], {1: None}, SUN) == [(c, 1)]


def test_feed_adds_next_week_from_the_planning_day():
    c = cycle()
    assert feed_weeks([c], {1: 6}, SUN - timedelta(days=1)) == [(c, 1)]  # Saturday
    assert feed_weeks([c], {1: 6}, SUN) == [(c, 1), (c, 2)]  # review day itself


def test_feed_hands_over_to_the_next_cycle():
    old = cycle(1)
    new = cycle(2, MON + timedelta(weeks=13), status="planning")
    week13_sunday = SUN + timedelta(weeks=12)
    assert feed_weeks([old, new], {1: 6, 2: 6}, week13_sunday) == [(old, 13), (new, 1)]
    assert feed_weeks([old], {1: 6}, week13_sunday) == [(old, 13)]  # nothing planned after


def test_feed_is_empty_outside_any_cycle():
    assert feed_weeks([cycle()], {1: 6}, MON - timedelta(days=3)) == []
    assert feed_weeks([], {}, MON) == []
