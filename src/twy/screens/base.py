"""Shared screen base; parsing helpers are re-exported from twy.services."""

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header

from twy.services import fmt_num, parse_float, parse_int  # noqa: F401 (re-exported for screens)

if TYPE_CHECKING:
    from twy.app import TwyApp


class TwyScreen(Screen):
    """A full screen with the app header and footer around `body()`."""

    app: "TwyApp"

    def compose(self) -> ComposeResult:
        yield Header()
        yield from self.body()
        yield Footer()

    def body(self) -> ComposeResult:
        yield from ()

    def guard_read_only(self) -> bool:
        """Notify and return True if the cycle being viewed is closed."""
        if self.app.read_only:
            self.notify("This cycle is closed and read-only.", severity="warning")
            return True
        return False
