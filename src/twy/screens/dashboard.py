from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Static

from twy.domain.dates import EXECUTION_WEEKS, current_week, cycle_end, last_finished_week, week_range
from twy.domain.scoring import cycle_average, format_score, goal_outcome, score_band
from twy.screens.base import TwyScreen, fmt_num
from twy.widgets.score_chart import ScoreChart


class DashboardScreen(TwyScreen):
    BINDINGS = [
        ("n", "new_cycle", "New cycle"),
        ("x", "back_to_current", "Current cycle"),
    ]

    def __init__(self, start_new_cycle: bool = False, copy_from: int | None = None):
        super().__init__()
        # Open the new-cycle dialog once mounted, so its result callback belongs to this screen.
        self.start_new_cycle = start_new_cycle
        self.copy_from = copy_from

    def body(self) -> ComposeResult:
        with VerticalScroll(classes="page"):
            yield Static("", id="banner")
            with Horizontal(id="stats"):
                yield Static("", id="cycle-info", classes="card")
                yield Static("", id="last-score", classes="card score")
                yield Static("", id="avg-score", classes="card score")
            yield ScoreChart(id="mini-chart")
            with Vertical(id="goals", classes="card"):
                yield Static("", id="goal-list")

    def on_mount(self) -> None:
        self.refresh_view()
        if self.start_new_cycle:
            self.app.new_cycle(copy_from=self.copy_from)

    def on_screen_resume(self) -> None:
        self.refresh_view()

    def refresh_view(self) -> None:
        app, repo = self.app, self.app.repo
        cycle = app.cycle
        banner = self.query_one("#banner", Static)
        banner.remove_class("warn", "info")

        if cycle is None:
            banner.update("No active cycle. Press [b]n[/b] to start a new 12-week cycle, or [b]c[/b] to browse past ones.")
            banner.add_class("info")
            for card in ("#cycle-info", "#last-score", "#avg-score", "#goal-list"):
                self.query_one(card, Static).update("")
            self.query_one(ScoreChart).set_scores({})
            return

        today = app.today
        pos = current_week(cycle.start_date, today)
        scores = repo.weekly_scores(cycle.id)
        reviewed = repo.reviewed_weeks(cycle.id)
        pending = [w for w in range(1, last_finished_week(cycle.start_date, today) + 1) if w not in reviewed]

        if app.read_only:
            banner.update(f"Viewing closed cycle “{cycle.name}” (read-only). Press [b]x[/b] to return to the current cycle.")
            banner.add_class("info")
        elif cycle.status == "planning":
            banner.update("Cycle is in planning. Press [b]p[/b] to add goals and tactics, then start the cycle.")
            banner.add_class("info")
        elif pending:
            weeks = ", ".join(str(w) for w in pending)
            banner.update(f"Weekly review pending for week {weeks}. Press [b]r[/b] to score it.")
            banner.add_class("warn")
        elif pos.in_review_week or pos.finished:
            banner.update("Week 12 is done. Press [b]w[/b] for your Week 13 review.")
            banner.add_class("info")
        else:
            banner.update("")

        if pos.not_started:
            where = f"Starts {cycle.start_date:%a %d %b}"
        elif pos.finished:
            where = "Finished"
        elif pos.in_review_week:
            where = "Week 13 · review"
        else:
            first, last = week_range(cycle.start_date, pos.week_no)
            where = f"Week {pos.week_no} of {EXECUTION_WEEKS}  ({first:%d %b} – {last:%d %b})"
        self.query_one("#cycle-info", Static).update(
            f"[b]{cycle.name}[/b]  ·  {cycle.status}\n{where}\n"
            f"[dim]{cycle.start_date:%d %b %Y} → {cycle_end(cycle.start_date):%d %b %Y}[/dim]"
        )

        last_week = max(scores) if scores else None
        last_score = scores[last_week] if last_week else None
        self._score_card("#last-score", f"Week {last_week}" if last_week else "Last week", last_score)
        self._score_card("#avg-score", f"Average ({len(scores)} wk)", cycle_average(scores.values()))
        self.query_one(ScoreChart).set_scores(scores)

        lines = ["[b]Goals[/b]"]
        for goal in repo.list_goals(cycle.id):
            latest = repo.latest_progress(goal.id)
            line = f"◆ {goal.title}"
            if goal.measurable:
                unit = f" {goal.unit}" if goal.unit else ""
                now = fmt_num(goal.final_value if goal.final_value is not None else latest) or "—"
                line += f"   [dim]{fmt_num(goal.baseline) or '?'} → {fmt_num(goal.target)}{unit}, now {now}[/dim]"
            outcome = goal_outcome(goal, latest)
            if outcome and (pos.in_review_week or pos.finished or cycle.status in ("review", "closed")):
                line += "  [green]✓ hit[/green]" if outcome == "hit" else "  [red]✗ missed[/red]"
            lines.append(line)
        if len(lines) == 1:
            lines.append("[dim]No goals yet. Press p to plan.[/dim]")
        self.query_one("#goal-list", Static).update("\n".join(lines))

    def _score_card(self, selector: str, label: str, score: float | None) -> None:
        card = self.query_one(selector, Static)
        card.remove_class("good", "warn", "bad", "none")
        card.add_class(score_band(score))
        card.update(f"{label}\n[b]{format_score(score)}[/b]\n[dim]target 85%[/dim]")

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        # Only show keys that apply right now.
        if action == "new_cycle":
            return self.app.repo.current_cycle() is None
        if action == "back_to_current":
            return self.app.viewing_cycle_id is not None
        return True

    def action_new_cycle(self) -> None:
        if self.app.repo.current_cycle() is not None:
            self.notify("Finish the current cycle first (Week 13 → Close cycle).", severity="warning")
            return
        self.app.new_cycle()

    def action_back_to_current(self) -> None:
        self.app.viewing_cycle_id = None
        self.refresh_view()
        self.refresh_bindings()
