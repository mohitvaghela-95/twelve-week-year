# twy: The 12 Week Year on your home network

A phone-friendly web app for running *The 12 Week Year* (Moran & Lennington): vision → 12-week goals → tactics → an end-of-week **execution score** with an **85%** target, then a Week 13 review. It runs in Docker on a home server, and one password protects it.

## What's in it

- **Home:** what you've logged today (one tap to Today), the current week, your last and average score, a mini trend, and goals with their lag measures.
- **Plan:** **Start cycle** at the top while you're planning, then goals and tactics (tap one to edit or delete it, ↑/↓ to reorder), soft warnings and actions per week.
- **Today:** tap + as you finish each tactic, see how the week is going, and write a short daily reflection.
- **Review:** a −/+ done count per tactic (starting from what you logged on Today), lag values and your weekly reflection, with the live score and Save pinned to the bottom. Saving goes straight on to the next week still to score.
- **Trend:** a 12-week chart with the 85% line, plus weekly notes.
- **Week 13:** final values, hit or missed, lessons, then **Close cycle & start next** (which can copy the plan).
- **Week:** the week's sessions, as an agenda on the phone and a timeline on desktop. Overlaps are flagged. **Plan this week** places your tactics; tap a session for its details and an Edit button.
- **Cycles, Vision, Settings:** past cycles are read-only. Settings has your reflection prompts, the backup download and the calendar link.

On a phone the tab bar holds Home, Today, Plan, Review and More (Week, Trend, Week 13, Cycles, Vision, Settings). On a wider screen every page is in the sidebar.

## Daily tracking and reflections

**Today** lists every tactic in this week's plan with a −/+ count for the day and a running “3 / 5 this week” total. Today's calendar sessions are listed below the tactics (beside them on a wide screen). Taps save instantly; if one fails, the count goes back and a Retry appears. Use the day chips to catch up on an earlier day this week. Future days and Week 13 can't be logged.

When you open a week's review, each tactic's done count starts from that week's daily logs, and you can still change it before saving. Once saved, the review is the record that the score uses. A tactic whose logs differ from its saved count shows “N logged on Today”.

The questions in the daily reflection and in the weekly review are set in Settings → **Reflection prompts**. Each one is free text, a 1–5 rating or yes/no. Add, rename, reorder, hide or delete them as you like. Answers keep the wording they were saved under, so editing or deleting a prompt never changes past reviews.

## Calendar

Tactics have no fixed times. As in the book, you **plan each week** around whatever else is on: Week → **Plan week N** lists every tactic active that week, and you give each session a day, start time and minutes. You can plan this week and next (week 1 any time before the cycle starts). **Copy week N's times** fills in last week's times to adjust. Sessions you leave unplaced still count, and you log them on Today as usual; the plan only says *when*. Home and Today remind you while this week isn't planned, and about next week from the day of your weekly review block. Saving your last weekly review takes you straight to planning.

Add **time blocks** on the Plan page for admin, chores, breakout and your weekly review. They repeat every week, belong to the cycle and are copied with "copy previous plan", but they aren't scored. Once a week is planned, it keeps its own copy of the blocks, so a past week shows the plan it had. On a week's plan you can move a block for that week only, or clear its time to skip it, and add one-off **events** (a dinner, a trip) to plan around. Events go in your calendar but aren't scored. Changing a block's weekly times later keeps any one-week moves and skips.

While you enter times, twy warns when one overlaps another session or block, and offers to **Move to** the nearest free time before or after. Free times are looked for between the hours in Settings → **Suggest times between** (default 06:00–22:00). Clashes never stop a save; any left over are listed on the Plan page.

Once a week is over, its plan is kept as it was. Week → **Record what happened** lets you note where each session really went: change a time if it moved, clear it if it didn't happen, or add one you didn't plan. Until you do, the week counts as having gone to plan. The Week page then compares the two: what happened is shown solid, a faint outline marks where moved or missed sessions were planned, and a line sums it up ("1 moved, 1 didn't happen, 1 added") so you can see what got in the way. You can record a week until the cycle is closed, even one you never planned. The weekly **Review** has the same "What happened" section, saved with the review, and points out where fewer sessions were logged on Today than happened in the calendar ("Run: 3 in the calendar, 2 logged on Today. 1 may not have happened."), so you can record the miss or fix the count. This doesn't change your score, which still comes from the counts in Review.

