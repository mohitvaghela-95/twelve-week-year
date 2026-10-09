from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Button, Label, Static, TextArea

from twy.screens.base import TwyScreen


class VisionScreen(TwyScreen):
    BINDINGS = [("ctrl+s", "save", "Save")]

    def __init__(self, onboarding: bool = False):
        super().__init__()
        self.onboarding = onboarding

    def body(self) -> ComposeResult:
        repo = self.app.repo
        with VerticalScroll(classes="page"):
            yield Static(
                "Your vision is the reason to do the hard tactics. Cover health, work, money, "
                "relationships and growth. A few honest paragraphs is enough.",
                classes="intro",
            )
            yield Label("Long-term vision (10+ years)", classes="section")
            yield TextArea(repo.get_vision("long_term"), id="long_term", classes="vision")
            yield Label("3-year vision", classes="section")
            yield TextArea(repo.get_vision("three_year"), id="three_year", classes="vision")
            yield Button("Save" if not self.onboarding else "Next: create cycle →", variant="primary", id="save")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.action_save()

    def action_save(self) -> None:
        for horizon in ("long_term", "three_year"):
            self.app.repo.set_vision(horizon, self.query_one(f"#{horizon}", TextArea).text)
        self.notify("Vision saved.")
        if self.onboarding:
            from twy.screens.dashboard import DashboardScreen

            self.app.switch_screen(DashboardScreen(start_new_cycle=True))
