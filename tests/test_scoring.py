from twy.domain.scoring import cycle_average, execution_score, goal_outcome, planned_items, score_band
from twy.models import Goal, Tactic, WeekEntry


def tactic(times=1, start=1, end=12):
    return Tactic(id=None, goal_id=1, title="t", times_per_week=times, start_week=start, end_week=end)


def entry(planned, done):
    return WeekEntry(cycle_id=1, week_no=1, tactic_id=1, planned=planned, done=done)


def test_planned_items_counts_occurrences_and_ranges():
    tactics = [tactic(times=3), tactic(times=1, start=1, end=1), tactic(times=2, start=6, end=12)]
    assert planned_items(tactics, 1) == 4
    assert planned_items(tactics, 2) == 3
    assert planned_items(tactics, 6) == 5


def test_execution_score():
    assert execution_score([entry(3, 2), entry(1, 1)]) == 0.75
    assert execution_score([entry(3, 5)]) == 1.0  # clamped
    assert execution_score([entry(2, -1)]) == 0.0  # clamped
    assert execution_score([]) is None
    assert execution_score([entry(0, 0)]) is None


def test_cycle_average_skips_empty_weeks():
    assert cycle_average([1.0, 0.5, None]) == 0.75
    assert cycle_average([None]) is None


def test_goal_outcome_increasing():
    g = Goal(id=1, cycle_id=1, title="Read", baseline=0, target=6)
    assert goal_outcome(g, 6) == "hit"
    assert goal_outcome(g, 5) == "missed"
    assert goal_outcome(g) is None


def test_goal_outcome_decreasing():
    g = Goal(id=1, cycle_id=1, title="Weight", baseline=80, target=75)
    assert goal_outcome(g, 74.5) == "hit"
    assert goal_outcome(g, 76) == "missed"


def test_goal_outcome_final_value_wins_and_manual():
    g = Goal(id=1, cycle_id=1, title="Read", baseline=0, target=6, final_value=7)
    assert goal_outcome(g, 2) == "hit"
    manual = Goal(id=1, cycle_id=1, title="Vague", outcome="hit")
    assert goal_outcome(manual) == "hit"


def test_score_band():
    assert score_band(0.85) == "good"
    assert score_band(0.7) == "warn"
    assert score_band(0.2) == "bad"
    assert score_band(None) == "none"
