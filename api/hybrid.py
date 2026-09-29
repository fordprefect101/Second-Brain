"""Retrieval for the assistant: keyword and vector search, merged (ADR-011).

    question ─┬─ keyword search (match="any", per item) ─┐
              └─ vector search (best chunk per item) ─────┴─ Reciprocal Rank Fusion

Two rankings on incompatible scales — ts_rank reaches ~0.7, cosine similarities
bunch between ~0.45 and 0.65 with no natural cut-off — so they are merged by
POSITION, never by score: each list gives an item 1 / (60 + its rank), and the
totals decide. An item both searches like beats one that only one search loved,
which is the point: each covers the other's blind spot.

If the embedding model is unreachable, retrieval degrades to keyword-only and says
so in the log. The assistant keeps working with AI search switched off.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from api import embeddings
from api.search import SearchHit, search
from api.vectors import best_chunks, vector_search

logger = logging.getLogger("personal-os.hybrid")

Mode = Literal["keyword", "vector", "hybrid"]

RRF_K = 60  # from the original RRF paper (Cormack et al., 2009); rarely worth tuning
CANDIDATES = 20  # taken from each list before merging

EmbedQuery = Callable[[str], list[float]]


def rrf(
    rankings: Sequence[Sequence[UUID]],
    *,
    k: int = RRF_K,
    weights: Sequence[float] | None = None,
) -> dict[UUID, float]:
    """Reciprocal Rank Fusion. Each ranking gives an item weight / (k + position).

    Weights are 1.0 each unless the eval shows otherwise — they are not a matter of
    opinion about which search is "better".
    """
    scores: dict[UUID, float] = {}
    for i, ranking in enumerate(rankings):
        weight = weights[i] if weights else 1.0
        for position, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + weight / (k + position)
    return scores


@dataclass
class Retrieval:
    hits: list[SearchHit]
    # Best-matching chunk per note, for the model to read instead of the whole
    # note. Empty when vectors were unavailable — the caller falls back.
    context: dict[UUID, str] = field(default_factory=dict)
    mode: Mode = "hybrid"  # what actually ran: "keyword" after a fallback


def _hits_for(conn: psycopg.Connection, ids: list[UUID], scores: dict[UUID, float]) -> list[SearchHit]:
    """SearchHits for ids found only by vector search, which returns ids, not rows."""
    if not ids:
        return []
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "select entity_id, title, excerpt, provider, source_modified_at "
            "from search_index where entity_id = any(%s)",
            (ids,),
        )
        rows = {r["entity_id"]: r for r in cur.fetchall()}
    return [
        SearchHit(
            id=i,
            title=rows[i]["title"],
            excerpt=rows[i]["excerpt"] or "",
            source=rows[i]["provider"],
            rank=scores[i],
            modified_at=rows[i]["source_modified_at"],
        )
        for i in ids
        if i in rows
    ]


def _context(conn: psycopg.Connection, query_vector: list[float], hits: list[SearchHit]) -> dict[UUID, str]:
    """The best chunk of each NOTE among the hits.

    Only notes: a task or event is one short chunk that repeats its own index row,
    which the model already sees. A note's best chunk replaces reading the whole
    note — a few hundred tokens instead of a few thousand.
    """
    note_ids = [h.id for h in hits if h.source == "obsidian"]
    chunks = best_chunks(conn, query_vector, note_ids)
    return {
        entity_id: (f"[{' › '.join(c.chunk_path)}]\n{c.chunk_text}" if c.chunk_path else c.chunk_text)
        for entity_id, c in chunks.items()
    }


def retrieve(
    conn: psycopg.Connection,
    question: str,
    *,
    limit: int = 5,
    mode: Mode = "hybrid",
    embed_query: EmbedQuery = embeddings.embed_query,
) -> Retrieval:
    """The top `limit` items for a question, plus the chunk text to answer from.

    mode="keyword" and mode="vector" exist for the eval, to measure each half on
    its own. The assistant uses "hybrid".
    """
    keyword = [] if mode == "vector" else search(conn, question, limit=CANDIDATES, match="any")
    if mode == "keyword":
        return Retrieval(keyword[:limit], mode="keyword")

    try:
        query_vector = embed_query(question)
    except embeddings.EmbeddingsUnavailable as exc:
        if mode == "vector":
            raise  # measuring vector search without vectors would be a silent lie
        logger.warning("Vector search unavailable; answering from keyword search only: %s", exc)
        return Retrieval(keyword[:limit], mode="keyword")

    vector = vector_search(conn, query_vector, limit=CANDIDATES)

    if mode == "vector":
        scores = {v.entity_id: v.score for v in vector}
    else:
        scores = rrf([[h.id for h in keyword], [v.entity_id for v in vector]])

    # sorted() is stable and keyword ids were inserted first, so exact ties keep
    # keyword order.
    top = sorted(scores, key=scores.get, reverse=True)[:limit]

    by_id = {h.id: h for h in keyword}
    missing = [i for i in top if i not in by_id]
    by_id.update({h.id: h for h in _hits_for(conn, missing, scores)})
    hits = []
    for i in top:
        if i in by_id:
            hit = by_id[i]
            hit.rank = scores[i]  # the fused score, not ts_rank
            hits.append(hit)

    return Retrieval(hits, _context(conn, query_vector, hits), mode=mode)
