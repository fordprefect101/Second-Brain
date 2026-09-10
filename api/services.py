"""Domain interfaces.

The contract between routes and providers. Routes depend on these Protocols;
they never import a provider directly. Swapping Obsidian for Notion should touch
one wiring line and nothing else.

Python `Protocol` gives structural typing — a class satisfies NoteService by
having the right methods, with no base class to inherit and no registration step.
It is the direct analogue of the TypeScript interfaces in Plan.md §8.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass
class ProviderNote:
    """A note as the provider sees it, before internal identity is assigned.

    `provider_id` is whatever the source uses natively — a vault-relative path for
    Obsidian, a page id for Notion. It stops here: everything above this layer uses
    the internal uuid from entity_map instead, so no route or component can come to
    depend on notes being files.
    """

    provider_id: str
    title: str
    excerpt: str
    modified_at: datetime
    tags: list[str] = field(default_factory=list)
    body: str | None = None  # populated only by get_note


@runtime_checkable
class NoteService(Protocol):
    """Read and write notes, wherever they live.

    The write methods are declared even though Obsidian raises NotImplementedError
    for them in V1 (ADR-005). Declaring the full interface keeps the contract honest
    — the gap is visible here rather than discovered later — and lets the UI be
    built against the finished shape.
    """

    source_id: str

    def list_notes(self, limit: int = 200) -> list[ProviderNote]: ...

    def get_note(self, provider_id: str) -> ProviderNote | None: ...

    # --- Phase 2b, gated on an undo mechanism (ADR-005) ---

    def create_note(self, title: str, body: str) -> ProviderNote: ...

    def update_note(self, provider_id: str, body: str) -> ProviderNote: ...


class NoteWritesNotSupported(RuntimeError):
    """Raised by providers that are deliberately read-only for now."""


# ---------------------------------------------------------------------------
# Calendar and Tasks (Phase 3)
# ---------------------------------------------------------------------------
#
# These interfaces are the real test of the layering. NoteService had one
# implementation, which proves nothing — an interface with a single implementation
# is just a file. Calendar and Tasks are structurally different from Obsidian:
# remote rather than local, paginated, rate-limited, with credentials that expire.
# If the abstraction has Obsidian-shaped assumptions in it, this is where it snaps.
#
# Note what is deliberately absent: no Google types, no field named after a Google
# concept, nothing about pagination or tokens. Those live in the provider.


@dataclass
class CalendarEvent:
    provider_id: str
    title: str
    start: datetime
    end: datetime
    # True for all-day events, where the API returns a date rather than a datetime.
    # The distinction is real and the UI must render them differently, so it cannot
    # be flattened away here.
    all_day: bool = False
    location: str | None = None
    description: str | None = None
    calendar_name: str | None = None


@dataclass
class Task:
    provider_id: str
    title: str
    completed: bool = False
    due: datetime | None = None
    notes: str | None = None
    # Google Tasks supports exactly one level of nesting (docs/integrations).
    # Modelled as a parent id rather than a nested structure, so a provider with
    # deeper nesting would not need this interface changed.
    parent_id: str | None = None


@runtime_checkable
class CalendarService(Protocol):
    source_id: str

    def list_events(self, start: datetime, end: datetime) -> list[CalendarEvent]: ...


@runtime_checkable
class TaskService(Protocol):
    source_id: str

    def list_tasks(self, include_completed: bool = False) -> list[Task]: ...

    def complete_task(self, provider_id: str) -> Task: ...