To plan around the rest of your life, paste your other calendars' private ICS links in Settings → **Plan around your other calendars** (iCloud: share a calendar as a Public Calendar; Google: the calendar's *Secret address in iCal format*). Their events show greyed on the planning page, the Week page and Today, repeats included, and timed ones count when twy checks for clashes (all-day ones don't). The home server fetches each link itself, at most every 15 minutes, so it needs internet access; if a link can't be reached, twy says so and uses the last copy. Imported events are read-only and never go into twy's own calendar.

Settings → **Calendar** has a private subscribe link. Tap **Subscribe on this device** on your iPhone, or on a Mac use Calendar → File → New Calendar Subscription. The calendar shows this week's sessions. Next week appears once you've planned it, or from the day of your weekly review block, whichever comes first. Your device fetches it from the home server, so it updates while you're on your home Wi-Fi (Google and Outlook's cloud can't reach your home network). **Reset link** stops old subscriptions from working.

## How scoring works

- A tactic like "Run 3×/week" adds **3 actions** to each week in its range.
- Execution score = actions done ÷ actions planned. Weeks with nothing planned are skipped.
- Each review **snapshots** what was planned, so later tactic edits don't rewrite past weeks. Deleting a tactic does remove it from history.
- Goals can optionally have a lag measure (unit, starting value, target). The direction is inferred, so 82 → 77 kg means lower is better. Goals without a target are marked hit or missed by hand at Week 13.
- Soft warnings appear for more than 3 goals, more than ~8 tactics, more actions in a week than your limit (Settings, default 25), or a goal with no tactics. They warn but never block.

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

### Option C: automatic deploys from GitHub

`.github/workflows/ci-cd.yml` builds the Docker image and runs the tests on every pull request. When a PR is merged to `main`, it also deploys: a GitHub runner joins your tailnet, then runs `scripts/deploy.sh` over SSH. Do Option B once by hand first, so the server already has `~/twy/.env` with a password.

On the server (the same Linux machine as above):

1. Install Tailscale and sign in:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   ```
2. Make sure the SSH server is running (`sudo apt install openssh-server` on Debian or Ubuntu), and that your user is in the `docker` group. The deploy runs `docker compose` over a non-interactive SSH session, where `sudo` can't prompt.
3. If the server is a laptop, stop it suspending when the lid is closed. Set `HandleLidSwitch=ignore` and `HandleLidSwitchExternalPower=ignore` in `/etc/systemd/logind.conf`, then run `sudo systemctl restart systemd-logind`.
4. Create a deploy key and authorise it:
   ```bash
   ssh-keygen -t ed25519 -N '' -C twy-deploy -f ~/twy-deploy
   cat ~/twy-deploy.pub >> ~/.ssh/authorized_keys
   ```

In the [Tailscale admin console](https://login.tailscale.com/admin):

1. In **Access controls**, add `"tagOwners": {"tag:ci": ["autogroup:admin"]}` and save. If the policy isn't the default allow-all, also add a rule that lets `tag:ci` reach the server on `tcp:22`.
2. Under **Settings → Trust credentials**, create an OAuth credential with the **Auth Keys → Write** scope and the `tag:ci` tag. Save the policy from step 1 first, or the tag can't be picked. If the deploy fails at "Join the tailnet" with `Status: 404, Message: "not found"`, this credential is missing the write scope or the tag.

On GitHub, go to **Settings → Environments → New environment**. Name it `production` and limit its deployment branches to `main`. Add these environment secrets:

| Secret | Value |
| --- | --- |
| `TS_OAUTH_CLIENT_ID`, `TS_OAUTH_SECRET` | From the trust credential |
| `DEPLOY_SERVER` | `you@server-name` (its Tailscale MagicDNS name) |
| `DEPLOY_SSH_KEY` | Contents of `~/twy-deploy` on the server (then delete that file) |
| `DEPLOY_SSH_KNOWN_HOSTS` | The server's host keys (see below) |

SSH only trusts a host key filed under the exact name it connects to, so record the keys under every name the server goes by. From a machine on the tailnet:

```bash
{ ssh-keyscan server-name; ssh-keyscan server-name.your-tailnet.ts.net; ssh-keyscan 100.x.y.z; } 2>/dev/null | gh secret set DEPLOY_SSH_KNOWN_HOSTS --env production
```

The tailnet suffix and address are on the Machines page. If the deploy fails with `Host key verification failed`, this secret is missing the name that `DEPLOY_SERVER` uses.

Because the repository is public, anyone can read the Actions logs. Secrets are masked in them, but keep anything else private out of the logs.

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
