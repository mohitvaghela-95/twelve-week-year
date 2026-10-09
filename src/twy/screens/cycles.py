from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static

from twy.domain.dates import cycle_end
from twy.domain.scoring import cycle_average, format_score, goal_outcome
from twy.screens.base import TwyScreen


class CyclesScreen(TwyScreen):
    def body(self) -> ComposeResult:
        with Vertical(classes="page"):
            yield Static("All cycles. Press Enter to open one (closed cycles are read-only).", classes="section")
            yield DataTable(id="cycles", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        repo = self.app.repo
        table = self.query_one(DataTable)
        table.add_columns("Name", "Start", "End", "Status", "Avg score", "Goals hit")
        for c in repo.list_cycles():
            goals = repo.list_goals(c.id)
            outcomes = [goal_outcome(g, repo.latest_progress(g.id)) for g in goals]
            hit = sum(1 for o in outcomes if o == "hit")
            table.add_row(
                c.name,
                f"{c.start_date:%d %b %Y}",
                f"{cycle_end(c.start_date):%d %b %Y}",
                c.status,
                format_score(cycle_average(repo.weekly_scores(c.id).values())),
                f"{hit}/{len(goals)}" if goals and c.status in ("review", "closed") else "—",
                key=str(c.id),
            )
        table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        cycle_id = int(event.row_key.value)
        current = self.app.repo.current_cycle()
        self.app.viewing_cycle_id = None if current and current.id == cycle_id else cycle_id
        self.app.action_goto("dashboard")
