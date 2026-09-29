"""Chunks and their vectors: writing, embedding, and searching them (ADR-011).

Three steps, deliberately separate:

  replace_chunks()  on index — rewrite an item's chunks, vectors left NULL
  embed_pending()   after index — fill every vector that is missing or stale
  vector_search()   at query time — items ranked by their best chunk

Splitting "write chunks" from "embed chunks" is what lets indexing survive Ollama
being down. Chunks are always written; vectors arrive whenever the model is
reachable. Nothing is flagged: a chunk is pending exactly when its vector is NULL
or was made by a different embedder signature, so a model or header change
re-embeds precisely what it must, and a failed run simply leaves work for the next.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from api import embeddings
from api.chunking import Chunk, HeaderMode, chunk_markdown, embedding_text

# What sits above each chunk when it is embedded (see chunking.embedding_text).
# Chosen by the eval (ADR-011): `none` 23/25; `full` 19–20/25. Changing it marks
# every vector pending, and the next reindex re-embeds them all (~15s).
CHUNK_HEADER: HeaderMode = "none"

EMBED_BATCH = 64  # chunks per embedding pass iteration; each iteration commits

EmbedDocuments = Callable[[list[str]], list[list[float]]]


def embedder_signature() -> str:
    """Model + prefixes + header mode. Read at call time, so changing CHUNK_HEADER
    marks every vector pending on the next embedding pass."""
    return f"{embeddings.signature()}|header={CHUNK_HEADER}"


def _literal(vector: Sequence[float]) -> str:
    """pgvector's text form, '[0.1,0.2,…]'. Sent as text and cast in SQL, so no
    driver extension is needed — no new dependency (ADR-008)."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def chunks_for(title: str, body: str, excerpt: str) -> list[Chunk]:
    """A note is split at its structure. Anything with no body — a task, an event,
    a repo, a capture — is already small: one chunk, its title and excerpt."""
    if body.strip():
        return chunk_markdown(body)
    text = f"{title}\n{excerpt}".strip()
    return [Chunk(text)] if text else []


def replace_chunks(conn: psycopg.Connection, entity_id: UUID, chunks: list[Chunk]) -> None:
    """Rewrite one item's chunks, vectors NULL until the embedding pass.

    Called whenever the item's fingerprint changes — the same trigger that rebuilds
    its tsvector — so chunk text never outlives the content it came from (ADR-011).
    The caller commits.
    """
    with conn.cursor() as cur:
        cur.execute("delete from search_chunks where entity_id = %s", (entity_id,))
        cur.executemany(
            "insert into search_chunks (entity_id, position, heading_path, text) "
            "values (%s, %s, %s, %s)",
            [(entity_id, i, chunk.path, chunk.text) for i, chunk in enumerate(chunks)],
        )


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

_PENDING = "c.embedding is null or c.embedder is distinct from %(sig)s"


def pending_count(conn: psycopg.Connection) -> int:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"select count(*) as n from search_chunks c where {_PENDING}",
            {"sig": embedder_signature()},
        )
        return cur.fetchone()["n"]


def _backfill_missing_chunks(conn: psycopg.Connection) -> None:
    """Give any indexed item with no chunks one, from its stored title and excerpt.

    Chunks are written when an item is (re)indexed. Calendar events outside the
    current fetch window are kept in the index but never re-read (prune=False in
    index_events), so without this they would never be chunked — invisible to
    vector search for good. Title + excerpt is exactly what a single-chunk item's
    chunk is anyway; for a note it is a stopgap until the note next changes.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into search_chunks (entity_id, position, text)
            select s.entity_id, 0, trim(s.title || E'\\n' || coalesce(s.excerpt, ''))
              from search_index s
             where not exists (select 1 from search_chunks c where c.entity_id = s.entity_id)
            """
        )
    conn.commit()


