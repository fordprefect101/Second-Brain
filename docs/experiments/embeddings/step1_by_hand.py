"""Cosine similarity from scratch, on your own notes. No library, no model.

The point is NOT to build something good. It is to build the simplest possible
"find similar documents", watch exactly where it fails, and understand what
embeddings are actually fixing — before adopting anything (Plan.md §3, §4).

    .venv/bin/python docs/experiments/embeddings/step1_by_hand.py
"""

from __future__ import annotations

import math
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from api.config import config  # noqa: E402
from api.providers.obsidian import ObsidianVaultProvider  # noqa: E402

WORD = re.compile(r"[a-z']+")

# Words too common to carry meaning. Same idea as Postgres stop words.
STOP = set(
    "the a an and or but is are was were be been being to of in on at for with "
    "as by from that this these those it its i you we they he she not no do does "
    "did have has had will would can could should may might must about into over "
    "we're it's".split()
)


def rule(text: str) -> None:
    print(f"\n{'─' * 74}\n{text}\n{'─' * 74}")


# ---------------------------------------------------------------------------
# 1. Text -> vector
# ---------------------------------------------------------------------------


def to_vector(text: str) -> Counter[str]:
    """A document as word counts. This IS a vector.

    Nothing mysterious about the word 'vector' here: it is a list of numbers with
    one slot per word in the vocabulary. 'redis appears 4 times, postgres 0 times'
    is a point in a space with one dimension per word.

    A Counter is that same list, stored sparsely — most words are 0, so listing
    only the non-zero ones is the same thing written compactly.
    """
    return Counter(w for w in WORD.findall(text.lower()) if w not in STOP and len(w) > 2)


# ---------------------------------------------------------------------------
# 2. Cosine similarity
# ---------------------------------------------------------------------------


def cosine(a: Counter[str], b: Counter[str]) -> float:
    """How close two vectors point, ignoring how long they are.

        cos = (a · b) / (|a| × |b|)

    The dot product on top rewards words the documents share. Dividing by both
    lengths is what makes this cosine rather than plain overlap — and it is the
    whole reason to use cosine instead of Euclidean distance.

    Euclidean asks 'how far apart are these points?'. A 4000-word note and a
    40-word note about the SAME topic are far apart under Euclidean simply because
    one is bigger. Cosine asks 'do they point the same way?', which is length
    independent — so a short note and a long note on one subject score as similar.

    Returns 0.0 (nothing shared) to 1.0 (identical direction). Never negative
    here, because word counts cannot be negative.
    """
    shared = set(a) & set(b)
    if not shared:
        return 0.0

    dot = sum(a[w] * b[w] for w in shared)
    magnitude_a = math.sqrt(sum(v * v for v in a.values()))
    magnitude_b = math.sqrt(sum(v * v for v in b.values()))
    return dot / (magnitude_a * magnitude_b)


def main() -> int:
    if config.vault_path is None:
        print("Set OBSIDIAN_VAULT_PATH in .env first.")
        return 1

    provider = ObsidianVaultProvider(config.vault_path)
    notes = [provider.get_note(n.provider_id) for n in provider.list_notes()]
    notes = [n for n in notes if n and n.body and len(n.body) > 100]

    docs = {n.title: to_vector(n.body) for n in notes}
    print(f"{len(docs)} notes with enough text to compare")

    # ------------------------------------------------------------------
    rule("1. What a document vector actually looks like")
    # ------------------------------------------------------------------
    title, vector = next(iter(docs.items()))
    print(f"'{title}' has {len(vector)} distinct words. Its top 8 dimensions:\n")
    for word, count in vector.most_common(8):
        print(f"    {word:18} {count}")
    print("\nEvery other word in the vocabulary is 0. That full list of numbers,")
    print("one slot per word, is the vector. Nothing more than that.")

    # ------------------------------------------------------------------
    rule("2. Which of your notes are most similar?")
    # ------------------------------------------------------------------
    pairs = sorted(
        (
            (cosine(docs[a], docs[b]), a, b)
            for i, a in enumerate(docs)
            for b in list(docs)[i + 1 :]
        ),
        reverse=True,
    )

    print("Most similar:")
    for score, a, b in pairs[:5]:
        print(f"    {score:.3f}   {a[:30]:32} ~ {b[:30]}")
    print("\nLeast similar:")
    for score, a, b in pairs[-3:]:
        print(f"    {score:.3f}   {a[:30]:32} ~ {b[:30]}")

    # ------------------------------------------------------------------
    rule("3. Searching by similarity instead of by keyword")
    # ------------------------------------------------------------------
    for query in ["music transcription audio", "building a resume with AI"]:
        query_vector = to_vector(query)
        ranked = sorted(
            ((cosine(query_vector, v), t) for t, v in docs.items()), reverse=True
        )
        print(f"\n  query: {query!r}")
        for score, title in ranked[:3]:
            print(f"      {score:.3f}  {title}")

    # ------------------------------------------------------------------
    rule("4. WHERE IT BREAKS — the reason embeddings exist")
    # ------------------------------------------------------------------
    print("Every query above shared literal words with the notes it found.")
    print("Now ask the same questions using DIFFERENT words:\n")

    for query in [
        "converting songs into sheet music",   # no shared words with the note
        "why did I pick one tool over another",
        "helping someone find a job",
    ]:
        query_vector = to_vector(query)
        ranked = sorted(
            ((cosine(query_vector, v), t) for t, v in docs.items()), reverse=True
        )
        best_score, best_title = ranked[0]
        verdict = "found nothing" if best_score < 0.02 else f"{best_score:.3f} {best_title}"
        print(f"    {query!r:42} -> {verdict}")

    print("\nThis is the ceiling. The vector has one slot per WORD, so two")
    print("documents are similar only if they use the SAME words. 'transcription'")
    print("and 'sheet music' occupy different slots and never overlap — the")
    print("similarity is 0 no matter how related the ideas are.")
    print("\nPostgres full-text search has exactly this limit, with stemming on top.")
    print("It is not a bug in the maths; the maths is fine. The vector is wrong.")

    # ------------------------------------------------------------------
    rule("5. What embeddings change")
    # ------------------------------------------------------------------
    print("An embedding replaces 'one slot per word' with 'a few hundred slots")
    print("learned from reading a lot of text'. The slots have no names, and no")
    print("single one means anything on its own.")
    print()
    print("What matters is that a model trained to put related text near related")
    print("text produces vectors where 'transcription' and 'sheet music' land")
    print("CLOSE, because they occurred in similar contexts in the training data.")
    print()
    print("The cosine function above does not change at all. Same formula, same")
    print("code. Only the vectors change — which is why building this first is")
    print("worth it: embeddings are not a new kind of search, just better vectors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
