"""Shared screen base and small parsing helpers."""

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header

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


def parse_float(text: str) -> float | None:
    text = text.strip()
    return float(text) if text else None


def parse_int(text: str, default: int = 0) -> int:
    text = text.strip()
    return int(text) if text else default


def fmt_num(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:g}"
