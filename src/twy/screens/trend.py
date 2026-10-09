from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static

from twy.domain.dates import EXECUTION_WEEKS, week_range
from twy.domain.scoring import cycle_average, format_score, score_band
from twy.screens.base import TwyScreen
from twy.widgets.score_chart import ScoreChart

BAND_STYLE = {"good": "green", "warn": "yellow", "bad": "red", "none": "dim"}


class TrendScreen(TwyScreen):
    def body(self) -> ComposeResult:
        with Vertical(classes="page"):
            yield Static("", id="trend-title", classes="section")
            yield ScoreChart(id="trend-chart", title="Execution score % (red line = 85% target)")
            yield DataTable(id="trend-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        cycle = self.app.cycle
        table = self.query_one(DataTable)
        table.add_columns("Week", "Dates", "Score", "What got in the way", "Fix")
        if cycle is None:
            self.query_one("#trend-title", Static).update("No cycle yet.")
            return

        repo = self.app.repo
        scores = repo.weekly_scores(cycle.id)
        self.query_one("#trend-title", Static).update(
            f"{cycle.name} · average {format_score(cycle_average(scores.values()))} "
            f"over {len(scores)} reviewed week(s)"
        )
        self.query_one(ScoreChart).set_scores(scores)
        for w in range(1, EXECUTION_WEEKS + 1):
            first, last = week_range(cycle.start_date, w)
            review = repo.get_week_review(cycle.id, w)
            score = scores.get(w)
            style = BAND_STYLE[score_band(score)]
            table.add_row(
                str(w),
                f"{first:%d %b} – {last:%d %b}",
                f"[{style}]{format_score(score) if review else 'not reviewed'}[/{style}]",
                review.obstacles if review else "",
                review.fix if review else "",
            )
