"""Server-rendered SVG bar chart of weekly execution scores with the 85% target line."""

from markupsafe import Markup

from twy.domain.dates import EXECUTION_WEEKS
from twy.domain.scoring import TARGET_SCORE, format_score, score_band


def score_chart_svg(scores: dict[int, float | None], compact: bool = False) -> Markup:
    width, height = 360, 150 if compact else 200
    left, right, top, bottom = 36, 30, 14, 20
    plot_w, plot_h = width - left - right, height - top - bottom
    slot = plot_w / EXECUTION_WEEKS
    bar_w = slot * 0.64

    def y(value: float) -> float:
        return top + plot_h * (1 - value)

    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Execution score by week, target {TARGET_SCORE:.0%}">'
    ]
    for value in (0, 0.5, 1):
        parts.append(f'<line class="grid" x1="{left}" x2="{width - right}" y1="{y(value):.1f}" y2="{y(value):.1f}"/>')
        parts.append(f'<text class="axis" x="{left - 6}" y="{y(value) + 3.5:.1f}" text-anchor="end">{value:.0%}</text>')

    for week in range(1, EXECUTION_WEEKS + 1):
        x = left + slot * (week - 1) + (slot - bar_w) / 2
        score = scores.get(week)
        if score is not None:
            bar_h = max(plot_h * score, 1.5)
            parts.append(
                f'<rect class="bar band-{score_band(score)}" x="{x:.1f}" y="{y(0) - bar_h:.1f}" '
                f'width="{bar_w:.1f}" height="{bar_h:.1f}" rx="2"><title>Week {week}: {format_score(score)}</title></rect>'
            )
        elif week not in scores:
            parts.append(f'<rect class="slot" x="{x:.1f}" y="{y(0) - 2:.1f}" width="{bar_w:.1f}" height="2" rx="1"/>')
        parts.append(f'<text class="axis" x="{x + bar_w / 2:.1f}" y="{height - 5}" text-anchor="middle">{week}</text>')

    target_y = y(TARGET_SCORE)
    parts.append(f'<line class="target" x1="{left}" x2="{width - right}" y1="{target_y:.1f}" y2="{target_y:.1f}"/>')
    parts.append(f'<text class="target-label" x="{width - right + 4}" y="{target_y + 3.5:.1f}">{TARGET_SCORE:.0%}</text>')
    parts.append("</svg>")
    return Markup("".join(parts))
