"""A minimal iCalendar (RFC 5545) writer for the subscribe-able calendar feed.

Times are written in UTC, converted from local wall-clock time with the server's
time zone (TZ), so DST is handled per date and no VTIMEZONE block is needed.
"""

from collections.abc import Iterable
from datetime import datetime, timezone, tzinfo

from twy.domain.dates import EXECUTION_WEEKS
from twy.domain.schedule import Session
from twy.models import BLOCK_KINDS

CALENDAR_NAME = "12 Week Year"


def escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\n").replace("\n", "\\n")


def fold(line: str) -> str:
    """Split lines longer than 75 octets; continuation lines start with a space."""
    out: list[str] = []
    current, size = "", 0
    for ch in line:
        width = len(ch.encode())
        if size + width > (75 if not out else 74):
            out.append(current)
            current, size = "", 0
        current += ch
        size += width
    out.append(current)
    return "\r\n ".join(out)


def utc_stamp(local: datetime, tz: tzinfo | None) -> str:
    aware = local.replace(tzinfo=tz) if tz else local.astimezone()  # None: the server's local zone
    return aware.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def describe(s: Session) -> str:
    if s.owner == "tactic":
        return f"Goal: {s.context}\nWeek {s.week_no} of {EXECUTION_WEEKS}"
    return BLOCK_KINDS.get(s.kind, s.kind)


def render_calendar(
    sessions: Iterable[Session], alert_minutes: int, now: datetime, tz: tzinfo | None = None
) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//twy//12 Week Year//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{CALENDAR_NAME}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for s in sessions:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{s.uid}",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{utc_stamp(s.start, tz)}",
            f"DTEND:{utc_stamp(s.end, tz)}",
            f"SUMMARY:{escape(s.title)}",
            f"DESCRIPTION:{escape(describe(s))}",
            f"CATEGORIES:{escape('Tactic' if s.owner == 'tactic' else BLOCK_KINDS.get(s.kind, s.kind))}",
            "TRANSP:OPAQUE",
        ]
        if alert_minutes > 0:
            lines += [
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                f"DESCRIPTION:{escape(s.title)}",
                f"TRIGGER:-PT{alert_minutes}M",
                "END:VALARM",
            ]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(line) for line in lines) + "\r\n"