def embed_pending(
    conn: psycopg.Connection, embed: EmbedDocuments = embeddings.embed_documents
) -> dict[str, int]:
    """Embed every chunk without a current vector. Returns {"embedded": n}.

    Raises EmbeddingsUnavailable if the model cannot be reached. Every batch is
    committed as it lands, so a failure part-way keeps what was done and the next
    run carries on from there.
    """
    sig = embedder_signature()
    header = CHUNK_HEADER
    embedded = 0
    _backfill_missing_chunks(conn)
    while True:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                select c.id, c.text, c.heading_path, s.title
                  from search_chunks c
                  join search_index s on s.entity_id = c.entity_id
                 where {_PENDING}
                 order by c.id
                 limit %(batch)s
                """,
                {"sig": sig, "batch": EMBED_BATCH},
            )
            rows = cur.fetchall()
        if not rows:
            return {"embedded": embedded}

        texts = [
            embedding_text(r["title"], Chunk(r["text"], list(r["heading_path"])), header)
            for r in rows
        ]
        vectors = embed(texts)
        if len(vectors) != len(rows):
            # Without this, the unfilled rows stay pending and the loop never ends.
            raise embeddings.EmbeddingsUnavailable(
                f"Model returned {len(vectors)} vectors for {len(rows)} chunks."
            )

        with conn.cursor() as cur:
            cur.executemany(
                "update search_chunks set embedding = %s::vector, embedder = %s where id = %s",
                [(_literal(v), sig, r["id"]) for v, r in zip(vectors, rows)],
            )
        conn.commit()
        embedded += len(rows)


# ---------------------------------------------------------------------------
# Searching
# ---------------------------------------------------------------------------


@dataclass
class VectorHit:
    entity_id: UUID
    score: float  # cosine similarity of the best chunk, ~0.45–0.65 in practice
    chunk_text: str
    chunk_path: list[str] = field(default_factory=list)


def _best_chunk_per_item(
    conn: psycopg.Connection,
    query_vector: Sequence[float],
    *,
    limit: int,
    entity_ids: list[UUID] | None = None,
) -> list[VectorHit]:
    """Each item's single best chunk, items ordered by that chunk's score.

    "Best chunk wins" (max-pooling): an item is as relevant as its most relevant
    part. DISTINCT ON keeps the first row per item, and the inner ORDER BY makes
    that the closest chunk. `<=>` is pgvector's cosine distance, so 1 - distance
    is cosine similarity. Exact search: every chunk is compared (ADR-008).
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            select entity_id, score, text, heading_path from (
                select distinct on (c.entity_id)
                       c.entity_id, c.text, c.heading_path,
                       1 - (c.embedding <=> %(q)s::vector) as score
                  from search_chunks c
                 where c.embedding is not null
                   and c.embedder = %(sig)s
                   and (%(ids)s::uuid[] is null or c.entity_id = any(%(ids)s::uuid[]))
                 order by c.entity_id, c.embedding <=> %(q)s::vector
            ) best
            order by score desc
            limit %(limit)s
            """,
            {
                "q": _literal(query_vector),
                "sig": embedder_signature(),
                "ids": entity_ids,
                "limit": limit,
            },
        )
        rows = cur.fetchall()
    return [
        VectorHit(r["entity_id"], float(r["score"]), r["text"], list(r["heading_path"]))
        for r in rows
    ]


def vector_search(
    conn: psycopg.Connection, query_vector: Sequence[float], *, limit: int = 20
) -> list[VectorHit]:
    """Every indexed item with a current vector, ranked by its best chunk."""
    return _best_chunk_per_item(conn, query_vector, limit=limit)


def best_chunks(
    conn: psycopg.Connection, query_vector: Sequence[float], entity_ids: list[UUID]
) -> dict[UUID, VectorHit]:
    """For each given item, the chunk closest to the question — what the assistant
    reads instead of the whole note."""
    if not entity_ids:
        return {}
    hits = _best_chunk_per_item(
        conn, query_vector, limit=len(entity_ids), entity_ids=entity_ids
    )
    return {h.entity_id: h for h in hits}
