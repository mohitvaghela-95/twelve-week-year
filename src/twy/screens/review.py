from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Label, Select, Static, TextArea

from twy.domain.dates import week_range
from twy.domain.scoring import execution_score, format_score, score_band
from twy.models import WeekEntry
from twy.screens.base import TwyScreen, fmt_num, parse_float, parse_int
from twy.services import ReviewRow, default_review_week, review_rows, reviewable_weeks, save_week, week_entries


class ReviewScreen(TwyScreen):
    """End-of-week scorecard: done counts per tactic, reflections and lag readings."""

    BINDINGS = [("ctrl+s", "save", "Save review")]

    def __init__(self, week_no: int | None = None):
        super().__init__()
        self.requested_week = week_no
        self.week_no: int | None = None
        self.rows: list[ReviewRow] = []
        self.measurable_goal_ids: list[int] = []

    def body(self) -> ComposeResult:
        with Vertical(classes="page"):
            with Horizontal(id="review-head"):
                yield Label("Week", classes="inline-label")
                yield Select([], allow_blank=True, prompt="—", id="week")
                yield Static("", id="week-dates", classes="muted")
                yield Static("", id="live-score", classes="score-pill")
            with VerticalScroll(id="review-body"):
                yield Static("", id="review-message")
                yield Label("Scorecard: how many times did you do each tactic?", classes="section")
                yield Vertical(id="rows")
                yield Vertical(id="lag")
                yield Label("What got in the way?", classes="section")
                yield TextArea(id="obstacles", classes="note")
                yield Label("One fix for next week", classes="section")
                yield TextArea(id="fix", classes="note")
                yield Button("Save review", variant="primary", id="save")

    async def on_mount(self) -> None:
        cycle = self.app.cycle
        if cycle is None:
            return self._message("No cycle yet. Go to the dashboard (d) and press n.")
        today = self.app.today
        weeks = reviewable_weeks(cycle, today)
        if not weeks:
            return self._message(f"This cycle starts on {cycle.start_date:%a %d %b}. Nothing to review yet.")

        reviewed = self.app.repo.reviewed_weeks(cycle.id)
        select = self.query_one("#week", Select)
        select.set_options([(f"{w}{' ✓' if w in reviewed else ''}", w) for w in weeks])
        default = self.requested_week or default_review_week(self.app.repo, cycle, today)
        select.value = min(default, weeks[-1])  # triggers Select.Changed -> load_week

    def _message(self, text: str) -> None:
        self.query_one("#review-message", Static).update(text)
        for widget_id in ("#rows", "#obstacles", "#fix", "#save"):
            self.query_one(widget_id).display = False

    async def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "week" and isinstance(event.value, int):
            await self.load_week(event.value)

    async def load_week(self, week_no: int) -> None:
        cycle, repo = self.app.cycle, self.app.repo
        self.week_no = week_no
        first, last = week_range(cycle.start_date, week_no)
        self.query_one("#week-dates", Static).update(f"{first:%a %d %b} – {last:%a %d %b}")

        existing = repo.get_week_review(cycle.id, week_no)
        goals = {g.id: g for g in repo.list_goals(cycle.id)}
        read_only = self.app.read_only

        rows_box = self.query_one("#rows", Vertical)
        await rows_box.remove_children()
        self.rows = review_rows(repo, cycle, week_no, existing)
        widgets = [
            Horizontal(
                Label(f"{r.goal_title} ▸ [b]{r.title}[/b]", classes="tactic-title"),
                Input(str(r.done), type="integer", id=f"done-{r.tactic_id}", classes="done", disabled=read_only),
                Label(f"/ {r.planned}", classes="planned"),
                classes="score-row",
            )
            for r in self.rows
        ]
        if not widgets:
            widgets.append(Static("[dim]No tactics planned for this week.[/dim]"))
        await rows_box.mount_all(widgets)

        lag_box = self.query_one("#lag", Vertical)
        await lag_box.remove_children()
        self.measurable_goal_ids = [g.id for g in goals.values() if g.measurable]
        if self.measurable_goal_ids:
            lag_widgets = [Label("Lag measures (optional): current value", classes="section")]
            for gid in self.measurable_goal_ids:
                g = goals[gid]
                value = repo.goal_progress(gid).get(week_no)
                unit = f" {g.unit}" if g.unit else ""
                lag_widgets.append(
                    Horizontal(
                        Label(f"{g.title} [dim](target {fmt_num(g.target)}{unit})[/dim]", classes="tactic-title"),
                        Input(fmt_num(value), type="number", id=f"lag-{gid}", classes="done", disabled=read_only),
                        Label(g.unit or "", classes="planned"),
                        classes="score-row",
                    )
                )
            await lag_box.mount_all(lag_widgets)

        for widget_id, text in (("obstacles", existing.obstacles if existing else ""), ("fix", existing.fix if existing else "")):
            area = self.query_one(f"#{widget_id}", TextArea)
            area.text = text
            area.read_only = read_only
        self.query_one("#save", Button).disabled = read_only
        self.query_one("#review-message", Static).update(
            f"[dim]Reviewed {existing.reviewed_at:%d %b %H:%M}. Editing updates it.[/dim]" if existing else ""
        )
        self.update_score()

    def entries(self) -> list[WeekEntry]:
        done = {}
        for r in self.rows:
            try:
                done[r.tactic_id] = parse_int(self.query_one(f"#done-{r.tactic_id}", Input).value)
            except ValueError:
                done[r.tactic_id] = 0
        return week_entries(self.app.cycle.id, self.week_no, self.rows, done)

    def update_score(self) -> None:
        score = execution_score(self.entries())
        pill = self.query_one("#live-score", Static)
        pill.remove_class("good", "warn", "bad", "none")
        pill.add_class(score_band(score))
        pill.update(f"Score {format_score(score)}  (target 85%)")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id and event.input.id.startswith("done-"):
            self.update_score()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.action_save()

    def action_save(self) -> None:
        if self.week_no is None or self.guard_read_only():
            return
        progress: dict[int, float | None] = {}
        for gid in self.measurable_goal_ids:
            try:
                progress[gid] = parse_float(self.query_one(f"#lag-{gid}", Input).value)
            except ValueError:
                self.notify("Lag measures must be numbers.", severity="error")
                return
        score = save_week(
            self.app.repo,
            self.app.cycle,
            self.week_no,
            self.entries(),
            self.query_one("#obstacles", TextArea).text,
            self.query_one("#fix", TextArea).text,
            progress,
        )
        self.notify(f"Week {self.week_no} saved: {format_score(score)}.")
        self.app.action_goto("dashboard")
