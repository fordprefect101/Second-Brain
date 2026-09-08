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
