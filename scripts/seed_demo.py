"""Create a demo database with a cycle that is 7 weeks in and 6 weeks reviewed.

    TWY_DB=/tmp/twy-demo.db uv run python scripts/seed_demo.py
    TWY_DB=/tmp/twy-demo.db uv run twy
"""

import random
from datetime import date, timedelta

from twy.db import connect, default_db_path
from twy.domain.dates import align_to_week_start
from twy.models import Goal, Tactic, WeekEntry, WeekReview
from twy.repo import Repo


def seed(repo: Repo, today: date) -> None:
    rng = random.Random(12)
    repo.set_setting("week_start", "0")
    repo.set_vision("long_term", "Healthy, financially independent, building useful software with people I like.")
    repo.set_vision("three_year", "Run a half marathon, ship two side projects, six months of savings.")

    start = align_to_week_start(today - timedelta(days=7 * 7), 0)
    cycle = repo.create_cycle("Autumn sprint", start, 0)
    repo.set_cycle_status(cycle.id, "active")

    weight = repo.save_goal(Goal(None, cycle.id, "Lose 5 kg", "Feel strong for the half marathon", "kg", 82, 77))
    ship = repo.save_goal(Goal(None, cycle.id, "Ship twy v1", "First side project of the three-year vision"))
    tactics = [
        repo.save_tactic(Tactic(None, weight.id, "Run 30 min", 3)),
        repo.save_tactic(Tactic(None, weight.id, "Log food daily", 1)),
        repo.save_tactic(Tactic(None, weight.id, "Long run 60 min", 1, 5, 12)),
        repo.save_tactic(Tactic(None, ship.id, "2-hour build session", 2)),
        repo.save_tactic(Tactic(None, ship.id, "Write README", 1, 10, 10)),
    ]

    notes = ["Travel for work", "", "Sick two days", "Late meetings", "", "Kids' school holiday"]
    value = 82.0
    for week in range(1, 7):
        entries = []
        for t in tactics:
            if t.active_in(week):
                done = max(0, t.times_per_week - rng.choice([0, 0, 0, 1]))
                entries.append(WeekEntry(cycle.id, week, t.id, t.times_per_week, done))
        value -= rng.uniform(0.2, 0.9)
        repo.save_week_review(
            WeekReview(cycle.id, week, notes[week - 1], "Book runs in calendar" if notes[week - 1] else "", entries=entries),
            {weight.id: round(value, 1)},
        )


if __name__ == "__main__":
    path = default_db_path()
    if path.exists():
        raise SystemExit(f"{path} already exists. Point TWY_DB at a new file.")
    seed(Repo(connect(path)), date.today())
    print(f"Seeded {path}")
