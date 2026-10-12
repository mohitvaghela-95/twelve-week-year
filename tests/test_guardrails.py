from twy.domain.guardrails import heavy_weeks, plan_warnings
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
    tactics = [Tactic(id=i, goal_id=1, title=f"t{i}", times_per_week=2) for i in range(9)]  # 18 items/week
    warnings = plan_warnings(goals(1), tactics, max_weekly_items=15)
    assert any("9 tactics" in w for w in warnings)
    assert any("Heavy weeks (> 15 items)" in w for w in warnings)


def test_daily_habits_are_not_heavy_by_default():
    # 3 goals, each with a 5x/week habit plus a 3x/week tactic: 24 items is a normal plan.
    tactics = [Tactic(id=i, goal_id=i, title="study", times_per_week=5) for i in range(1, 4)]
    tactics += [Tactic(id=i + 3, goal_id=i, title="apply", times_per_week=3) for i in range(1, 4)]
    assert not any("Heavy weeks" in w for w in plan_warnings(goals(3), tactics))


def test_heavy_weeks_respects_week_ranges():
    tactics = [
        Tactic(id=1, goal_id=1, title="daily", times_per_week=5),
        Tactic(id=2, goal_id=1, title="sprint", times_per_week=5, start_week=11, end_week=12),
    ]
    assert heavy_weeks(tactics, 8) == [11, 12]


def test_invalid_week_range():
    warnings = plan_warnings(goals(1), [Tactic(id=1, goal_id=1, title="bad", start_week=8, end_week=3)])
    assert any("invalid week range" in w for w in warnings)
