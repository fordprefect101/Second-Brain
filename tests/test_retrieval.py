"""Chunk storage, the embedding pass, vector search and hybrid retrieval.

Ollama is never called. The fake embedder below hashes words into a 768-slot
vector — word overlap standing in for meaning — which is enough to test the
plumbing: that chunks follow their notes, that pending vectors are filled, that
the best chunk wins, and that everything degrades to keyword search when the
model is unreachable. What it cannot test is embedding QUALITY; that is the eval's
job, against the real model.
"""

from __future__ import annotations

import math
import re
import zlib

import pytest

from api import vectors
from api.embeddings import DIMENSIONS, EmbeddingsUnavailable
from api.hybrid import retrieve, rrf
from api.search import index_notes
from api.vectors import embed_pending, pending_count, vector_search
from tests.conftest import write_note

FILLER = " ".join(["lorem"] * 60)


def fake_vector(text: str) -> list[float]:
    vector = [0.0] * DIMENSIONS
    for word in re.findall(r"[a-z]+", text.lower()):
        vector[zlib.crc32(word.encode()) % DIMENSIONS] += 1.0
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


def fake_documents(texts: list[str]) -> list[list[float]]:
    return [fake_vector(t) for t in texts]


def unavailable(*_args):
    raise EmbeddingsUnavailable("Ollama is not running.")


def chunk_rows(db):
    with db.cursor() as cur:
        cur.execute(
            "select s.title, c.position, c.text, c.embedding is not null as embedded "
            "from search_chunks c join search_index s on s.entity_id = c.entity_id "
            "order by s.title, c.position"
        )
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Chunks follow their notes
# ---------------------------------------------------------------------------


def test_indexing_writes_chunks_without_vectors(db, provider, vault):
    write_note(vault, "Note.md", f"## One\n{FILLER}\n\n## Two\n{FILLER}")

    index_notes(db, provider)

    rows = chunk_rows(db)
    assert [r["position"] for r in rows] == [0, 1]
    assert not any(r["embedded"] for r in rows)
    assert pending_count(db) == 2


def test_the_embedding_pass_fills_every_pending_vector(db, provider, vault):
    write_note(vault, "Note.md", f"## One\n{FILLER}\n\n## Two\n{FILLER}")
    index_notes(db, provider)

    stats = embed_pending(db, embed=fake_documents)

    assert stats == {"embedded": 2}
    assert pending_count(db) == 0
    assert embed_pending(db, embed=fake_documents) == {"embedded": 0}  # nothing left


def test_an_edited_note_gets_fresh_chunks(db, provider, vault):
    """Chunk text must never outlive the content it came from (ADR-011)."""
    write_note(vault, "Note.md", f"Original words. {FILLER}")
    index_notes(db, provider)
    embed_pending(db, embed=fake_documents)

    write_note(vault, "Note.md", f"Replacement words. {FILLER}")
    index_notes(db, provider)

    [row] = chunk_rows(db)
    assert row["text"].startswith("Replacement")
    assert not row["embedded"]  # new text, so it waits for a new vector


def test_an_index_row_with_no_chunks_gets_one_from_its_stored_text(db, provider, vault):
    """Calendar events outside the fetch window stay indexed but are never re-read
    (prune=False), so they would never be chunked. The embedding pass covers any row
    like that from what the index already stores: title and excerpt."""
    write_note(vault, "Note.md", FILLER)
    index_notes(db, provider)
    with db.cursor() as cur:
        cur.execute("delete from search_chunks")  # a row indexed before chunks existed
    db.commit()

    embed_pending(db, embed=fake_documents)

    [row] = chunk_rows(db)
    assert row["text"].startswith("Note\n")
    assert row["embedded"]


def test_a_deleted_note_takes_its_chunks_with_it(db, provider, vault):
    gone = write_note(vault, "Gone.md", FILLER)
    write_note(vault, "Kept.md", FILLER)
    index_notes(db, provider)

    gone.unlink()
    index_notes(db, provider)

    assert {r["title"] for r in chunk_rows(db)} == {"Kept"}


