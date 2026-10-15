import time
from datetime import date, datetime

import pytest

from twy import calendars
from twy.calendars import (
    CACHE_SECONDS,
    ImportedEvent,
    all_day_dates,
    busy,
    day_spans,
    events_between,
    load,
    normalise_url,
)

MON, SUN = date(2026, 10, 12), date(2026, 10, 18)


def ics(*events: str) -> bytes:
    body = "".join(f"BEGIN:VEVENT\r\n{e.strip()}\r\nEND:VEVENT\r\n" for e in events)
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:test\r\n{body}END:VCALENDAR\r\n".encode()


def event(uid: str, summary: str, *lines: str) -> str:
    return "\r\n".join([f"UID:{uid}", f"SUMMARY:{summary}", *lines])


@pytest.fixture
def london(monkeypatch):
    """The server's local time zone, as TZ sets it in the container."""
    monkeypatch.setenv("TZ", "Europe/London")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def parse(data: bytes):
    calendar, error = load("https://example.test/a.ics", now=0, fetcher=lambda _url: data)
    assert error == ""
    return calendar


def test_normalise_url():
    assert (
        normalise_url(" webcal://p01-caldav.icloud.com/published/2/abc ")
        == "https://p01-caldav.icloud.com/published/2/abc"
    )
    assert normalise_url("https://calendar.google.com/x/basic.ics") == "https://calendar.google.com/x/basic.ics"
    assert normalise_url("file:///etc/passwd") is None
    assert normalise_url("calendar.google.com/x.ics") is None
    assert normalise_url("https://") is None


def test_repeats_are_expanded_and_times_made_local(london):
    data = ics(
        event(
            "standup",
            "Standup",
            "DTSTART;TZID=America/New_York:20261005T090000",
            "DTEND;TZID=America/New_York:20261005T091500",
            "RRULE:FREQ=WEEKLY;BYDAY=MO,WE",
        ),
        event("cancelled", "Cancelled", "DTSTART:20261013T100000Z", "DTEND:20261013T110000Z", "STATUS:CANCELLED"),
        event("floating", "Floating", "DTSTART:20261013T200000", "DURATION:PT90M"),
        event("before", "Last week", "DTSTART:20261009T100000Z", "DTEND:20261009T110000Z"),
    )
    found = events_between(parse(data), MON, SUN)
    assert [(e.title, e.start, e.end, e.all_day) for e in found] == [
        # 09:00 in New York is 14:00 in London (both on summer time).
        ("Standup", datetime(2026, 10, 12, 14, 0), datetime(2026, 10, 12, 14, 15), False),
        ("Floating", datetime(2026, 10, 13, 20, 0), datetime(2026, 10, 13, 21, 30), False),
        ("Standup", datetime(2026, 10, 14, 14, 0), datetime(2026, 10, 14, 14, 15), False),
    ]


def test_all_day_events_and_untitled_events(london):
    data = ics(
        event("trip", "Half term", "DTSTART;VALUE=DATE:20261016", "DTEND;VALUE=DATE:20261020"),
        event("busy", "", "DTSTART;VALUE=DATE:20261012"),
    )
    trip, untitled = sorted(events_between(parse(data), MON, SUN), key=lambda e: e.title, reverse=True)
    assert trip.all_day and all_day_dates(trip, MON, SUN) == [date(2026, 10, 16), date(2026, 10, 17), SUN]
    assert (untitled.title, all_day_dates(untitled, MON, SUN)) == ("Busy", [MON])


def test_overnight_events_split_at_midnight_and_clip_to_the_week():
    flight = ImportedEvent("Flight", datetime(2026, 10, 13, 22, 0), datetime(2026, 10, 14, 6, 30))
    assert day_spans(flight, MON, SUN) == [(date(2026, 10, 13), 1320, 1440), (date(2026, 10, 14), 0, 390)]
    late = ImportedEvent("Party", datetime(2026, 10, 18, 23, 0), datetime(2026, 10, 19, 2, 0))
    assert day_spans(late, MON, SUN) == [(SUN, 1380, 1440)]
    assert day_spans(ImportedEvent("Ping", datetime(2026, 10, 13, 9), datetime(2026, 10, 13, 9)), MON, SUN) == []


def test_cache_and_stale_copy_on_error():
    calendars.clear_cache()
    url = "https://example.test/cal.ics"
    calls = []

    def ok(fetched_url):
        calls.append(fetched_url)
        return ics(event("a", "Gym", "DTSTART:20261013T070000", "DURATION:PT1H"))

    def down(_url):
        raise OSError("connection refused")

    assert load(url, 100, ok)[1] == "" and load(url, 100 + CACHE_SECONDS - 1, ok)[1] == ""
    assert len(calls) == 1  # the second read came from the cache
    stale, error = load(url, 100 + CACHE_SECONDS, down)
    assert stale is not None and error == "Couldn't load your calendar from example.test. Showing the last copy."
    calendars.clear_cache()
    assert load(url, 0, down) == (None, "Couldn't load your calendar from example.test.")
    assert load(url, 0, lambda _url: b"not a calendar")[1].startswith("Couldn't load")


def test_busy_merges_calendars_and_reports_failures(monkeypatch):
    calendars.clear_cache()
    feeds = {
        "https://a.test/1.ics": ics(event("a", "Dentist", "DTSTART:20261014T090000", "DURATION:PT30M")),
        "https://b.test/private-token.ics": None,
    }

    def fake_fetch(url):
        if feeds[url] is None:
            raise OSError("timed out")
        return feeds[url]

    monkeypatch.setattr(calendars, "fetch", fake_fetch)
    events, errors = busy(feeds, MON, SUN, now=0)
    assert [e.title for e in events] == ["Dentist"]
    assert errors == ["Couldn't load your calendar from b.test."]  # never the link's secret path
