from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Label, Select, Static, TextArea

from twy.domain.dates import current_week
from twy.domain.scoring import cycle_average, format_score, goal_outcome
from twy.models import CycleReview
from twy.screens.base import TwyScreen, fmt_num, parse_float
from twy.screens.forms import ConfirmModal

OUTCOMES = [("✓ Hit", "hit"), ("✗ Missed", "missed")]


class Week13Screen(TwyScreen):
    """Cycle review: goal outcomes, average execution, lessons, then close and start the next cycle."""

    BINDINGS = [("ctrl+s", "save", "Save review")]

    def body(self) -> ComposeResult:
        with VerticalScroll(classes="page"):
            yield Static("", id="w13-summary", classes="card")
            yield Label("Goals", classes="section")
            yield Vertical(id="w13-goals")
            yield Label("Lessons: what worked, what didn't, what will you change?", classes="section")
            yield TextArea(id="lessons", classes="note")
            yield Label("How will you celebrate?", classes="section")
            yield TextArea(id="celebration", classes="note")
            with Horizontal(classes="buttons"):
                yield Button("Save review", variant="primary", id="save")
                yield Button("Close cycle & start next", variant="warning", id="close")

    async def on_mount(self) -> None:
        cycle, repo, app = self.app.cycle, self.app.repo, self.app
        if cycle is None:
            self.query_one("#w13-summary", Static).update("No cycle yet.")
            for w in self.query(Button):
                w.display = False
            return

        scores = repo.weekly_scores(cycle.id)
        pos = current_week(cycle.start_date, app.today)
        early = ""
        if pos.not_started or (pos.week_no is not None and pos.week_no <= 12):
            early = f"\n[yellow]⚠ The cycle is still running (week {pos.week_no or 0}). You can review early, but results may change.[/yellow]"
        self.query_one("#w13-summary", Static).update(
            f"[b]{cycle.name}[/b] · Week 13 review\n"
            f"Average execution: [b]{format_score(cycle_average(scores.values()))}[/b] "
            f"over {len(scores)}/12 reviewed weeks · weeks ≥ 85%: "
            f"{sum(1 for s in scores.values() if s is not None and s >= 0.85)}{early}"
        )

        rows = []
        for g in repo.list_goals(cycle.id):
            if g.measurable:
                latest = repo.latest_progress(g.id)
                final = g.final_value if g.final_value is not None else latest
                unit = f" {g.unit}" if g.unit else ""
                rows.append(
                    Horizontal(
                        Label(
                            f"◆ [b]{g.title}[/b] [dim]{fmt_num(g.baseline) or '?'} → {fmt_num(g.target)}{unit}[/dim]",
                            classes="tactic-title",
                        ),
                        Input(fmt_num(final), placeholder="final", type="number", id=f"final-{g.id}", classes="done",
                              disabled=app.read_only),
                        Static("", id=f"outcome-{g.id}", classes="outcome"),
                        classes="score-row",
                    )
                )
            else:
                rows.append(
                    Horizontal(
                        Label(f"◆ [b]{g.title}[/b]", classes="tactic-title"),
                        Select(OUTCOMES, value=g.outcome or Select.NULL, prompt="Hit or missed?",
                               id=f"manual-{g.id}", classes="outcome-select", disabled=app.read_only),
                        classes="score-row",
                    )
                )
        if not rows:
            rows.append(Static("[dim]This cycle has no goals.[/dim]"))
        await self.query_one("#w13-goals", Vertical).mount_all(rows)
        self.update_outcomes()

        review = repo.get_cycle_review(cycle.id)
        for widget_id in ("lessons", "celebration"):
            area = self.query_one(f"#{widget_id}", TextArea)
            area.text = getattr(review, widget_id) if review else ""
            area.read_only = app.read_only
        for button in self.query(Button):
            button.display = not app.read_only

    def update_outcomes(self) -> None:
        for g in self.app.repo.list_goals(self.app.cycle.id):
            if not g.measurable:
                continue
            try:
                g.final_value = parse_float(self.query_one(f"#final-{g.id}", Input).value)
            except ValueError:
                g.final_value = None
            outcome = goal_outcome(g)
            label = {"hit": "[green]✓ hit[/green]", "missed": "[red]✗ missed[/red]"}.get(outcome, "[dim]—[/dim]")
            self.query_one(f"#outcome-{g.id}", Static).update(label)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id and event.input.id.startswith("final-"):
            self.update_outcomes()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.action_save()
        elif event.button.id == "close":
            self.action_close_cycle()

    def save(self) -> bool:
        cycle, repo = self.app.cycle, self.app.repo
        for g in repo.list_goals(cycle.id):
            if g.measurable:
                try:
                    g.final_value = parse_float(self.query_one(f"#final-{g.id}", Input).value)
                except ValueError:
                    self.notify(f"Final value for “{g.title}” must be a number.", severity="error")
                    return False
                g.outcome = goal_outcome(g)
            else:
                value = self.query_one(f"#manual-{g.id}", Select).value
                g.outcome = value if isinstance(value, str) else None
            repo.save_goal(g)
        repo.save_cycle_review(
            CycleReview(
                cycle.id,
                lessons=self.query_one("#lessons", TextArea).text.strip(),
                celebration=self.query_one("#celebration", TextArea).text.strip(),
            )
        )
        if cycle.status != "closed":
            repo.set_cycle_status(cycle.id, "review")
        return True

    def action_save(self) -> None:
        if self.app.cycle is None or self.guard_read_only():
            return
        if self.save():
            self.notify("Week 13 review saved.")

    def action_close_cycle(self) -> None:
        cycle = self.app.cycle
        if cycle is None or self.guard_read_only() or not self.save():
            return

        def confirmed(ok: bool | None) -> None:
            if not ok:
                return
            self.app.repo.set_cycle_status(cycle.id, "closed")
            self.notify(f"“{cycle.name}” closed. Well done.")
            from twy.screens.dashboard import DashboardScreen

            self.app.switch_screen(DashboardScreen(start_new_cycle=True, copy_from=cycle.id))

        self.app.push_screen(
            ConfirmModal(f"Close “{cycle.name}”? It becomes read-only and you can start the next cycle."),
            confirmed,
        )
