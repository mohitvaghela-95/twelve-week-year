from twy.domain.guardrails import plan_warnings
from twy.models import Goal, Tactic


def goals(n):
    return [Goal(id=i, cycle_id=1, title=f"G{i}") for i in range(1, n + 1)]


def test_clean_plan_has_no_warnings():
    assert plan_warnings(goals(1), [Tactic(id=1, goal_id=1, title="t")]) == []


def test_too_many_goals_and_goal_without_tactics():
    warnings = plan_warnings(goals(4), [Tactic(id=i, goal_id=i, title="t") for i in range(1, 4)])
    assert any("4 goals" in w for w in warnings)
    assert any("G4" in w and "no tactics" in w for w in warnings)


def test_too_many_tactics_and_heavy_weeks():
    tactics = [Tactic(id=i, goal_id=1, title=f"t{i}", times_per_week=2) for i in range(9)]
    warnings = plan_warnings(goals(1), tactics)
    assert any("9 tactics" in w for w in warnings)
    assert any("Heavy weeks" in w for w in warnings)


def test_invalid_week_range():
    warnings = plan_warnings(goals(1), [Tactic(id=1, goal_id=1, title="bad", start_week=8, end_week=3)])
    assert any("invalid week range" in w for w in warnings)
