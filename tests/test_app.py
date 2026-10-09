"""End-to-end UI flow with Textual's Pilot, against an in-memory database."""

from datetime import date

from textual.widgets import DataTable, Input, Static, TextArea, Tree

from twy.app import TwyApp
from twy.db import connect
from twy.models import Goal, Tactic
from twy.repo import Repo
from twy.screens.cycles import CyclesScreen
from twy.screens.dashboard import DashboardScreen
from twy.screens.forms import ConfirmModal, GoalForm, NewCycleModal, TacticForm
from twy.screens.plan import PlanScreen
from twy.screens.review import ReviewScreen
from twy.screens.settings import SettingsScreen
from twy.screens.vision import VisionScreen
from twy.screens.week13 import Week13Screen
from twy.widgets.score_chart import ScoreChart

SIZE = (120, 45)


def text(app: TwyApp, selector: str) -> str:
    return str(app.screen.query_one(selector, Static).render())


async def fill(pilot, widget_id: str, value: str) -> None:
    widget = pilot.app.screen.query_one(f"#{widget_id}", Input)
    widget.value = value
    await pilot.pause()


async def test_onboarding_creates_first_cycle():
    app = TwyApp(repo=Repo(connect(":memory:")), today=date(2026, 10, 7))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)
        await pilot.click("#save")
        await pilot.pause()
        assert isinstance(app.screen, VisionScreen)
        app.screen.query_one("#long_term", TextArea).text = "Healthy, calm, building things."
        await pilot.click("#save")
        await pilot.pause()
        assert isinstance(app.screen, NewCycleModal)
        # 7 Oct 2026 is a Wednesday, so the default start snaps to Monday 12 Oct.
        assert app.screen.query_one("#start", Input).value == "2026-10-12"
        await fill(pilot, "name", "Q4 sprint")
        await pilot.click("#submit")
        await pilot.pause()
        assert isinstance(app.screen, PlanScreen)

    assert app.repo.get_vision("long_term").startswith("Healthy")
    cycle = app.repo.current_cycle()
    assert (cycle.name, cycle.start_date, cycle.status) == ("Q4 sprint", date(2026, 10, 12), "planning")


async def test_plan_review_week13_close_flow():
    repo = Repo(connect(":memory:"))
    repo.set_setting("week_start", "0")
    cycle = repo.create_cycle("Q4", date(2026, 10, 5), 0)
    # Today is in week 3, so weeks 1 and 2 are finished.
    app = TwyApp(repo=repo, today=date(2026, 10, 20))

    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)

        # --- Plan: add a measurable goal and a 3x/week tactic through the forms.
        await pilot.press("p")
        await pilot.pause()
        assert isinstance(app.screen, PlanScreen)
        await pilot.press("g")
        await pilot.pause()
        assert isinstance(app.screen, GoalForm)
        await fill(pilot, "title", "Lose 5 kg")
        await fill(pilot, "unit", "kg")
        await fill(pilot, "baseline", "80")
        await fill(pilot, "target", "75")
        await pilot.click("#submit")
        await pilot.pause()

        await pilot.press("a")  # goal is selected after saving it
        await pilot.pause()
        assert isinstance(app.screen, TacticForm)
        await fill(pilot, "title", "Run 30 min")
        await fill(pilot, "times", "3")
        await pilot.click("#submit")
        await pilot.pause()

        goal = repo.list_goals(cycle.id)[0]
        repo.save_tactic(Tactic(id=None, goal_id=goal.id, title="Log food"))
        app.screen.refresh_view()
        await pilot.click("#start")
        await pilot.pause()
        assert repo.get_cycle(cycle.id).status == "active"
        tree = app.screen.query_one(Tree)
        assert len(tree.root.children) == 1 and len(tree.root.children[0].children) == 2

        # Typing letters into an Input must not trigger app-level navigation.
        await pilot.press("g")
        await pilot.pause()
        await pilot.press("p", "r", "q")
        await pilot.pause()
        assert isinstance(app.screen, GoalForm)
        assert app.screen.query_one("#title", Input).value == "prq"
        await pilot.press("escape")
        await pilot.pause()

        # --- Dashboard nags about the two unreviewed weeks.
        await pilot.press("d")
        await pilot.pause()
        assert "week 1, 2" in text(app, "#banner")

        # --- Review week 1 (default = oldest pending): run 2 of 3, log food 1 of 1 -> 75%.
        await pilot.press("r")
        await pilot.pause()
        assert isinstance(app.screen, ReviewScreen)
        assert app.screen.week_no == 1
        run_id, log_id = (t.id for t in repo.list_tactics(cycle.id))
        await fill(pilot, f"done-{run_id}", "2")
        await fill(pilot, f"done-{log_id}", "1")
        await fill(pilot, f"lag-{goal.id}", "79")
        assert "75%" in text(app, "#live-score")
        app.screen.query_one("#obstacles", TextArea).text = "Late meetings"
        await pilot.press("ctrl+s")
        await pilot.pause()

        assert repo.weekly_scores(cycle.id) == {1: 0.75}
        assert repo.get_week_review(cycle.id, 1).obstacles == "Late meetings"
        assert isinstance(app.screen, DashboardScreen)
        assert "75%" in text(app, "#last-score")
        assert "week 2" in text(app, "#banner")
        assert app.screen.query_one(ScoreChart) is not None

        # --- Week 13: final value 74 -> hit; close the cycle and start the next one.
        await pilot.press("w")
        await pilot.pause()
        assert isinstance(app.screen, Week13Screen)
        await fill(pilot, f"final-{goal.id}", "74")
        assert "hit" in text(app, f"#outcome-{goal.id}")
        app.screen.query_one("#lessons", TextArea).text = "Fewer tactics"
        await pilot.click("#close")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        await pilot.click("#submit")
        await pilot.pause()
        assert isinstance(app.screen, NewCycleModal)
        await fill(pilot, "name", "Q1")
        await pilot.click("#submit")
        await pilot.pause()

        old = repo.get_cycle(cycle.id)
        assert old.status == "closed"
        assert repo.get_goal(goal.id).outcome == "hit"
        assert repo.get_cycle_review(cycle.id).lessons == "Fewer tactics"
        new = repo.current_cycle()
        assert new.name == "Q1" and new.id != cycle.id
        assert [t.title for t in repo.list_tactics(new.id)] == ["Run 30 min", "Log food"]  # copied

        # --- History: open the closed cycle read-only.
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, CyclesScreen)
        table = app.screen.query_one(DataTable)
        assert table.row_count == 2
        table.move_cursor(row=1)  # newest first, so the old cycle is row 1
        await pilot.press("enter")
        await pilot.pause()
        assert app.read_only and app.cycle.id == cycle.id
        assert "read-only" in text(app, "#banner")
        await pilot.press("p")
        await pilot.pause()
        await pilot.press("g")
        await pilot.pause()
        assert isinstance(app.screen, PlanScreen)  # add-goal refused, no form opened
        await pilot.press("d", "x")
        await pilot.pause()
        assert not app.read_only and app.cycle.id == new.id