# ---------------------------------------------------------------------------
# When the model is unreachable
# ---------------------------------------------------------------------------


def test_embedding_failure_leaves_chunks_pending_for_the_next_run(db, provider, vault):
    write_note(vault, "Note.md", FILLER)
    index_notes(db, provider)

    with pytest.raises(EmbeddingsUnavailable):
        embed_pending(db, embed=unavailable)
    db.rollback()

    assert pending_count(db) == 1
    assert embed_pending(db, embed=fake_documents) == {"embedded": 1}


def test_retrieval_falls_back_to_keyword_when_the_model_is_down(db, provider, vault):
    write_note(vault, "Pooling.md", f"Connection pooling notes. {FILLER}")
    index_notes(db, provider)

    result = retrieve(db, "what about pooling?", embed_query=unavailable)

    assert result.mode == "keyword"
    assert [h.title for h in result.hits] == ["Pooling"]
    assert result.context == {}  # the assistant then reads whole notes, as before


def test_vector_mode_refuses_to_fall_back(db, provider, vault):
    """The eval measures vector search on its own; a silent fallback would make it
    report keyword results under a vector label."""
    write_note(vault, "Note.md", FILLER)
    index_notes(db, provider)

    with pytest.raises(EmbeddingsUnavailable):
        retrieve(db, "anything", mode="vector", embed_query=unavailable)


def test_changing_the_header_mode_marks_every_vector_pending(db, provider, vault, monkeypatch):
    """Vectors made with different settings are not comparable — they must be
    redone, and the signature is what notices."""
    write_note(vault, "Note.md", f"## One\n{FILLER}\n\n## Two\n{FILLER}")
    index_notes(db, provider)
    embed_pending(db, embed=fake_documents)

    monkeypatch.setattr(vectors, "CHUNK_HEADER", "full")

    assert pending_count(db) == 2
    assert embed_pending(db, embed=fake_documents) == {"embedded": 2}


# ---------------------------------------------------------------------------
# Searching
# ---------------------------------------------------------------------------


def test_a_note_ranks_by_its_best_chunk(db, provider, vault):
    """Max-pooling: one strongly relevant section is enough, however much else the
    note is about. Averaging would dilute it under the unrelated sections."""
    unrelated = "\n\n".join(f"## Section {i}\n{FILLER}" for i in range(6))
    # Long enough to stay its own chunk — under MIN_TOKENS it would be merged into
    # the section before it, which is the chunker working, not this test.
    relevant = " ".join(["audio transcription midi tabs"] * 10)
    write_note(vault, "Long.md", f"{unrelated}\n\n## Transcription\n{relevant}")
    write_note(vault, "Short.md", "audio and other things entirely " + FILLER)
    index_notes(db, provider)
    embed_pending(db, embed=fake_documents)

    hits = vector_search(db, fake_vector("audio transcription midi tabs"))

    assert hits[0].chunk_path == ["Transcription"]


def test_rrf_rewards_agreement_over_one_strong_vote():
    """The worked example: #1 and #4 beats #5 and #5 beats #1 in one list only."""
    a, b, c = "a", "b", "c"
    keyword = [a, "x", "y", "z", c]
    vector = [b, "p", "q", a, c]

    scores = rrf([keyword, vector])

    assert scores[a] > scores[c] > scores[b]


def test_hybrid_returns_the_matching_section_not_the_whole_note(db, provider, vault):
    """What makes prompts small: the model reads one section, not the note."""
    write_note(
        vault,
        "ADR.md",
        f"## Context\n{FILLER}\n\n## Decision\nWe chose postgres for search quality. {FILLER}",
    )
    index_notes(db, provider)
    embed_pending(db, embed=fake_documents)

    result = retrieve(db, "why postgres for search", embed_query=fake_vector)

    assert result.mode == "hybrid"
    [context] = result.context.values()
    assert context.startswith("[Decision]")
    assert "Context" not in context
