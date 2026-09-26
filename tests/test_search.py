"""Search indexing and querying.

Covers the two properties that are easy to break without noticing:

  · incremental indexing actually skips unchanged content
  · deletions are inferred, since nothing reports them

Plus the ranking behaviour the FTS experiment established, so it cannot regress.
"""

from __future__ import annotations

from api.search import index_captures, index_notes, search
from tests.conftest import write_note


def make_capture(db, body: str, kind: str = "note"):
    with db.cursor() as cur:
        cur.execute(
            "insert into capture_items (body, kind) values (%s, %s) returning id",
            (body, kind),
        )
        capture_id = cur.fetchone()["id"]
    db.commit()
    return capture_id


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------


def test_indexes_every_note(db, provider, vault):
    write_note(vault, "One.md", "First note about Postgres.")
    write_note(vault, "Two.md", "Second note about Redis.")

    stats = index_notes(db, provider)

    assert stats["seen"] == 2
    assert stats["indexed"] == 2
    assert stats["skipped"] == 0


def test_second_run_skips_unchanged_notes(db, provider, vault):
    """Incremental indexing: unchanged content is not reindexed."""
    write_note(vault, "One.md", "Content.")
    index_notes(db, provider)

    stats = index_notes(db, provider)

    assert stats["indexed"] == 0
    assert stats["skipped"] == 1


def test_changed_content_is_reindexed(db, provider, vault):
    write_note(vault, "One.md", "Original content.")
    index_notes(db, provider)

    write_note(vault, "One.md", "Completely different content.")
    stats = index_notes(db, provider)

    assert stats["indexed"] == 1
    assert stats["skipped"] == 0


def test_retagging_triggers_reindex(db, provider, vault):
    """Tags are searchable, so changing them must change the fingerprint.

    A body-only hash would miss this and leave the index stale.
    """
    write_note(vault, "One.md", "---\ntags: [old]\n---\n\nSame body.\n")
    index_notes(db, provider)

    write_note(vault, "One.md", "---\ntags: [new]\n---\n\nSame body.\n")
    stats = index_notes(db, provider)

    assert stats["indexed"] == 1


def test_deleted_notes_are_removed_from_the_index(db, provider, vault):
    """Nothing reports a deletion, so absence from the listing is the signal."""
    write_note(vault, "Keep.md", "Keeping this.")
    gone = write_note(vault, "Delete.md", "Removing this.")
    index_notes(db, provider)

    gone.unlink()
    stats = index_notes(db, provider)

    assert stats["removed"] == 1
    with db.cursor() as cur:
        cur.execute("select title from search_index")
        assert [r["title"] for r in cur.fetchall()] == ["Keep"]


def test_empty_vault_does_not_wipe_other_providers(db, provider, vault):
    """Removal is scoped per provider, not global."""
    make_capture(db, "A capture")
    index_captures(db)

    index_notes(db, provider)  # vault is empty

    with db.cursor() as cur:
        cur.execute("select count(*) as n from search_index where provider='personal_os'")
        assert cur.fetchone()["n"] == 1


def test_archived_captures_are_not_indexed(db, provider):
    capture_id = make_capture(db, "Should not appear")
    with db.cursor() as cur:
        cur.execute(
            "update capture_items set status='archived' where id=%s", (capture_id,)
        )
    db.commit()

    stats = index_captures(db)

    assert stats["seen"] == 0


def test_excerpt_respects_the_length_constraint(db, provider, vault):
    """schema.sql enforces <= 500 chars. Indexing must not violate it (Plan.md §2)."""
    write_note(vault, "Long.md", "word " * 2000)

    index_notes(db, provider)

    with db.cursor() as cur:
        cur.execute("select length(excerpt) as n from search_index")
        assert cur.fetchone()["n"] <= 500


# ---------------------------------------------------------------------------
# Querying
# ---------------------------------------------------------------------------


def test_finds_a_note_by_its_body(db, provider, vault):
    write_note(vault, "Note.md", "Discussion of connection pooling.")
    index_notes(db, provider)

    hits = search(db, "pooling")

    assert [h.title for h in hits] == ["Note"]


def test_title_matches_outrank_body_matches(db, provider, vault):
    """setweight puts titles at weight A. A note *called* X beats one mentioning X."""
    write_note(vault, "Postgres.md", "Some unrelated content here.")
    write_note(vault, "Other.md", "A passing mention of postgres in the body.")
    index_notes(db, provider)

    hits = search(db, "postgres")

    assert [h.title for h in hits] == ["Postgres", "Other"]
    assert hits[0].rank > hits[1].rank


def test_stemming_matches_word_forms(db, provider, vault):
    write_note(vault, "Note.md", "The query was optimized last week.")
    index_notes(db, provider)

    assert len(search(db, "optimizing")) == 1


def test_prefix_matching(db, provider, vault):
    """Typing 'transcrip' should find 'Transcription' before you finish."""
    write_note(vault, "Transcription.md", "About audio.")
    index_notes(db, provider)

    hits = search(db, "transcrip")

    assert [h.title for h in hits] == ["Transcription"]
    # A prefix hit must score above zero, or it sorts below everything.
    assert hits[0].rank > 0


def test_search_spans_sources(db, provider, vault):
    write_note(vault, "Redis.md", "Notes on redis clustering.")
    make_capture(db, "Look into redis persistence")
    index_notes(db, provider)
    index_captures(db)

    sources = {h.source for h in search(db, "redis")}

    assert sources == {"obsidian", "personal_os"}


def test_source_filter(db, provider, vault):
    write_note(vault, "Redis.md", "Notes on redis.")
    make_capture(db, "Look into redis")
    index_notes(db, provider)
    index_captures(db)

    hits = search(db, "redis", source="obsidian")

    assert {h.source for h in hits} == {"obsidian"}


def test_empty_query_returns_nothing(db):
    assert search(db, "   ") == []


def test_no_matches_returns_empty_list(db, provider, vault):
    write_note(vault, "Note.md", "About cats.")
    index_notes(db, provider)

    assert search(db, "quantum") == []


# ---------------------------------------------------------------------------
# match="any" — whole questions, as the assistant sends them
# ---------------------------------------------------------------------------


def test_any_mode_finds_a_note_containing_only_some_words(db, provider, vault):
    """The bug the eval found: a question never has ALL its words in one note."""
    write_note(vault, "Pooling.md", "Discussion of connection pooling.")
    index_notes(db, provider)

    question = "why do we need pooling for the api"

    assert search(db, question) == []  # every-word mode: "need" and "api" are missing
    assert [h.title for h in search(db, question, match="any")] == ["Pooling"]


def test_any_mode_ranks_notes_matching_more_words_higher(db, provider, vault):
    write_note(vault, "Alpha.md", "Postgres connection pooling.")
    write_note(vault, "Beta.md", "Postgres only.")
    index_notes(db, provider)

    hits = search(db, "postgres connection pooling", match="any")

    assert [h.title for h in hits] == ["Alpha", "Beta"]
    assert hits[0].rank > hits[1].rank


def test_any_mode_does_not_prefix_match_the_last_word(db, provider, vault):
    """Prefix matching is for typing in progress. A finished question's last word
    matched as a prefix is how "app" found job applications and motor races."""
    write_note(vault, "Transcription.md", "About audio.")
    index_notes(db, provider)

    assert search(db, "tell me about transcrip", match="any") == []


def test_any_mode_with_only_stop_words_returns_nothing(db, provider, vault):
    write_note(vault, "Note.md", "Why is it that cats sleep so much?")
    index_notes(db, provider)

    assert search(db, "why is it?", match="any") == []
