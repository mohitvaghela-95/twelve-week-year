from datetime import date

from twy.models import CycleReview, Goal, Tactic, WeekEntry, WeekReview

MON = date(2026, 10, 5)


def make_plan(repo):
    cycle = repo.create_cycle("Q4", MON, 0)
    goal = repo.save_goal(Goal(id=None, cycle_id=cycle.id, title="Lose 5 kg", unit="kg", baseline=80, target=75))
    run = repo.save_tactic(Tactic(id=None, goal_id=goal.id, title="Run", times_per_week=3))
    log = repo.save_tactic(Tactic(id=None, goal_id=goal.id, title="Log food"))
    return cycle, goal, run, log


def test_settings_and_vision(repo):
    assert not repo.is_configured
    repo.set_setting("week_start", "6")
    assert repo.is_configured and repo.week_start == 6
    repo.set_vision("long_term", "Healthy and free")
    repo.set_vision("long_term", "Healthier")
    assert repo.get_vision("long_term") == "Healthier"
    assert repo.get_vision("three_year") == ""


def test_cycle_goal_tactic_round_trip(repo):
    cycle, goal, run, log = make_plan(repo)
    assert repo.current_cycle().id == cycle.id
    assert [g.title for g in repo.list_goals(cycle.id)] == ["Lose 5 kg"]
    assert [t.title for t in repo.list_tactics(cycle.id)] == ["Run", "Log food"]

    repo.move_tactic(log.id, -1)
    assert [t.title for t in repo.list_tactics(cycle.id)] == ["Log food", "Run"]

    run.times_per_week = 4
    repo.save_tactic(run)
    assert repo.get_tactic(run.id).times_per_week == 4

    repo.delete_goal(goal.id)
    assert repo.list_tactics(cycle.id) == []


def test_week_review_snapshot_and_scores(repo):
    cycle, goal, run, log = make_plan(repo)
    review = WeekReview(
        cycle_id=cycle.id,
        week_no=1,
        obstacles="Travel",
        fix="Run in hotel gym",
        entries=[WeekEntry(cycle.id, 1, run.id, 3, 2), WeekEntry(cycle.id, 1, log.id, 1, 1)],
    )
    repo.save_week_review(review, progress={goal.id: 79.0})
    assert repo.weekly_scores(cycle.id) == {1: 0.75}
    assert repo.latest_progress(goal.id) == 79.0

    # Editing the tactic later does not rewrite the snapshot.
    run.times_per_week = 5
    repo.save_tactic(run)
    planned = {e.tactic_id: e.planned for e in repo.get_week_review(cycle.id, 1).entries}
    assert planned == {run.id: 3, log.id: 1}
    assert repo.weekly_scores(cycle.id) == {1: 0.75}

    # Re-saving replaces the week, and None clears a lag reading.
    review.entries = [WeekEntry(cycle.id, 1, run.id, 3, 3), WeekEntry(cycle.id, 1, log.id, 1, 1)]
    repo.save_week_review(review, progress={goal.id: None})
    assert repo.weekly_scores(cycle.id) == {1: 1.0}
    assert repo.latest_progress(goal.id) is None
    assert repo.reviewed_weeks(cycle.id) == {1}


def test_close_cycle_and_copy_plan(repo):
    cycle, goal, run, log = make_plan(repo)
    repo.save_cycle_review(CycleReview(cycle.id, lessons="Plan less", celebration="Dinner"))
    repo.set_cycle_status(cycle.id, "closed")
    assert repo.current_cycle() is None

    nxt = repo.create_cycle("Q1", date(2027, 1, 4), 0)
    repo.copy_plan(cycle.id, nxt.id)
    assert [t.title for t in repo.list_tactics(nxt.id)] == ["Run", "Log food"]
    assert repo.get_cycle_review(cycle.id).lessons == "Plan less"
    assert [c.name for c in repo.list_cycles()] == ["Q1", "Q4"]
