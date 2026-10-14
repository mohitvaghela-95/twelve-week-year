from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from twy.domain.schedule import Session
from twy.web.ics import fold, render_calendar

LONDON = ZoneInfo("Europe/London")
NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


def session(day, title="Study", start=420, minutes=60):
    return Session(
        day=day,
        start_min=start,
        duration_min=minutes,
        title=title,
        kind="tactic",
        owner="tactic",
        owner_id=1,
        slot_id=7,
        cycle_id=1,
        week_no=1,
        context="AI engineer offer",
    )


def test_calendar_structure_and_alert():
    text = render_calendar([session(date(2026, 10, 5))], 10, NOW, LONDON)
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")
    lines = text.split("\r\n")
    assert lines[0] == "BEGIN:VCALENDAR" and lines[-2] == "END:VCALENDAR"
    assert "UID:tactic-7-20261005@twy" in lines
    assert "DTSTART:20261005T060000Z" in lines  # 07:00 BST
    assert "DTEND:20261005T070000Z" in lines
    assert "DESCRIPTION:Goal: AI engineer offer\\nWeek 1 of 12" in lines
    assert "TRIGGER:-PT10M" in lines
    assert "BEGIN:VALARM" not in render_calendar([session(date(2026, 10, 5))], 0, NOW, LONDON)


def test_local_time_survives_the_clock_change():
    # UK clocks go back on Sunday 25 October 2026.
    text = render_calendar([session(date(2026, 10, 24)), session(date(2026, 10, 26))], 0, NOW, LONDON)
    assert "DTSTART:20261024T060000Z" in text
    assert "DTSTART:20261026T070000Z" in text


def test_escaping_and_folding():
    text = render_calendar([session(date(2026, 10, 5), title="Read, then notes; ideas\\links")], 0, NOW, LONDON)
    assert r"SUMMARY:Read\, then notes\; ideas\\links" in text
    line = "DESCRIPTION:" + "é" * 80
    parts = fold(line).split("\r\n")
    assert len(parts) > 1
    assert all(len(p.encode()) <= 75 for p in parts)
    assert all(p.startswith(" ") for p in parts[1:])
    assert parts[0] + "".join(p[1:] for p in parts[1:]) == line
