# twy: The 12 Week Year in your terminal and browser

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

## Web app on your home server

The same app as a phone-friendly web page you can use from any device on your home network. It covers everything the terminal app does: dashboard, plan, weekly review with −/+ steppers and a live score, trend, Week 13, cycles, vision and settings. One password protects it, and each device stays logged in for 30 days.

### What you need

- A Linux PC or mini PC on your home network with Docker and the compose plugin, and SSH access to it from this Mac. Your server user must be able to run `docker` (add it to the `docker` group).
- Give the server a fixed IP address with a DHCP reservation on your router, so the address doesn't change.

### First deploy

```bash
scripts/deploy.sh you@server-ip --copy-db
```

The first run copies the project to `~/twy` on the server and creates `~/twy/.env`, then stops. Set a password in that file:

```bash
ssh you@server-ip nano twy/.env
```

Then run the same command again. It copies this Mac's database to the server once (`--copy-db`), builds the image, and starts it. Open `http://<server-ip>:8000` on your phone. In Safari, use Share → **Add to Home Screen** for an app-like icon.

The server is now where your data lives. `deploy.sh` saves the server in `deploy.env`, so the Desktop alias (`twy.command`) opens the terminal app on the server over SSH (`docker exec -it twy twy`).

### Updating

Run `scripts/deploy.sh` again; it remembers the server. It never touches the server's `data/` folder or `.env`.

### Good to know

- **Home network only.** It uses plain HTTP, so don't forward port 8000 on your router. For access away from home, use a VPN into your network (such as Tailscale or WireGuard) rather than exposing it.
- **Backups:** Settings → **Download backup** saves a copy of the database. The live file is `~/twy/data/twy.db` on the server.
- **Settings** live in `~/twy/.env`: `TWY_PASSWORD`, `TZ` (so "today" matches your clock), `TWY_PORT`. Run `docker compose up -d` in `~/twy` after changing them.
- **Logs:** `docker compose logs -f` in `~/twy`.

### Try it on this Mac

```bash
TWY_PASSWORD=demo TWY_DB=/tmp/twy-demo.db uv run twy-web
```

Then open http://localhost:8000.

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

Layout: `domain/` (pure week math, scoring, guardrails) · `repo.py` (all SQL) · `services.py` (rules shared by both UIs) · `screens/` + `widgets/` (Textual UI) · `web/` (Flask app, templates, static files) · `Dockerfile` + `compose.yaml` + `scripts/deploy.sh` (home server).
