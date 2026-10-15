from twy.domain.conflicts import (
    Placed,
    check_rows,
    find_clashes,
    free_starts,
    pair_warnings,
    placed_for_cycle,
    placed_for_week,
    shared_weeks,
)
from twy.models import Block, Slot, WeekItem

MON, TUE = 0, 1
WINDOW = (6 * 60, 22 * 60)


def at(h, m=0):
    return h * 60 + m


def placed(title, start, minutes=60, weeks=(1, 12), day=MON, owner_id=1, owner="tactic", row=None):
    return Placed(title, owner, owner_id, weeks, Slot(day, start, minutes), row)


def test_overlap_is_exact_and_same_day():
    study = placed("Study", at(7))
    assert not find_clashes([study, placed("Gym", at(8), owner_id=2)])  # back to back
    assert find_clashes([study, placed("Gym", at(7, 59), owner_id=2)])  # a 1-minute overlap
    assert not find_clashes([study, placed("Gym", at(7), day=TUE, owner_id=2)])


def test_clashes_only_count_in_shared_weeks():
    early = placed("Study", at(7), weeks=(1, 6))
    late = placed("Gym", at(7), weeks=(7, 12), owner_id=2)
    assert not find_clashes([early, late])
    assert shared_weeks((1, 12), (7, 12)) == (7, 12)

    admin = Block(id=5, cycle_id=1, title="Admin", kind="admin", slots=[Slot(MON, at(7, 30), 30)])
    chores = Block(id=6, cycle_id=1, title="Chores", kind="chores", slots=[Slot(MON, at(7), 60)])
    everything = placed_for_cycle([admin, chores])
    [clash] = find_clashes(everything)
    assert clash.weeks == (1, 13)  # blocks run in weeks 1–13
    assert placed_for_cycle([admin, chores], skip=5) == everything[1:]


def test_a_week_plan_is_checked_within_its_week():
    run = WeekItem(1, 3, "tactic", MON, at(7), 60, tactic_id=1, title="Run")
    admin = WeekItem(1, 3, "block", MON, at(7, 30), 30, block_id=5, title="Admin")
    skipped = WeekItem(1, 3, "block", MON, at(7), 30, block_id=6, title="Chores", skipped=True)
    placed_items = placed_for_week(3, [run, admin, skipped])
    assert [(p.title, p.owner, p.owner_id, p.weeks) for p in placed_items] == [
        ("Run", "tactic", 1, (3, 3)),
        ("Admin", "block", 5, (3, 3)),
    ]
    [clash] = find_clashes(placed_items)
    assert clash.weeks == (3, 3)


def test_pair_warnings_group_days():
    rows = []
    for day in range(5):
        rows += [placed("Study", at(7), day=day), placed("Gym", at(7, 30), day=day, weeks=(7, 12), owner_id=2)]
    rows += [placed("Study", at(20), day=TUE), placed("Study", at(20, 30), day=TUE)]  # the same item twice
    warnings = pair_warnings(find_clashes(rows))
    assert [w.text for w in warnings] == [
        "“Study” overlaps “Gym” on Mon, Tue, Wed, Thu, Fri (weeks 7–12).",
        "“Study” overlaps itself on Tue (weeks 1–12).",
    ]
    assert (warnings[0].owner, warnings[0].owner_id) == ("tactic", 1)


def test_check_rows_flags_form_rows_and_suggests():
    gym = placed("Gym", at(7), owner_id=2)
    rows = [placed("slot 1", at(7, 30), row=1), placed("slot 2", at(9), row=2), placed("slot 3", at(9, 30), row=3)]
    checks = check_rows(rows, [gym], WINDOW)
    assert [p.title for p, _weeks in checks[0].clashes] == ["Gym"]
    # Busy: Gym 07:00–08:00 and the form's own rows 09:00–10:30. Back-to-back gaps count as free.
    assert (checks[0].earlier, checks[0].later) == (at(6), at(8))
    assert [p.title for p, _weeks in checks[1].clashes] == ["slot 3"]
    assert [p.title for p, _weeks in checks[2].clashes] == ["slot 2"]


# -- free_starts ------------------------------------------------------------------


def test_free_starts_nearest_either_side():
    busy = [Slot(MON, at(7), 60), Slot(MON, at(8), 30)]  # 07:00–08:30
    assert free_starts(Slot(MON, at(7, 30), 60), busy, WINDOW) == (at(6), at(8, 30))


def test_free_starts_keeps_the_length_and_the_grid():
    busy = [Slot(MON, at(7), 60), Slot(MON, at(9), 60)]  # free 08:00–09:00 and from 10:00
    # 90 minutes doesn't fit between 08:00 and 09:00, and 07:10 is off the 15-minute grid.
    assert free_starts(Slot(MON, at(7, 10), 90), busy, WINDOW) == (None, at(10))
    assert free_starts(Slot(MON, at(7, 10), 60), busy, WINDOW) == (at(6), at(8))


def test_free_starts_stays_inside_the_window():
    busy = [Slot(MON, at(6), 60), Slot(MON, at(21), 60)]
    # Earlier would start before 06:00; later would end after 22:00.
    assert free_starts(Slot(MON, at(21, 15), 60), busy, WINDOW) == (at(20), None)
    # 06:00 is before a 06:10 window and 06:15 overlaps the busy 06:30.
    assert free_starts(Slot(MON, at(6, 30), 30), [Slot(MON, at(6, 30), 30)], (at(6, 10), at(22))) == (None, at(7))


def test_free_starts_full_day():
    assert free_starts(Slot(MON, at(9), 60), [Slot(MON, at(6), 16 * 60)], WINDOW) == (None, None)
