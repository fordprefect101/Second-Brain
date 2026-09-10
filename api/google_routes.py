"""Google connection, calendar, and task routes.

The connect flow is unusual for an API: it hands the browser off to Google and
waits for a redirect back. Everything else here is ordinary — get a provider, call
the domain interface, return domain objects.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from api import tokens as token_store
from api.config import REPO_ROOT
from api.oauth import (
    NeedsReconnect,
    OAuthNotConfigured,
    PROVIDER,
    build_consent_url,
    connection_status,
    exchange_code,
    load_client_credentials,
    new_state,
)
from api.providers.google_calendar import GoogleCalendarProvider
from api.providers.google_client import GoogleApiError, PermissionDenied
from api.providers.google_tasks import GoogleTasksProvider
from api.services import CalendarService, TaskService

router = APIRouter(tags=["google"])

# The CSRF state, held in memory between the redirect out and the redirect back.
# In-memory is adequate because the window is seconds and this is a single-user
# local app; a multi-user server would need it in a session.
_pending_state: dict[str, str] = {}


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class CalendarEventOut(CamelModel):
    id: str
    title: str
    start: datetime
    end: datetime
    all_day: bool
    location: str | None = None
    source: str


class TaskOut(CamelModel):
    id: str
    title: str
    completed: bool
    due: datetime | None = None
    notes: str | None = None
    parent_id: str | None = None
    source: str


def _handle(exc: Exception) -> HTTPException:
    """Map provider failures onto meaningful HTTP status codes.

    The distinction that matters: 401 tells the UI to show "reconnect", which is a
    NORMAL state here — unverified apps get 7-day refresh tokens, so it happens
    roughly weekly. It must not look like a crash.
    """
    if isinstance(exc, NeedsReconnect):
        return HTTPException(status_code=401, detail=str(exc))
    if isinstance(exc, PermissionDenied):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, OAuthNotConfigured):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


@router.get("/google/status")
def google_status() -> dict:
    """Never raises. An unconnected integration is a normal state, not an error."""
    return connection_status(REPO_ROOT)


@router.get("/google/connect")
def google_connect() -> RedirectResponse:
    """Send the browser to Google's consent screen."""
    try:
        credentials = load_client_credentials(REPO_ROOT)
    except OAuthNotConfigured as exc:
        raise _handle(exc) from exc

    state = new_state()
    _pending_state["state"] = state
    return RedirectResponse(build_consent_url(credentials, state))


@router.get("/google/callback", response_class=HTMLResponse)
def google_callback(
    code: str | None = None, state: str | None = None, error: str | None = None
) -> HTMLResponse:
    """Where Google sends the browser back.

    Returns HTML rather than JSON: a human is looking at this page, not a script.
    """
    if error:
        hint = (
            "Add your email under Audience > Test users in Google Cloud Console."
            if error == "access_denied"
            else ""
        )
        return _page(f"Authorisation failed: {error}", hint, ok=False)

    expected = _pending_state.pop("state", None)
    if not state or state != expected:
        # The response did not originate from our /connect request.
        return _page("State mismatch — request did not originate here.", "", ok=False)

    if not code:
        return _page("No authorisation code returned.", "", ok=False)

    try:
        exchange_code(load_client_credentials(REPO_ROOT), code)
    except Exception as exc:
        return _page("Could not exchange the code for tokens.", str(exc)[:200], ok=False)

    return _page("Google connected.", "You can close this tab.", ok=True)


@router.post("/google/disconnect")
def google_disconnect() -> dict:
    """Forget our copy of the tokens.

    Deliberately honest in the response: this does NOT revoke Google's grant.
    Users reasonably assume "disconnect" cuts access everywhere, and it does not.
    """
    token_store.delete(PROVIDER)
    return {
        "disconnected": True,
        "note": (
            "Local tokens deleted. Google still lists this app until you revoke it "
            "at myaccount.google.com/permissions"
        ),
    }


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


def get_calendar_service() -> CalendarService:
    """The one place a concrete calendar provider is named."""
    return GoogleCalendarProvider(REPO_ROOT)


@router.get("/events", response_model=list[CalendarEventOut])
def list_events(
    days: Annotated[int, Query(ge=1, le=30)] = 1,
) -> list[CalendarEventOut]:
    """Events from now to `days` ahead. Defaults to today."""
    service = get_calendar_service()
    now = datetime.now(timezone.utc)

    try:
        events = service.list_events(now, now + timedelta(days=days))
    except Exception as exc:
        raise _handle(exc) from exc

    return [
        CalendarEventOut(
            id=event.provider_id,
            title=event.title,
            start=event.start,
            end=event.end,
            all_day=event.all_day,
            location=event.location,
            source=service.source_id,
        )
        for event in events
    ]


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


def get_task_service() -> TaskService:
    """The one place a concrete task provider is named."""
    return GoogleTasksProvider(REPO_ROOT)


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(include_completed: bool = False) -> list[TaskOut]:
    service = get_task_service()
    try:
        tasks = service.list_tasks(include_completed=include_completed)
    except Exception as exc:
        raise _handle(exc) from exc

    return [_task_out(task, service.source_id) for task in tasks]


@router.post("/tasks/{list_id}/{task_id}/complete", response_model=TaskOut)
def complete_task(list_id: str, task_id: str) -> TaskOut:
    """Path is split because a Google task id is only addressable with its list."""
    service = get_task_service()
    try:
        task = service.complete_task(f"{list_id}/{task_id}")
    except Exception as exc:
        raise _handle(exc) from exc

    return _task_out(task, service.source_id)


def _task_out(task, source: str) -> TaskOut:
    return TaskOut(
        id=task.provider_id,
        title=task.title,
        completed=task.completed,
        due=task.due,
        notes=task.notes,
        parent_id=task.parent_id,
        source=source,
    )


def _page(heading: str, detail: str, *, ok: bool) -> HTMLResponse:
    colour = "#2f6f4e" if ok else "#b3261e"
    return HTMLResponse(
        f"""<html><body style="font:16px/1.6 system-ui;padding:3rem;max-width:34rem">
        <h2 style="color:{colour};margin:0 0 .5rem">{heading}</h2>
        <p style="color:#666">{detail}</p></body></html>""",
        status_code=200 if ok else 400,
    )
