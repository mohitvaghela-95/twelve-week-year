"""Modal dialogs: new cycle, goal and tactic editors, confirmation."""

from dataclasses import replace
from datetime import date

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Static

from twy.domain.dates import EXECUTION_WEEKS, WEEKDAY_NAMES, align_to_week_start, cycle_end
from twy.models import Goal, Tactic
from twy.screens.base import fmt_num, parse_float, parse_int


class FormModal(ModalScreen):
    """Common layout: a titled dialog with Save/Cancel; Escape cancels."""

    BINDINGS = [("escape", "cancel", "Cancel")]
    TITLE_TEXT = ""
    SUBMIT_LABEL = "Save"

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.TITLE_TEXT, classes="dialog-title")
            yield from self.fields()
            yield Static("", id="form-error", classes="error")
            with Horizontal(classes="buttons"):
                yield Button(self.SUBMIT_LABEL, variant="primary", id="submit")
                yield Button("Cancel", id="cancel")

    def fields(self) -> ComposeResult:
        yield from ()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            self.submit()
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.submit()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def error(self, message: str) -> None:
        self.query_one("#form-error", Static).update(message)

    def value(self, widget_id: str) -> str:
        return self.query_one(f"#{widget_id}", Input).value

    def submit(self) -> None:
        raise NotImplementedError


class ConfirmModal(FormModal):
    SUBMIT_LABEL = "Yes"

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def fields(self) -> ComposeResult:
        yield Label(self.message)

    def on_mount(self) -> None:
        self.query_one("#submit", Button).focus()

    def submit(self) -> None:
        self.dismiss(True)


class NewCycleModal(FormModal):
    """Returns (name, start_date, copy_previous) or None."""

    TITLE_TEXT = "New 12-week cycle"
    SUBMIT_LABEL = "Create"

    def __init__(self, week_start: int, today: date, can_copy: bool):
        super().__init__()
        self.week_start = week_start
        self.default_start = align_to_week_start(today, week_start)
        self.can_copy = can_copy

    def fields(self) -> ComposeResult:
        yield Label("Name")
        yield Input(f"Cycle from {self.default_start:%d %b %Y}", id="name")
        yield Label(f"Start date (YYYY-MM-DD, snaps to the next {WEEKDAY_NAMES[self.week_start]})")
        yield Input(self.default_start.isoformat(), id="start")
        yield Static("", id="snap", classes="muted")
        if self.can_copy:
            yield Checkbox("Copy goals and tactics from the previous cycle", True, id="copy")

    def on_mount(self) -> None:
        self.update_snap()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "start":
            self.update_snap()

    def snapped(self) -> date | None:
        try:
            return align_to_week_start(date.fromisoformat(self.value("start").strip()), self.week_start)
        except ValueError:
            return None

    def update_snap(self) -> None:
        start = self.snapped()
        text = (
            f"Runs {start:%a %d %b %Y} → {cycle_end(start):%a %d %b %Y}, then week 13 review."
            if start
            else "Enter a date like 2026-10-12."
        )
        self.query_one("#snap", Static).update(text)

    def submit(self) -> None:
        name = self.value("name").strip()
        start = self.snapped()
        if not name:
            return self.error("Name is required.")
        if start is None:
            return self.error("Start date must be YYYY-MM-DD.")
        copy = self.can_copy and self.query_one("#copy", Checkbox).value
        self.dismiss((name, start, copy))


class GoalForm(FormModal):
    TITLE_TEXT = "Goal"

    def __init__(self, goal: Goal):
        super().__init__()
        self.goal = goal

    def fields(self) -> ComposeResult:
        g = self.goal
        yield Label("Goal (specific and measurable)")
        yield Input(g.title, placeholder="e.g. Lose 5 kg", id="title")
        yield Label("Why it matters")
        yield Input(g.why, placeholder="Link to your vision", id="why")
        yield Label("Optional lag measure: unit, starting value, target")
        with Horizontal(classes="inline"):
            yield Input(g.unit or "", placeholder="unit (kg, books…)", id="unit")
            yield Input(fmt_num(g.baseline), placeholder="baseline", type="number", id="baseline")
            yield Input(fmt_num(g.target), placeholder="target", type="number", id="target")

    def submit(self) -> None:
        title = self.value("title").strip()
        if not title:
            return self.error("Goal title is required.")
        try:
            baseline, target = parse_float(self.value("baseline")), parse_float(self.value("target"))
        except ValueError:
            return self.error("Baseline and target must be numbers.")
        if baseline is not None and target is None:
            return self.error("Add a target, or clear the baseline.")
        self.dismiss(
            replace(
                self.goal,
                title=title,
                why=self.value("why").strip(),
                unit=self.value("unit").strip() or None,
                baseline=baseline,
                target=target,
            )
        )


class TacticForm(FormModal):
    TITLE_TEXT = "Tactic"

    def __init__(self, tactic: Tactic, goal_title: str):
        super().__init__()
        self.tactic = tactic
        self.TITLE_TEXT = f"Tactic for “{goal_title}”"

    def fields(self) -> ComposeResult:
        t = self.tactic
        yield Label("Action you control (yes/no each time)")
        yield Input(t.title, placeholder="e.g. Run 30 min", id="title")
        yield Label("Times per week · from week · to week")
        with Horizontal(classes="inline"):
            yield Input(str(t.times_per_week), type="integer", id="times")
            yield Input(str(t.start_week), type="integer", id="start")
            yield Input(str(t.end_week), type="integer", id="end")

    def submit(self) -> None:
        title = self.value("title").strip()
        if not title:
            return self.error("Tactic title is required.")
        try:
            times = parse_int(self.value("times"), 1)
            start = parse_int(self.value("start"), 1)
            end = parse_int(self.value("end"), EXECUTION_WEEKS)
        except ValueError:
            return self.error("Times and weeks must be whole numbers.")
        if times < 1:
            return self.error("Times per week must be at least 1.")
        if not (1 <= start <= end <= EXECUTION_WEEKS):
            return self.error(f"Weeks must satisfy 1 ≤ from ≤ to ≤ {EXECUTION_WEEKS}.")
        self.dismiss(replace(self.tactic, title=title, times_per_week=times, start_week=start, end_week=end))
