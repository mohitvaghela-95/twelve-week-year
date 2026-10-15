"""Other calendars (ICS links) shown while you plan, so a week fits around what's already on.

The home server fetches each link when a page needs it and keeps it for 15 minutes; if a fetch
fails, the last copy is used and the page says so. Times are converted to the server's local
time zone (TZ), like the feed twy publishes. Imported events are read-only: they're never stored
and never go into twy's own calendar feed.
"""

import http.client
import threading
import time
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

import icalendar
import recurring_ical_events

CACHE_SECONDS = 15 * 60
TIMEOUT_SECONDS = 5
MAX_BYTES = 5 * 1024 * 1024
MAX_LINKS = 5

# Network, HTTP, size and parse failures: a calendar that can't load mustn't break the page.
LOAD_ERRORS = (OSError, http.client.HTTPException, ValueError)
# A malformed event or repeating series.
READ_ERRORS = (ValueError, TypeError, KeyError, AttributeError)  # InvalidCalendar is a ValueError


@dataclass(frozen=True)
class ImportedEvent:
    title: str
    start: datetime  # server-local, naive; for all-day events, midnight of the first day
    end: datetime  # exclusive
    all_day: bool = False


def normalise_url(text: str) -> str | None:
    """An http(s) calendar link, with webcal:// read as https://, or None if it isn't one."""
    url = text.strip()
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://") :]
    parts = urlsplit(url)
    return url if parts.scheme in ("http", "https") and parts.netloc else None


def host(url: str) -> str:
    """Just the server's name, for messages: links often carry a private token in the path."""
    return urlsplit(url).hostname or "a calendar"


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "twy calendar import"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("calendar is too large")
    return body


_cache: dict[str, tuple[float, icalendar.Calendar | None]] = {}
_lock = threading.Lock()


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def load(url: str, now: float, fetcher: Callable[[str], bytes] | None = None) -> tuple[icalendar.Calendar | None, str]:
    """The parsed calendar (fresh, cached or stale) and an error message ("" if all went well)."""
    with _lock:
        cached = _cache.get(url)
    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1], ""
    try:
        calendar = icalendar.Calendar.from_ical((fetcher or fetch)(url))
    except LOAD_ERRORS:
        stale = cached[1] if cached else None
        note = " Showing the last copy." if stale is not None else ""
        return stale, f"Couldn't load your calendar from {host(url)}.{note}"
    with _lock:
        _cache[url] = (now, calendar)
    return calendar, ""


def local(value: datetime) -> datetime:
    """Server-local wall-clock time. Floating times (no zone) are already local."""
    return value.astimezone().replace(tzinfo=None) if value.tzinfo else value


def events_between(calendar: icalendar.Calendar, first: date, last: date) -> list[ImportedEvent]:
    """Events overlapping `first`..`last` (inclusive), with repeats expanded. Cancelled ones are left out."""
    # A day either side, as the query compares in the events' own time zones; clipped after converting.
    found = recurring_ical_events.of(calendar, skip_bad_series=True).between(
        first - timedelta(days=1), last + timedelta(days=2)
    )
    lo, hi = (
        datetime.combine(first, datetime.min.time()),
        datetime.combine(last + timedelta(days=1), datetime.min.time()),
    )
    events: list[ImportedEvent] = []
    for component in found:
        if str(component.get("STATUS", "")).upper() == "CANCELLED":
            continue
        title = str(component.get("SUMMARY", "")).strip() or "Busy"
        start = component.decoded("DTSTART")
        end = component.decoded("DTEND") if "DTEND" in component else start
        if not isinstance(start, datetime):  # an all-day event: DTEND is the day after the last
            begin = datetime.combine(start, datetime.min.time())
            finish = datetime.combine(end if end > start else start + timedelta(days=1), datetime.min.time())
            event = ImportedEvent(title, begin, finish, all_day=True)
        else:
            event = ImportedEvent(title, local(start), local(end) if isinstance(end, datetime) else local(start))
        if event.start < hi and (event.end > lo or (event.end == event.start and event.start >= lo)):
            events.append(event)
    return sorted(events, key=lambda e: (e.start, e.title))


def busy(
    urls: Iterable[str], first: date, last: date, now: float | None = None
) -> tuple[list[ImportedEvent], list[str]]:
    """Every linked calendar's events in `first`..`last`, plus a message for each that couldn't load."""
    now = time.monotonic() if now is None else now
    events: list[ImportedEvent] = []
    errors: list[str] = []
    for url in urls:
        calendar, error = load(url, now)
        if error:
            errors.append(error)
        if calendar is not None:
            try:
                events += events_between(calendar, first, last)
            except READ_ERRORS:
                errors.append(f"Couldn't read some events from {host(url)}.")
    return sorted(events, key=lambda e: (e.start, e.title)), errors


def day_spans(event: ImportedEvent, first: date, last: date) -> list[tuple[date, int, int]]:
    """A timed event as (day, start minute, end minute) pieces within `first`..`last`, split at midnight."""
    spans = []
    day = max(event.start.date(), first)
    while day <= min(event.end.date(), last):
        midnight = datetime.combine(day, datetime.min.time())
        start = max(int((event.start - midnight).total_seconds() // 60), 0)
        end = min(int((event.end - midnight).total_seconds() // 60), 24 * 60)
        if end > start:
            spans.append((day, start, end))
        day += timedelta(days=1)
    return spans


def all_day_dates(event: ImportedEvent, first: date, last: date) -> list[date]:
    days, day = [], max(event.start.date(), first)
    while day < event.end.date() and day <= last:
        days.append(day)
        day += timedelta(days=1)
    return days
