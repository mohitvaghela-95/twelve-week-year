# twy: The 12 Week Year in your terminal

A keyboard-driven terminal app for running *The 12 Week Year* (Moran & Lennington): vision → 12-week goals → tactics → an end-of-week **execution score** with an **85%** target, then a Week 13 review.

## Install & run

```bash
uv sync
uv run twy
```

On first run, twy asks for your week start day and your vision, then creates your first cycle.

## Keys

| Key | Screen | |
|---|---|---|
| `d` / `Esc` | Dashboard | current week, last and average score, trend, goals |
| `p` | Plan | `g` add goal · `a` add tactic · `e`/Enter edit · `⌫` delete · `[` `]` reorder · **Start cycle** |
| `r` | Weekly review | done count per tactic, live score, reflections, lag values · `Ctrl+S` save |
| `t` | Trend | 12-week chart with the 85% line plus weekly notes |
| `w` | Week 13 | final values, hit/missed, lessons · **Close cycle & start next** |
| `c` | Cycles | browse past cycles (closed cycles are read-only; `x` on the dashboard returns) |
| `v` / `s` | Vision / Settings | |
| `q` | Quit | |

## How scoring works

- A tactic like "Run 3×/week" adds **3 items** to each week in its range.
- Execution score = items done ÷ items planned. Weeks with nothing planned are skipped.
- Each review **snapshots** what was planned, so later tactic edits don't rewrite past weeks. Deleting a tactic does remove it from history.
- Goals can optionally have a lag measure (unit, baseline, target). Direction is inferred, so 82 → 77 kg means lower is better. Goals without a target are marked hit or missed by hand at Week 13.
- Soft warnings: more than 3 goals, more than ~8 tactics, more than 15 items in a week, or a goal without tactics. They warn but never block.

## Data

Everything lives in one SQLite file: `~/Library/Application Support/twy/twy.db` on macOS (the Settings screen shows the path). To back up, copy that file. Set `TWY_DB=/path/to/file.db` to use a different file.

## Try it with demo data

```bash
TWY_DB=/tmp/twy-demo.db uv run python scripts/seed_demo.py
TWY_DB=/tmp/twy-demo.db uv run twy
```

`TWY_TODAY=2026-11-20` pretends today is another date, which is useful for trying the Week 13 flow.

## Development

```bash
uv run pytest
```

Layout: `domain/` (pure week math, scoring, guardrails) · `repo.py` (all SQL) · `screens/` (Textual UI) · `widgets/score_chart.py`.
