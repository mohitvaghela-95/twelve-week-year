from datetime import date

import pytest

from twy.domain.dates import align_to_week_start, current_week, cycle_end, last_finished_week, week_range

MON = date(2026, 10, 5)  # a Monday
SUN = date(2026, 10, 11)


def test_align_monday_start():
    assert align_to_week_start(date(2026, 10, 7), 0) == date(2026, 10, 12)
    assert align_to_week_start(MON, 0) == MON  # already aligned


def test_align_sunday_start():
    assert align_to_week_start(date(2026, 10, 7), 6) == SUN
    assert align_to_week_start(SUN, 6) == SUN


def test_week_range():
    assert week_range(MON, 1) == (MON, date(2026, 10, 11))
    assert week_range(MON, 2) == (date(2026, 10, 12), date(2026, 10, 18))
    assert cycle_end(MON) == date(2026, 12, 27)
    with pytest.raises(ValueError):
        week_range(MON, 0)


def test_week_range_across_dst_change():
    # US DST ends Nov 1 2026; date-only math must not drift.
    first, last = week_range(date(2026, 10, 26), 2)
    assert (first, last) == (date(2026, 11, 2), date(2026, 11, 8))


def test_current_week_positions():
    assert current_week(MON, date(2026, 10, 4)).not_started
    assert current_week(MON, MON).week_no == 1
    assert current_week(MON, date(2026, 10, 11)).week_no == 1
    assert current_week(MON, date(2026, 10, 12)).week_no == 2
    assert current_week(MON, date(2026, 12, 27)).week_no == 12
    assert current_week(MON, date(2026, 12, 28)).in_review_week
    assert current_week(MON, date(2027, 1, 4)).finished


def test_last_finished_week():
    assert last_finished_week(MON, MON) == 0
    assert last_finished_week(MON, date(2026, 10, 11)) == 0  # week 1 still running on its last day
    assert last_finished_week(MON, date(2026, 10, 12)) == 1
    assert last_finished_week(MON, date(2027, 6, 1)) == 12
