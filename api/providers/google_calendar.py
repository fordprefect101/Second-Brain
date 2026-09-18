"""Google Calendar provider. Read-only.

Satisfies CalendarService structurally — no inheritance, same as
ObsidianVaultProvider satisfying NoteService.

Everything Google-specific stops here: the URL, the pagination, the two different
date formats, the timezone handling. Layers above see CalendarEvent objects and
cannot tell where they came from.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from api.providers.google_client import GoogleClient
from api.services import CalendarEvent

CALENDAR_LIST_URL = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"

# How many calendars to query at once. Cap rather than one thread per calendar:
# a subscribed-feed collector can have dozens, and firing all of them at Google
# simultaneously invites the 429 that GoogleClient then has to back off from.
MAX_CONCURRENT_CALENDARS = 8

# Which calendars exist changes when the user subscribes to something — monthly
# at most. Fetching it on every request cost 0.75s of every page load.
#
# Module-level rather than per-instance because a provider is constructed fresh
# for each request, so an instance cache would never be read twice.
#
# Two concurrent requests can both miss and both fetch. That is a wasted call,
# not a correctness problem, so this stays lock-free.
_CALENDAR_CACHE: tuple[float, list[dict]] | None = None
CALENDAR_CACHE_TTL_SECONDS = 600


def clear_calendar_cache() -> None:
    """Drop the cached calendar list. For tests, and for after a reconnect."""
    global _CALENDAR_CACHE
    _CALENDAR_CACHE = None


class GoogleCalendarProvider:
    source_id = "google_calendar"

    def __init__(self, repo_root: Path, calendar_id: str | None = None):
        self.client = GoogleClient(repo_root)
        # None means "every calendar the user has visible". Passing an explicit id
        # restricts to one, which is useful in tests.
        self.calendar_id = calendar_id

    def list_calendars(self) -> list[dict]:
        """Calendars the user has switched ON in Google Calendar.

        A Google account usually has far more calendars than the primary one —
        subscribed sports schedules, holidays, shared family calendars, classroom
        feeds. Querying only 'primary' silently returns nothing for people whose
        real content lives in subscriptions.

        `selected` is Google's own record of which calendars the user ticked
        visible in their UI. Honouring it means the Personal OS shows what they
        already chose to see, rather than inventing a different filter and then
        needing settings to control it.

        Cached for CALENDAR_CACHE_TTL_SECONDS — see the note above _CALENDAR_CACHE.
        """
        global _CALENDAR_CACHE

        if _CALENDAR_CACHE is not None:
            cached_at, calendars = _CALENDAR_CACHE
            if time.monotonic() - cached_at < CALENDAR_CACHE_TTL_SECONDS:
                return calendars

        calendars = [
            calendar
            for calendar in self.client.paginate(CALENDAR_LIST_URL, limit=100)
            if calendar.get("selected", False)
        ]
        _CALENDAR_CACHE = (time.monotonic(), calendars)
        return calendars

    def _events_for(
        self, calendar: dict, start: datetime, end: datetime
    ) -> list[CalendarEvent]:
        """One calendar's events. Runs in a worker thread — see list_events."""
        items = self.client.paginate(
            EVENTS_URL.format(calendar_id=quote(calendar["id"], safe="")),
            {
                "timeMin": _rfc3339(start),
                "timeMax": _rfc3339(end),
                # Expand recurring events into individual occurrences. Without
                # this a weekly standup returns once, as a rule, and never
                # appears on the right day.
                "singleEvents": "true",
                # Only permitted when singleEvents is true — Google rejects it
                # otherwise, which is a confusing 400 the first time.
                "orderBy": "startTime",
                "maxResults": 250,
            },
            limit=500,
        )

        return [
            _to_event(item, calendar.get("summary"))
            for item in items
            # Cancelled occurrences of a recurring event still appear.
            if item.get("status") != "cancelled"
        ]

    def list_events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        """Events across every visible calendar, earliest first.

        Still one request per calendar, but issued concurrently rather than in
        sequence. Measured against a real account: seven calendars took 5.7s
        serially at ~0.8s each, because the wait is entirely network latency and
        nothing overlapped. Concurrently the whole set costs roughly as much as
        the slowest single calendar.

        Threads rather than asyncio: GoogleClient is synchronous and carries the
        refresh-on-401 and backoff logic, and porting that to async would be a far
        larger change than the one this speedup justifies. The work is I/O-bound,
        so the GIL is released during the request and threads are enough.

        Exception semantics are unchanged — if any calendar fails the whole call
        fails, exactly as the sequential version did.
        """
        if self.calendar_id is not None:
            targets = [{"id": self.calendar_id, "summary": None}]
        else:
            targets = [
                {"id": c["id"], "summary": c.get("summary")}
                for c in self.list_calendars()
            ]

        if not targets:
            return []

        with ThreadPoolExecutor(
            max_workers=min(len(targets), MAX_CONCURRENT_CALENDARS)
        ) as pool:
            # map, not submit+as_completed: results are re-sorted below anyway, and
            # map re-raises the first exception, preserving the old behaviour.
            per_calendar = pool.map(
                lambda calendar: self._events_for(calendar, start, end), targets
            )
            events = [event for group in per_calendar for event in group]

        # Each calendar came back sorted, but merging several breaks that — so the
        # combined list has to be re-sorted. All-day events parse as naive
        # datetimes and timed ones as aware, which cannot be compared directly,
        # hence the key normalises before sorting.
        events.sort(key=_sort_key)
        return events


def _rfc3339(value: datetime) -> str:
    """Google requires RFC3339 with an explicit offset.

    A naive datetime would be interpreted in an unspecified timezone, so it is
    assumed to be UTC rather than silently sent as-is.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _sort_key(event: CalendarEvent) -> datetime:
    """Comparable key across all-day and timed events.

    All-day events are deliberately naive (no timezone — an all-day event has no
    meaningful one), timed events are aware. Python refuses to compare the two, so
    naive values are treated as UTC purely for ordering.
    """
    start = event.start
    return start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start


def _to_event(item: dict, calendar_name: str | None = None) -> CalendarEvent:
    """Map Google's shape onto the domain type.

    The awkward part: Google returns two different structures. A timed event has
    `dateTime` (a full timestamp); an all-day event has `date` (just a day, no
    time, no zone). Treating them identically shifts all-day events by the UTC
    offset — which is how "all-day Friday" ends up displaying on Thursday.
    """
    start_raw = item.get("start", {})
    end_raw = item.get("end", {})
    all_day = "date" in start_raw

    return CalendarEvent(
        provider_id=item["id"],
        title=item.get("summary") or "(no title)",
        start=_parse(start_raw, all_day),
        end=_parse(end_raw, all_day),
        all_day=all_day,
        location=item.get("location"),
        description=item.get("description"),
        # Which calendar this came from. With eight of them, "Formula 1" versus
        # "Family" is most of what makes an event legible at a glance.
        calendar_name=calendar_name,
    )


def _parse(part: dict, all_day: bool) -> datetime:
    if all_day:
        # Midnight local, deliberately naive: an all-day event has no timezone, and
        # attaching one would make it drift across date boundaries.
        return datetime.fromisoformat(part["date"])
    return datetime.fromisoformat(part["dateTime"])
