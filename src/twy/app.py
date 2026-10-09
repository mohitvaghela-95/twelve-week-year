"""TwyApp: holds the repository, the 'today' clock and screen navigation."""

import os
from datetime import date
from pathlib import Path

from textual.app import App
from textual.binding import Binding
from textual.screen import ModalScreen

from twy.db import connect, default_db_path
from twy.models import Cycle
from twy.repo import Repo
from twy.screens.cycles import CyclesScreen
from twy.screens.dashboard import DashboardScreen
from twy.screens.forms import NewCycleModal
from twy.screens.plan import PlanScreen
from twy.screens.review import ReviewScreen
from twy.screens.settings import SettingsScreen
from twy.screens.trend import TrendScreen
from twy.screens.vision import VisionScreen
from twy.screens.week13 import Week13Screen
from twy.services import create_cycle, previous_cycle_id

SCREENS = {
    "dashboard": DashboardScreen,
    "plan": PlanScreen,
    "review": ReviewScreen,
    "trend": TrendScreen,
    "week13": Week13Screen,
    "cycles": CyclesScreen,
    "vision": VisionScreen,
    "settings": SettingsScreen,
}


class TwyApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "12 Week Year"
    BINDINGS = [
        Binding("d", "goto('dashboard')", "Home"),
        Binding("p", "goto('plan')", "Plan"),
        Binding("r", "goto('review')", "Review"),
        Binding("t", "goto('trend')", "Trend"),
        Binding("w", "goto('week13')", "Week 13"),
        Binding("c", "goto('cycles')", "Cycles"),
        Binding("v", "goto('vision')", "Vision"),
        Binding("s", "goto('settings')", "Settings"),
        Binding("escape", "goto('dashboard')", "Home", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, repo: Repo | None = None, today: date | None = None, db_path: Path | None = None):
        super().__init__()
        self.db_path = db_path or default_db_path()
        self.repo = repo or Repo(connect(self.db_path))
        env_today = os.environ.get("TWY_TODAY")
        self._today = today or (date.fromisoformat(env_today) if env_today else None)
        self.viewing_cycle_id: int | None = None  # set when browsing a past cycle

    @property
    def today(self) -> date:
        return self._today or date.today()

    @property
    def cycle(self) -> Cycle | None:
        """The cycle being shown: a past one being browsed, else the current open one."""
        if self.viewing_cycle_id is not None:
            return self.repo.get_cycle(self.viewing_cycle_id)
        return self.repo.current_cycle()

    @property
    def read_only(self) -> bool:
        cycle = self.cycle
        return cycle is not None and cycle.status == "closed"

    def on_mount(self) -> None:
        first_run = not self.repo.is_configured
        self.push_screen(SettingsScreen(onboarding=True) if first_run else DashboardScreen())

    def action_goto(self, name: str) -> None:
        if isinstance(self.screen, ModalScreen):
            return
        if isinstance(self.screen, SCREENS[name]) and name != "dashboard":
            return
        self.switch_screen(SCREENS[name]())

    def new_cycle(self, copy_from: int | None = None) -> None:
        """Ask for name/start date, create the cycle and open the plan."""
        previous = copy_from if copy_from is not None else previous_cycle_id(self.repo)

        def created(result: tuple[str, date, bool] | None) -> None:
            if result is None:
                return
            name, start, copy = result
            create_cycle(self.repo, name, start, previous if copy else None)
            self.viewing_cycle_id = None
            self.notify(f"Created “{name}”. Add goals and tactics, then press Start cycle.")
            self.call_after_refresh(self.action_goto, "plan")

        self.push_screen(NewCycleModal(self.repo.week_start, self.today, previous is not None), created)
