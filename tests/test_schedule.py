from dataclasses import replace
from datetime import date, timedelta

from twy.domain.schedule import (
    Session,
    cycle_week_at,
    feed_weeks,
    lanes,
    overlaps,
    planning_offset,
    sessions_from_items,
    slot_date,
    template_items,
)
from twy.models import Block, Cycle, Slot, WeekItem

MON = date(2026, 10, 5)
SUN = MON + timedelta(days=6)


def cycle(id=1, start=MON, week_start=0, status="active"):
    return Cycle(id=id, name=f"C{id}", start_date=start, week_start=week_start, status=status)


def session(day, start, minutes, slot_id):
    return Session(
        day=day,
        start_min=start,
        duration_min=minutes,
        title=f"s{slot_id}",
        kind="tactic",
        owner="tactic",
        owner_id=1,
        key=f"s-{slot_id}",
        cycle_id=1,
        week_no=1,
    )


def test_slot_date_follows_the_cycle_week():
    saturday_weeks = cycle(start=date(2026, 10, 3), week_start=5)
    assert slot_date(saturday_weeks, 1, 5) == date(2026, 10, 3)  # Saturday starts the week
    assert slot_date(saturday_weeks, 1, 0) == date(2026, 10, 5)  # the Monday after
    assert slot_date(saturday_weeks, 2, 4) == date(2026, 10, 16)  # Friday ends week 2


def test_an_unplanned_week_is_its_blocks_in_weeks_1_to_13():
    c = cycle()
    admin = Block(id=1, cycle_id=1, title="Admin", kind="admin", slots=[Slot(4, 960, 30, id=4)])
    [item] = template_items(c, 13, [admin])
    assert (item.kind, item.block_id, item.title, item.weekday) == ("block", 1, "Admin", 4)
    assert (item.orig_weekday, item.orig_start_min) == (4, 960)
    assert template_items(c, 14, [admin]) == []


def test_sessions_from_items_number_tactics_and_keep_block_keys():
    c = cycle()
    study = [
        WeekItem(1, 2, "tactic", 2, 420, 60, tactic_id=1, title="Study"),
        WeekItem(1, 2, "tactic", 0, 420, 60, tactic_id=1, title="Study"),
    ]
    admin = WeekItem(1, 2, "block", 4, 960, 30, block_id=3, title="Admin", orig_weekday=4, orig_start_min=960)
    moved = replace(admin, weekday=5, start_min=600, custom=True)
    skipped = WeekItem(1, 2, "block", 6, 600, 60, block_id=4, title="Chores", skipped=True)

    week2 = sessions_from_items(c, 2, [*study, admin, skipped], {1: "AI engineer offer"}, {3: "admin"})
    assert [(s.title, s.day, s.kind) for s in week2] == [
        ("Study", date(2026, 10, 12), "tactic"),
        ("Study", date(2026, 10, 14), "tactic"),
        ("Admin", date(2026, 10, 16), "admin"),
    ]
    assert week2[0].context == "AI engineer offer"
    # Numbered in time order, so re-saving the same plan keeps every calendar event's identity.
    assert [s.uid for s in week2] == ["tactic-1-w2-1@twy", "tactic-1-w2-2@twy", "block-3-20261016-960@twy"]
    # A block moved for one week keeps its key, so calendar apps move the event rather than adding one.
    [moved_session] = sessions_from_items(c, 2, [moved], {}, {3: "admin"})
    assert moved_session.uid == week2[2].uid and moved_session.day == date(2026, 10, 17)

    # Events are numbered in time order within their cycle week.
    dinner = WeekItem(1, 2, "event", 4, 1140, 120, title="Dinner")
    dentist = WeekItem(1, 2, "event", 1, 540, 30, title="Dentist")
    events = sessions_from_items(c, 2, [dinner, dentist], {}, {})
    assert [(s.title, s.owner, s.kind, s.owner_id, s.uid) for s in events] == [
        ("Dentist", "event", "event", None, "event-c1-w2-1@twy"),
        ("Dinner", "event", "event", None, "event-c1-w2-2@twy"),
    ]


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


def test_feed_adds_next_week_once_it_is_planned():
    c = cycle()
    assert feed_weeks([c], {1: 6}, MON + timedelta(days=2), planned={(1, 2)}) == [(c, 1), (c, 2)]
    assert feed_weeks([c], {1: None}, MON, planned={(1, 2)}) == [(c, 1), (c, 2)]  # no review block needed
    assert feed_weeks([c], {1: None}, MON, planned={(2, 2)}) == [(c, 1)]  # another cycle's week 2


def test_feed_hands_over_to_the_next_cycle():
    old = cycle(1)
    new = cycle(2, MON + timedelta(weeks=13), status="planning")
    week13_sunday = SUN + timedelta(weeks=12)
    assert feed_weeks([old, new], {1: 6, 2: 6}, week13_sunday) == [(old, 13), (new, 1)]
    assert feed_weeks([old], {1: 6}, week13_sunday) == [(old, 13)]  # nothing planned after


def test_feed_is_empty_outside_any_cycle():
    assert feed_weeks([cycle()], {1: 6}, MON - timedelta(days=3)) == []
    assert feed_weeks([], {}, MON) == []
