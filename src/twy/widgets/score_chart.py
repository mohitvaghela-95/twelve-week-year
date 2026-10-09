"""Bar chart of weekly execution scores with the 85% target line."""

from textual_plotext import PlotextPlot

from twy.domain.dates import EXECUTION_WEEKS
from twy.domain.scoring import TARGET_SCORE


class ScoreChart(PlotextPlot):
    def __init__(self, scores: dict[int, float | None] | None = None, *, title: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self._scores = scores or {}
        self._title = title

    def on_mount(self) -> None:
        self.replot()

    def set_scores(self, scores: dict[int, float | None]) -> None:
        self._scores = scores
        self.replot()

    def replot(self) -> None:
        plt = self.plt
        plt.clear_data()
        weeks = list(range(1, EXECUTION_WEEKS + 1))
        # Unreviewed weeks get a zero-height bar, so they show as blank.
        values = [round((self._scores.get(w) or 0) * 100) for w in weeks]
        plt.bar([str(w) for w in weeks], values, width=0.6)
        plt.hline(TARGET_SCORE * 100, "red")
        plt.ylim(0, 100)
        plt.yticks([0, 25, 50, 85, 100])
        if self._title:
            plt.title(self._title)
        plt.xlabel("Week")
        self.refresh()
