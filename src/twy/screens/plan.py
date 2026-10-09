from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Static, Tree

from twy.domain.dates import EXECUTION_WEEKS
from twy.domain.guardrails import plan_warnings
from twy.domain.scoring import planned_items
from twy.models import Goal, Tactic
from twy.screens.base import TwyScreen, fmt_num
from twy.screens.forms import ConfirmModal, GoalForm, TacticForm


class PlanScreen(TwyScreen):
    BINDINGS = [
        ("g", "add_goal", "Add goal"),
        ("a", "add_tactic", "Add tactic"),
        ("e", "edit", "Edit"),
        Binding("delete,backspace", "delete", "Delete"),
        Binding("[", "move(-1)", "Move up", show=False),
        Binding("]", "move(1)", "Move down", show=False),
    ]

    def body(self) -> ComposeResult:
        with Horizontal(classes="page"):
            with Vertical(id="plan-main"):
                yield Static("", id="plan-title", classes="section")
                tree: Tree = Tree("plan", id="plan-tree")
                tree.show_root = False
                yield tree
            with Vertical(id="plan-side"):
                yield Static("", id="warnings", classes="card")
                yield Static("", id="load", classes="card")
                yield Static(
                    "[b]Keys[/b]\n g  add goal\n a  add tactic to selected goal\n e  edit selected\n"
                    " ⌫  delete selected\n [ ]  reorder",
                    classes="card muted",
                )
                yield Button("Start cycle", variant="success", id="start")

    def on_mount(self) -> None:
        self.refresh_view()
        self.query_one(Tree).focus()

    def refresh_view(self, select: tuple[str, int] | None = None) -> None:
        cycle = self.app.cycle
        tree = self.query_one(Tree)
        tree.clear()
        start = self.query_one("#start", Button)
        if cycle is None:
            self.query_one("#plan-title", Static).update("No cycle. Go to the dashboard (d) and press n.")
            start.display = False
            return

        self.query_one("#plan-title", Static).update(f"Plan · {cycle.name}  [dim]({cycle.status})[/dim]")
        start.display = cycle.status == "planning"
        goals = self.app.repo.list_goals(cycle.id)
        tactics = self.app.repo.list_tactics(cycle.id)
        target_node = None
        for goal in goals:
            label = f"[b]◆ {goal.title}[/b]"
            if goal.measurable:
                unit = f" {goal.unit}" if goal.unit else ""
                label += f"  [dim]{fmt_num(goal.baseline) or '?'} → {fmt_num(goal.target)}{unit}[/dim]"
            node = tree.root.add(label, data=("goal", goal.id), expand=True)
            if select == ("goal", goal.id):
                target_node = node
            for t in (t for t in tactics if t.goal_id == goal.id):
                leaf = node.add_leaf(
                    f"{t.title}  [dim]{t.times_per_week}×/wk · weeks {t.start_week}–{t.end_week}[/dim]",
                    data=("tactic", t.id),
                )
                if select == ("tactic", t.id):
                    target_node = leaf
        if not goals:
            tree.root.add_leaf("[dim]No goals yet. Press g to add one (1–3 is ideal).[/dim]")
        if target_node is not None:
            self.call_after_refresh(tree.move_cursor, target_node)

        warnings = plan_warnings(goals, tactics)
        self.query_one("#warnings", Static).update(
            "[b]Checks[/b]\n" + ("\n".join(f"[yellow]⚠[/yellow] {w}" for w in warnings) or "[green]✓ Plan looks focused.[/green]")
        )
        load = "  ".join(f"{w}:{planned_items(tactics, w)}" for w in range(1, EXECUTION_WEEKS + 1))
        self.query_one("#load", Static).update(f"[b]Items per week[/b]\n{load}")

    # -- selection helpers --------------------------------------------------------

    def selected(self) -> tuple[str, int] | None:
        node = self.query_one(Tree).cursor_node
        return node.data if node is not None and node.data else None

    def selected_goal_id(self) -> int | None:
        sel = self.selected()
        if sel is None:
            return None
        kind, item_id = sel
        return item_id if kind == "goal" else self.app.repo.get_tactic(item_id).goal_id

    # -- actions ------------------------------------------------------------------

    def action_add_goal(self) -> None:
        cycle = self.app.cycle
        if cycle is None or self.guard_read_only():
            return

        def saved(goal: Goal | None) -> None:
            if goal:
                goal = self.app.repo.save_goal(goal)
                self.refresh_view(select=("goal", goal.id))

        self.app.push_screen(GoalForm(Goal(id=None, cycle_id=cycle.id, title="")), saved)

    def action_add_tactic(self) -> None:
        if self.guard_read_only():
            return
        goal_id = self.selected_goal_id()
        if goal_id is None:
            self.notify("Select a goal first (or press g to add one).", severity="warning")
            return
        goal = self.app.repo.get_goal(goal_id)

        def saved(tactic: Tactic | None) -> None:
            if tactic:
                tactic = self.app.repo.save_tactic(tactic)
                self.refresh_view(select=("tactic", tactic.id))

        self.app.push_screen(TacticForm(Tactic(id=None, goal_id=goal_id, title=""), goal.title), saved)

    def action_edit(self) -> None:
        sel = self.selected()
        if sel is None or self.guard_read_only():
            return
        kind, item_id = sel
        repo = self.app.repo
        if kind == "goal":
            def saved_goal(goal: Goal | None) -> None:
                if goal:
                    repo.save_goal(goal)
                    self.refresh_view(select=sel)

            self.app.push_screen(GoalForm(repo.get_goal(item_id)), saved_goal)
        else:
            tactic = repo.get_tactic(item_id)

            def saved_tactic(t: Tactic | None) -> None:
                if t:
                    repo.save_tactic(t)
                    self.refresh_view(select=sel)

            self.app.push_screen(TacticForm(tactic, repo.get_goal(tactic.goal_id).title), saved_tactic)

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        # Enter on a tactic edits it; on a goal, Tree toggles expansion.
        if event.node.data and event.node.data[0] == "tactic":
            self.action_edit()

    def action_delete(self) -> None:
        sel = self.selected()
        if sel is None or self.guard_read_only():
            return
        kind, item_id = sel
        repo = self.app.repo
        if kind == "goal":
            message = f"Delete goal “{repo.get_goal(item_id).title}” and all its tactics?"
        else:
            message = f"Delete tactic “{repo.get_tactic(item_id).title}”? Its past weekly scores are removed too."

        def confirmed(ok: bool | None) -> None:
            if ok:
                (repo.delete_goal if kind == "goal" else repo.delete_tactic)(item_id)
                self.refresh_view()

        self.app.push_screen(ConfirmModal(message), confirmed)

    def action_move(self, delta: int) -> None:
        sel = self.selected()
        if sel is None or self.guard_read_only():
            return
        kind, item_id = sel
        (self.app.repo.move_goal if kind == "goal" else self.app.repo.move_tactic)(item_id, delta)
        self.refresh_view(select=sel)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start" and not self.guard_read_only():
            cycle = self.app.cycle
            if not self.app.repo.list_tactics(cycle.id):
                self.notify("Add at least one tactic before starting.", severity="warning")
                return
            self.app.repo.set_cycle_status(cycle.id, "active")
            self.notify(f"“{cycle.name}” is active. Review each week with r.")
            self.refresh_view()
