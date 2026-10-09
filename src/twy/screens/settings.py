from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Label, Select, Static

from twy.domain.dates import WEEKDAY_NAMES
from twy.screens.base import TwyScreen


class SettingsScreen(TwyScreen):
    BINDINGS = [("ctrl+s", "save", "Save")]

    def __init__(self, onboarding: bool = False):
        super().__init__()
        self.onboarding = onboarding

    def body(self) -> ComposeResult:
        with Vertical(classes="page"):
            if self.onboarding:
                yield Static(
                    "Welcome! The 12 Week Year treats 12 weeks as your whole year.\n"
                    "First, pick the day your weeks start. Then write your vision and plan your first cycle.",
                    classes="intro",
                )
            yield Label("Week starts on", classes="section")
            yield Select(
                [(name, i) for i, name in enumerate(WEEKDAY_NAMES)],
                value=self.app.repo.week_start,
                allow_blank=False,
                id="week-start",
            )
            yield Static("Applies to new cycles; existing cycles keep their dates.", classes="muted")
            yield Static(f"Data file: {self.app.db_path}", classes="muted")
            yield Button("Save" if not self.onboarding else "Next: vision →", variant="primary", id="save")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.action_save()

    def action_save(self) -> None:
        value = self.query_one("#week-start", Select).value
        self.app.repo.set_setting("week_start", str(value))
        self.notify(f"Weeks start on {WEEKDAY_NAMES[value]}.")
        if self.onboarding:
            from twy.screens.vision import VisionScreen

            self.app.switch_screen(VisionScreen(onboarding=True))
        else:
            self.app.action_goto("dashboard")
