"""Tags for notes written through the API.

Two jobs, both about keeping one small vocabulary instead of a growing pile:

  1. suggest — which EXISTING tags fit a new note, by meaning
  2. guard   — before a NEW tag is added, is there an existing one that means the same

Both compare embeddings from the same model search uses. When that model cannot be
reached (it runs on another machine that may be asleep), both fall back to spelling
and say so: a spelling check catches postgres/postgresql, never db/database.

The two cut-offs were measured against nomic-embed-text on real pairs, not guessed
(2026-10-04): same-meaning tag pairs scored 0.69-0.91 and unrelated pairs at most
0.61; tags that fit a note scored 0.63-0.72 against it and unrelated ones at most
0.54. Single words embed closer together than sentences do, which is why these sit
far above the thresholds search uses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable, Sequence

from api import embeddings

# What a note IS. Exactly one per note; never suggested, never checked for likeness.
TYPE_TAGS = ("project", "adr", "plan", "learning", "note")

MAX_TOPIC_TAGS = 3

SAME_MEANING = 0.68  # tag vs tag
FITS_NOTE = 0.60  # note text vs tag
SAME_SPELLING = 0.85

# Long notes are cut for the comparison: the model refuses oversized input rather
# than truncating (embeddings.py), and the opening says what a note is about.
SUGGEST_TEXT_CHARS = 1500

Embedder = Callable[[list[str]], list[list[float]]]


@dataclass(frozen=True)
class SimilarTag:
    tag: str
    score: float
    by: str  # "meaning" or "spelling"


def normalize(tag: str) -> str:
    """One spelling per tag: lowercase, hyphens for spaces, no leading '#'."""
    cleaned = tag.strip().lstrip("#").lower()
    cleaned = re.sub(r"[\s_]+", "-", cleaned)
    cleaned = re.sub(r"[^a-z0-9/-]", "", cleaned)
    return re.sub(r"-{2,}", "-", cleaned).strip("-")


def slug(name: str) -> str:
    """A project name as a tag: 'Etsy Automation' -> 'etsy-automation'."""
    return normalize(name)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    # Vectors arrive normalized (embeddings.py), so the dot product is the cosine.
    return sum(x * y for x, y in zip(a, b))


def _spelling(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _embed_tags(tags: list[str]) -> list[list[float]]:
    return [embeddings.embed_query(t.replace("-", " ")) for t in tags]


def similar_existing(
    tag: str,
    existing: Sequence[str],
    *,
    embed: Embedder = _embed_tags,
) -> tuple[SimilarTag | None, bool]:
    """The existing tag closest to a proposed new one, if any is close enough.

    Returns (match, checked_meaning). checked_meaning is False when the model was
    unreachable and only spelling was compared — the caller must pass that on, since
    "no similar tag" then means less than it says.
    """
    candidates = [t for t in existing if t not in TYPE_TAGS and t != tag]
    if not candidates:
        return None, True

    by_spelling = max(candidates, key=lambda t: _spelling(tag, t))
    spelling_score = _spelling(tag, by_spelling)
    if spelling_score >= SAME_SPELLING:
        return SimilarTag(by_spelling, round(spelling_score, 2), "spelling"), True

    try:
        vectors = embed([tag, *candidates])
    except embeddings.EmbeddingsUnavailable:
        return None, False

    scored = [(_cosine(vectors[0], v), t) for v, t in zip(vectors[1:], candidates)]
    score, best = max(scored)
    if score >= SAME_MEANING:
        return SimilarTag(best, round(score, 2), "meaning"), True
    return None, True


def suggest_for_note(
    title: str,
    body: str,
    existing: Sequence[str],
    *,
    limit: int = MAX_TOPIC_TAGS,
    embed_note: Callable[[str], list[float]] | None = None,
    embed: Embedder = _embed_tags,
) -> tuple[list[SimilarTag], bool]:
    """Existing tags that fit a note, best first. Returns (tags, checked_meaning)."""
    candidates = [t for t in existing if t not in TYPE_TAGS]
    if not candidates:
        return [], True

    text = f"{title}\n\n{body}"[:SUGGEST_TEXT_CHARS]

    try:
        note_vector = (
            embed_note(text) if embed_note else embeddings.embed_documents([text])[0]
        )
        tag_vectors = embed(candidates)
    except embeddings.EmbeddingsUnavailable:
        # Without the model: a tag fits if the note literally uses the word.
        words = set(re.findall(r"[a-z0-9]+", text.lower()))
        hits = [t for t in candidates if set(t.split("-")) <= words]
        return [SimilarTag(t, 1.0, "spelling") for t in hits[:limit]], False

    scored = sorted(
        ((_cosine(note_vector, v), t) for v, t in zip(tag_vectors, candidates)),
        reverse=True,
    )
    return [
        SimilarTag(t, round(score, 2), "meaning")
        for score, t in scored[:limit]
        if score >= FITS_NOTE
    ], True
