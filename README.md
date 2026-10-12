# twy: The 12 Week Year on your home network

A phone-friendly web app for running *The 12 Week Year* (Moran & Lennington): vision → 12-week goals → tactics → an end-of-week **execution score** with an **85%** target, then a Week 13 review. It runs in Docker on a home server, and one password protects it.

## What's in it

- **Home:** the current week, your last and average score, a mini trend, and goals with their lag measures.
- **Plan:** goals and tactics, reordering, soft warnings and items per week, then **Start cycle**.
- **Review:** a −/+ done count per tactic, a live score, lag values, obstacles and a fix.
- **Trend:** a 12-week chart with the 85% line, plus weekly notes.
- **Week 13:** final values, hit or missed, lessons, then **Close cycle & start next** (which can copy the plan).
- **Week:** this week's time blocks, as an agenda on the phone and a timeline on desktop. Overlaps are flagged.
- **Cycles, Vision, Settings:** past cycles are read-only. Settings has the backup download and the calendar link.

## Calendar

Give a tactic **calendar times** (day, start time and minutes, e.g. Study: Mon–Fri 07:00 for 60 min). Add **time blocks** on the Plan page for admin, chores, breakout and your weekly review. Blocks belong to the cycle and are copied with "copy previous plan", but they aren't scored.

While you enter times, twy warns when one overlaps another tactic or block in a week where both run, and offers to **Move to** the nearest free time before or after. Free times are looked for between the hours in Settings → **Suggest times between** (default 06:00–22:00). Clashes never stop a save; any left over are listed on the Plan page.

Settings → **Calendar** has a private subscribe link. Tap **Subscribe on this device** on your iPhone, or on a Mac use Calendar → File → New Calendar Subscription. The calendar shows this week's sessions. From the day of your weekly review block, it also shows next week. Your device fetches it from the home server, so it updates while you're on your home Wi-Fi (Google and Outlook's cloud can't reach your home network). **Reset link** stops old subscriptions from working.

## How scoring works

- A tactic like "Run 3×/week" adds **3 items** to each week in its range.
- Execution score = items done ÷ items planned. Weeks with nothing planned are skipped.
- Each review **snapshots** what was planned, so later tactic edits don't rewrite past weeks. Deleting a tactic does remove it from history.
- Goals can optionally have a lag measure (unit, baseline, target). The direction is inferred, so 82 → 77 kg means lower is better. Goals without a target are marked hit or missed by hand at Week 13.
- Soft warnings appear for more than 3 goals, more than ~8 tactics, more items in a week than your limit (Settings, default 25), or a goal with no tactics. They warn but never block.

## Running it on your home server

### What you need

- A Linux PC or mini PC on your home network with Docker and the compose plugin. Your server user must be able to run `docker`, so add it to the `docker` group.
- A fixed IP address for the server. Set up a DHCP reservation on your router so the address doesn't change.

### Option A: git

On the server:

```bash
git clone https://github.com/mohitvaghela-95/twelve-week-year.git ~/twy
cd ~/twy
cp .env.example .env
sed -i "s/^TWY_UID=.*/TWY_UID=$(id -u)/; s/^TWY_GID=.*/TWY_GID=$(id -g)/" .env
mkdir -p data
nano .env
docker compose up -d --build
```

In `nano`, set `TWY_PASSWORD`. Create `data/` **before** the first `docker compose up`. Otherwise Docker creates it owned by root, and the app can't open its database.

To update, run this in `~/twy`:

```bash
git pull && docker compose up -d --build
```

### Option B: rsync from this Mac

```bash
scripts/deploy.sh you@server-ip
```

The first run copies the project to `~/twy`, creates `.env` and stops. Set the password, then run the script again:

```bash
ssh -t you@server-ip nano twy/.env
```

`deploy.sh` saves the server in `deploy.env`, so later updates are just `scripts/deploy.sh`. It never touches the server's `data/` folder or `.env`.

### Then

Open `http://<server-ip>:8000` on your phone. In Safari, choose Share → **Add to Home Screen** to get an app-like icon.

### Good to know

- **Home network only.** It uses plain HTTP, so don't forward port 8000 on your router. To use it away from home, set up a VPN into your network (such as Tailscale or WireGuard) rather than exposing the app.
- **Backups:** Settings → **Download backup** saves a copy of the database. The live file is `~/twy/data/twy.db` on the server.
- **Settings** live in `~/twy/.env`: `TWY_PASSWORD`, `TZ` (so "today" matches your clock) and `TWY_PORT`. After changing them, run `docker compose up -d` in `~/twy`.
- **Logs:** run `docker compose logs -f` in `~/twy`.

## Try it on this Mac

```bash
TWY_DB=/tmp/twy-demo.db uv run python scripts/seed_demo.py
TWY_PASSWORD=demo TWY_DB=/tmp/twy-demo.db uv run twy-web
```

Then open http://localhost:8000. To try the Week 13 flow, set `TWY_TODAY=2026-11-20` to pretend today is a different date.

## Development

```bash
uv run pytest
```

The code is laid out as follows:
- `domain/`: pure week math, scoring and guardrails.
- `repo.py`: all the SQL.
- `services.py`: the app rules, kept out of the routes.
- `web/`: the Flask app, templates and static files.
- `Dockerfile`, `compose.yaml` and `scripts/deploy.sh`: the home server setup.
