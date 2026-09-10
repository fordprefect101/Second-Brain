"""Google Calendar provider. Read-only.

Satisfies CalendarService structurally — no inheritance, same as
ObsidianVaultProvider satisfying NoteService.

Everything Google-specific stops here: the URL, the pagination, the two different
date formats, the timezone handling. Layers above see CalendarEvent objects and
cannot tell where they came from.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from api.providers.google_client import GoogleClient
from api.services import CalendarEvent

EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"


class GoogleCalendarProvider:
    source_id = "google_calendar"

    def __init__(self, repo_root: Path, calendar_id: str = "primary"):
        self.client = GoogleClient(repo_root)
        self.calendar_id = calendar_id

    def list_events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        """Events in a window, earliest first."""
        items = self.client.paginate(
            EVENTS_URL.format(calendar_id=self.calendar_id),
            {
                "timeMin": _rfc3339(start),
                "timeMax": _rfc3339(end),
                # Expand recurring events into individual occurrences. Without this
                # a weekly standup returns once, as a rule, and never appears on
                # the right day.
                "singleEvents": "true",
                # Only permitted when singleEvents is true — Google rejects it
                # otherwise, which is a confusing 400 the first time.
                "orderBy": "startTime",
                "maxResults": 250,
            },
            limit=500,
        )

        events = []
        for item in items:
            # Cancelled occurrences of a recurring event still appear in the feed.
            if item.get("status") == "cancelled":
                continue
            events.append(_to_event(item))

        return events


def _rfc3339(value: datetime) -> str:
    """Google requires RFC3339 with an explicit offset.

    A naive datetime would be interpreted in an unspecified timezone, so it is
    assumed to be UTC rather than silently sent as-is.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _to_event(item: dict) -> CalendarEvent:
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
    )


def _parse(part: dict, all_day: bool) -> datetime:
    if all_day:
        # Midnight local, deliberately naive: an all-day event has no timezone, and
        # attaching one would make it drift across date boundaries.
        return datetime.fromisoformat(part["date"])
    return datetime.fromisoformat(part["dateTime"])
