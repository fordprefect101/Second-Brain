"""How [[links]] become lines between notes (api/graph.py), following Obsidian."""

from __future__ import annotations

from datetime import datetime, timezone

from api.graph import build_graph, link_targets
from api.services import ProviderNote


def note(path: str, body: str = "") -> ProviderNote:
    return ProviderNote(
        provider_id=path,
        title=path.rsplit("/", 1)[-1][:-3],
        excerpt="",
        modified_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        tags=[],
        body=body,
    )


def test_aliases_headings_and_md_are_stripped_from_targets():
    body = "See [[Plan|the plan]], [[backlog#AI-2]] and [[ADR index.md]]."
    assert link_targets(body) == ["Plan", "backlog", "ADR index"]


def test_attachments_and_links_in_code_are_not_notes():
    body = "![[diagram.png]]\n```\n[[Not a link]]\n```\nand `[[nor this]]`"
    assert link_targets(body) == []


def test_a_link_finds_its_note_by_filename_in_any_folder():
    graph = build_graph([note("Index.md", "[[ADR-001]]"), note("Personal OS/ADR-001.md")])
    assert graph.edges == [("Index.md", "Personal OS/ADR-001.md")]


def test_a_link_by_path_and_case_differences_still_match():
    graph = build_graph([note("a.md", "[[Personal OS/plan]]"), note("Personal OS/Plan.md")])
    assert graph.edges == [("a.md", "Personal OS/Plan.md")]


def test_with_two_notes_of_the_same_name_the_shorter_path_wins():
    notes = [note("a.md", "[[Plan]]"), note("Old/Archive/Plan.md"), note("Plan.md")]
    assert build_graph(notes).edges == [("a.md", "Plan.md")]


def test_repeats_and_self_links_are_dropped_and_missing_notes_counted():
    graph = build_graph([note("a.md", "[[b]] [[b]] [[a]] [[Nowhere]]"), note("b.md")])
    assert graph.edges == [("a.md", "b.md")]
    assert graph.unresolved == 1
