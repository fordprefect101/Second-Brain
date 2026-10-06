"""Notes written through the API: frontmatter owned by the API, tags guarded, undo.

The vault is a throwaway directory (conftest) and the embedding model is switched
off, so these exercise the spelling fallback and never leave the machine.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from api import embeddings, note_writes
from api.note_writes import NoteWriteError, TagNeedsConfirmation
from tests.conftest import write_note


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    def unreachable(*_args, **_kwargs):
        raise embeddings.EmbeddingsUnavailable("no model in tests")

    monkeypatch.setattr(embeddings, "embed_query", unreachable)
    monkeypatch.setattr(embeddings, "embed_documents", unreachable)
    monkeypatch.setattr(note_writes, "embed_pending", unreachable)


def create(db, provider, **overrides):
    args = dict(
        title="ADR-001 Use Postgres",
        body="# ADR-001\n\nWe use Postgres.",
        note_type="adr",
        project="Etsy Automation",
        folder="Projects/Etsy Automation",
    )
    return note_writes.create_note(db, provider, **(args | overrides))


def test_create_writes_frontmatter_from_the_fields(db, provider, vault):
    written = create(db, provider)

    text = (vault / written.provider_id).read_text()
    assert text.startswith('---\nproject: "[[Etsy Automation]]"\ntags: [adr, etsy-automation]\n---\n')
    assert "We use Postgres." in text
    assert written.provider_id == "Projects/Etsy Automation/ADR-001 Use Postgres.md"


def test_created_note_reads_back_with_its_tags(db, provider):
    written = create(db, provider)
    assert provider.get_note(written.provider_id).tags == ["adr", "etsy-automation"]


def test_body_with_its_own_frontmatter_is_refused(db, provider):
    with pytest.raises(NoteWriteError, match="without frontmatter"):
        create(db, provider, body="---\ntags: [x]\n---\ntext")


def test_unknown_type_is_refused(db, provider):
    with pytest.raises(NoteWriteError, match="Unknown note type"):
        create(db, provider, note_type="memo")


def test_create_never_overwrites_an_existing_note(db, provider, vault):
    write_note(vault, "Projects/Etsy Automation/ADR-001 Use Postgres.md", "mine")
    with pytest.raises(NoteWriteError, match="already exists"):
        create(db, provider)
    assert (vault / "Projects/Etsy Automation/ADR-001 Use Postgres.md").read_text() == "mine"


def test_folder_outside_the_vault_is_refused(db, provider, vault):
    with pytest.raises(ValueError, match="escapes the vault"):
        create(db, provider, folder="../outside")
    assert not (vault.parent / "outside").exists()


def test_existing_topic_tag_is_accepted(db, provider, vault):
    write_note(vault, "a.md", "---\ntags: [postgres]\n---\nx")
    written = create(db, provider, topics=["Postgres"])
    assert written.tags == ["adr", "etsy-automation", "postgres"]
    assert written.new_tags == []


def test_new_tag_needs_confirmation_and_names_the_near_duplicate(db, provider, vault):
    write_note(vault, "a.md", "---\ntags: [postgres]\n---\nx")

    with pytest.raises(TagNeedsConfirmation) as refused:
        create(db, provider, topics=["postgresql"])

    assert refused.value.similar.tag == "postgres"
    assert list(vault.rglob("ADR-001*")) == []  # nothing was written


def test_new_tag_is_added_when_confirmed(db, provider):
    written = create(db, provider, topics=["billing"], allow_new_tags=True)
    assert written.new_tags == ["billing"]
    assert "billing" in note_writes.vault_tags(provider)


def test_too_many_topic_tags_is_refused(db, provider):
    with pytest.raises(NoteWriteError, match="At most 3"):
        create(db, provider, topics=["a", "b", "c", "d"], allow_new_tags=True)


def test_undo_of_a_create_moves_the_note_to_trash(db, provider, vault):
    written = create(db, provider)

    note_writes.undo_last_write(db, provider, written.entity_id)

    assert not (vault / written.provider_id).exists()
    assert (vault / ".trash" / "ADR-001 Use Postgres.md").exists()
    assert provider.list_notes() == []  # .trash is not part of the vault's notes


def test_update_keeps_frontmatter_and_undo_restores_the_text(db, provider, vault):
    written = create(db, provider)
    before = (vault / written.provider_id).read_text()
    read = provider.get_note(written.provider_id)

    note_writes.update_note(
        db, provider, written.entity_id, body="Changed.", base_modified_at=read.modified_at
    )
    after = (vault / written.provider_id).read_text()
    assert after.startswith('---\nproject: "[[Etsy Automation]]"') and "Changed." in after

    note_writes.undo_last_write(db, provider, written.entity_id)
    assert (vault / written.provider_id).read_text() == before


def test_update_is_refused_when_the_note_changed_since_it_was_read(db, provider, vault):
    written = create(db, provider)
    read = provider.get_note(written.provider_id)

    with pytest.raises(NoteWriteError, match="changed since"):
        note_writes.update_note(
            db,
            provider,
            written.entity_id,
            body="Changed.",
            base_modified_at=read.modified_at - timedelta(seconds=5),
        )
    assert "We use Postgres." in (vault / written.provider_id).read_text()


def test_project_page_carries_type_status_and_repos(db, provider, vault):
    written = create(
        db,
        provider,
        title="Etsy Automation",
        body="# Etsy Automation\n\n## What it is",
        note_type="project",
        folder="Projects",
        status="active",
        repos=["fordprefect101/etsy-automation"],
    )
    text = (vault / written.provider_id).read_text()
    assert text.startswith(
        '---\ntype: project\nstatus: "active"\nrepos:\n'
        '  - "fordprefect101/etsy-automation"\ntags: [project, etsy-automation]\n---\n'
    )
