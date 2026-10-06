"""Which [[links]] a note may have, so the note graph stays a tree.

    Personal OS ── Projects index ── project page ── architecture page ── ADRs

Links are resolved exactly as the graph draws them (api/graph.py), so a rule is
broken only when a line would actually appear. What a note is, is read from where
it sits and what it is called, the same things a person looks at:

  an ADR                 filename starts ADR-001, ADR-002, …
  an architecture page   filename contains "architecture overview"
  a project page         a note that "Projects index" links to
  the same project       the same folder

The rules:

  1. An ADR is linked only from its folder's architecture page and from ADRs
     beside it, and links only to those.
  2. A project page does not link to another project page.
  3. Any other note links to at most one project page: the one it belongs to.
  4. A few named notes may link only to the notes listed for them.

Pure functions over the notes: no database, no files.
"""

from __future__ import annotations

import re
from collections import defaultdict

from api.graph import build_graph
from api.services import ProviderNote

INDEX = "projects index"

# Project pages that may link to each other.
ALLOWED_PAIRS = [{"audiotour project", "voice generation"}]

# Notes whose links are fixed: name -> the only notes it may link to.
ONLY_LINKS = {"master resume": {"personal os", INDEX}}

_ADR_RE = re.compile(r"adr-\d+")


def _name(provider_id: str) -> str:
    return provider_id.rsplit("/", 1)[-1].removesuffix(".md").lower()


def _folder(provider_id: str) -> str:
    return provider_id.rsplit("/", 1)[0].lower() if "/" in provider_id else ""


def _is_adr(provider_id: str) -> bool:
    return _ADR_RE.match(_name(provider_id)) is not None


def _is_architecture(provider_id: str) -> bool:
    return "architecture overview" in _name(provider_id)


def _beside_adr(provider_id: str, adr: str) -> bool:
    """A note an ADR may share a link with: a sibling ADR or its architecture page."""
    return _folder(provider_id) == _folder(adr) and (
        _is_adr(provider_id) or _is_architecture(provider_id)
    )


def problems(notes: list[ProviderNote], only: str | None = None) -> list[str]:
    """Every broken rule, in plain words. `only` limits it to links FROM one note.

    With `only` unset the whole vault is checked, which adds one thing no single
    note can show: an ADR that no architecture page links to.
    """
    edges = build_graph(notes).edges
    title = {n.provider_id: n.title for n in notes}
    projects = {b for a, b in edges if _name(a) == INDEX}

    found: list[str] = []
    project_links: dict[str, list[str]] = defaultdict(list)
    for source, target in edges:
        if only is not None and source != only:
            continue
        here, there = f"'{title[source]}'", f"'{title[target]}'"

        allowed = ONLY_LINKS.get(_name(source))
        if allowed is not None and _name(target) not in allowed:
            found.append(f"{here} may not link to {there}. Write the name without brackets.")
        elif _is_adr(source):
            if not _beside_adr(target, source):
                found.append(
                    f"{here} is an ADR and may link only to other ADRs of its project. "
                    f"Write {there} without brackets."
                )
        elif _is_adr(target):
            if not _beside_adr(source, target):
                found.append(
                    f"{here} links to the ADR {there}. Only the project's architecture "
                    "overview page links to ADRs. Write its name without brackets."
                )
        elif target in projects and _name(source) != INDEX:
            if source not in projects:
                project_links[source].append(target)
            elif {_name(source), _name(target)} not in ALLOWED_PAIRS:
                found.append(
                    f"{here} and {there} are both project pages, and projects do not "
                    f"link to each other. Write {there} without brackets."
                )

    for source, targets in project_links.items():
        if len(targets) > 1:
            names = ", ".join(f"'{title[t]}'" for t in targets)
            found.append(
                f"'{title[source]}' links to {len(targets)} project pages ({names}). "
                "A note belongs to one project: keep that link, write the others "
                "without brackets."
            )

    if only is None:
        listed = {b for a, b in edges if _is_architecture(a) and _beside_adr(a, b)}
        for note in notes:
            if _is_adr(note.provider_id) and note.provider_id not in listed:
                found.append(
                    f"'{note.title}' is not linked from an architecture overview page "
                    "in its folder. Add it to that page's list of decisions."
                )
    return found
