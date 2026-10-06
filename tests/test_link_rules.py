"""Which [[links]] keep the note graph a tree (api/link_rules.py)."""

from __future__ import annotations

from api.link_rules import problems
from tests.test_graph import note

INDEX = note("Projects/Projects index.md", "[[Resume Builder]] [[Personal OS]] [[AE]]")
ARCHITECTURE = "Resume Builder/Resume Builder — architecture overview.md"


def vault(*extra):
    return [
        INDEX,
        note("Projects/Personal OS.md", "[[Projects index]]"),
        note("Projects/Resume Builder.md", "[[Resume Builder — architecture overview]]"),
        note("Projects/AE.md"),
        note(ARCHITECTURE, "[[Resume Builder]] [[ADR-001-facts]] [[ADR-002-claims]]"),
        note("Resume Builder/ADR-001-facts.md", "See [[ADR-002-claims]]."),
        note("Resume Builder/ADR-002-claims.md"),
        *extra,
    ]


def test_a_tidy_vault_has_no_problems():
    assert problems(vault()) == []


def test_an_adr_may_link_back_to_its_architecture_page():
    adr = note("Resume Builder/ADR-003-x.md", "[[Resume Builder — architecture overview]]")
    assert problems(vault(adr), only=adr.provider_id) == []


def test_an_adr_may_not_link_to_its_project_page():
    adr = note("Resume Builder/ADR-003-x.md", "Covers [[Resume Builder]]")
    (found,) = problems(vault(adr), only=adr.provider_id)
    assert "'ADR-003-x' is an ADR" in found and "'Resume Builder' without brackets" in found


def test_an_adr_may_not_link_to_an_adr_of_another_project():
    adr = note("AE/ADR-001 Drafts only.md", "Like [[ADR-001-facts]]")
    assert len(problems(vault(adr), only=adr.provider_id)) == 1


def test_only_the_architecture_page_links_to_adrs():
    page = note("Resume Builder/Eval plan.md", "From [[ADR-001-facts]]")
    (found,) = problems(vault(page), only=page.provider_id)
    assert "Only the project's architecture overview page links to ADRs" in found


def test_project_pages_do_not_link_to_each_other():
    notes = vault()
    notes[3] = note("Projects/AE.md", "After [[Resume Builder]]")
    (found,) = problems(notes, only="Projects/AE.md")
    assert "both project pages" in found


def test_the_allowed_pair_of_projects_may_link():
    notes = [
        note("Projects/Projects index.md", "[[Audiotour project]] [[Voice Generation]]"),
        note("Projects/Audiotour project.md", "[[Voice Generation]]"),
        note("Projects/Voice Generation.md", "[[Audiotour project]]"),
    ]
    assert problems(notes) == []


def test_a_note_belongs_to_one_project():
    one = note("Plan.md", "For [[AE]]")
    two = note("Plan.md", "For [[AE]] and [[Resume Builder]]")
    assert problems(vault(one), only="Plan.md") == []
    (found,) = problems(vault(two), only="Plan.md")
    assert "links to 2 project pages" in found


def test_master_resume_links_only_to_personal_os_and_the_index():
    fine = note("Master Resume.md", "[[Personal OS]] [[Projects index]]")
    wrong = note("Master Resume.md", "[[Personal OS]] [[Resume Builder]]")
    assert problems(vault(fine), only="Master Resume.md") == []
    (found,) = problems(vault(wrong), only="Master Resume.md")
    assert "'Master Resume' may not link to 'Resume Builder'" in found


def test_only_the_note_being_saved_is_judged():
    messy = note("Old.md", "[[ADR-001-facts]]")
    clean = note("New.md", "plain text")
    assert problems(vault(messy, clean), only="New.md") == []


def test_the_whole_vault_check_finds_an_adr_nobody_lists():
    (found,) = problems(vault(note("Resume Builder/ADR-009-loose.md")))
    assert "'ADR-009-loose' is not linked from an architecture overview page" in found
