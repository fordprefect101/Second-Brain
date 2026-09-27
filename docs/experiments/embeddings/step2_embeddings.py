"""Step 2: the same cosine similarity, with real embeddings instead of word counts.

Step 1 ended on a claim: "the cosine function does not change — only the vectors
change." This tests it. Same notes, same queries that step 1 could not answer,
plus five questions from the eval set: three keyword search misses and two it
gets right.

    ollama serve                  # if the Ollama app is not already running
    ollama pull nomic-embed-text
    .venv/bin/python docs/experiments/embeddings/step2_embeddings.py
"""

from __future__ import annotations

import math
import sys
from collections import Counter
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from api.config import config  # noqa: E402
from api.providers.obsidian import ObsidianVaultProvider  # noqa: E402

OLLAMA_EMBED_URL = "http://localhost:11434/api/embed"
MODEL = "nomic-embed-text"

# nomic-embed-text was trained with these prefixes and expects them. Without them
# it still returns numbers — just worse ones. Section 4 measures how much worse.
DOC_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "

# The queries step 1 could not answer: no words in common with the right note.
STEP1_FAILURES = [
    "converting songs into sheet music",
    "why did I pick one tool over another",
    "helping someone find a job",
]

# From evals/dataset.jsonl: (id, question, the note that answers it, keyword rank today).
EVAL = [
    ("q006", "why not just use the file name to identify a note everywhere in the app?",
     "ADR-004-entity-map-from-day-one", None),
    ("q017", "which tool would I use to split a song into its separate instruments?",
     "Automated Music Transcription", None),
    ("q022", "which model turns my notes into vectors?",
     "Personal OS System", None),
    ("q003", "why did we skip an ORM, wouldn't it make switching databases easier?",
     "ADR-003-raw-sql-over-orm", 1),
    ("q004", "why do we write the Google and GitHub connections ourselves instead of "
             "using existing MCP servers?",
     "ADR-006-direct-apis-over-mcp", 3),
]

def rule(text: str) -> None:
    print(f"\n{'─' * 74}\n{text}\n{'─' * 74}")


# ---------------------------------------------------------------------------
# 1. Text -> vector, now by a model
# ---------------------------------------------------------------------------


def embed(texts: list[str], prefix: str) -> list[list[float]]:
    """Many texts in one request. Returns one vector per text, in the same order."""
    response = httpx.post(
        OLLAMA_EMBED_URL,
        json={"model": MODEL, "input": [prefix + t for t in texts]},
        timeout=120.0,
    )
    response.raise_for_status()
    return response.json()["embeddings"]


# ---------------------------------------------------------------------------
# 2. Cosine similarity — same formula as step 1, different shape of vector
# ---------------------------------------------------------------------------


def cosine(a: list[float], b: list[float]) -> float:
    """cos = (a · b) / (|a| × |b|), exactly as in step 1.

    What changed is the loop. Step 1's vectors were sparse dictionaries — only the
    words a note contained — so it looped over shared KEYS. An embedding is dense:
    768 numbers, every slot filled, slot 1 always meaning the same learned thing.
    So the loop pairs up slot 1 with slot 1, slot 2 with slot 2: zip.

    Two other differences from step 1:
      · no "nothing shared → 0.0" shortcut: dense vectors always share every slot
      · the result can be negative now, because embedding values can be
    """
    dot = sum(x * y for x, y in zip(a, b))
    magnitude_a = math.sqrt(sum(x * x for x in a))
    magnitude_b = math.sqrt(sum(y * y for y in b))
    return dot / (magnitude_a * magnitude_b)


def ranked(query: list[float], docs: list[list[float]], titles: list[str]) -> list[tuple[float, str]]:
    """Every note, most similar first. Every note gets a score — there is no 'no match'."""
    return sorted(((cosine(query, d), t) for d, t in zip(docs, titles)), reverse=True)


def position(title: str, results: list[tuple[float, str]]) -> int:
    return next(i for i, (_, t) in enumerate(results, start=1) if t == title)


def main() -> int:
    if config.vault_path is None:
        print("Set OBSIDIAN_VAULT_PATH in .env first.")
        return 1

    provider = ObsidianVaultProvider(config.vault_path)
    notes = [provider.get_note(n.provider_id) for n in provider.list_notes()]
    notes = [n for n in notes if n and n.body and len(n.body) > 100]
    titles = [n.title for n in notes]
    bodies = [n.body for n in notes]

    try:
        doc_vectors = embed(bodies, DOC_PREFIX)
    except httpx.ConnectError:
        print("Ollama is not running. Start it with `ollama serve`.")
        return 1
    print(f"{len(notes)} notes embedded")

    # ------------------------------------------------------------------
    rule("1. What an embedding actually looks like")
    # ------------------------------------------------------------------
    vector = doc_vectors[0]
    print(f"'{titles[0]}' became {len(vector)} numbers. The first eight:\n")
    print("    " + "  ".join(f"{x:+.3f}" for x in vector[:8]))
    print(f"\n    magnitude (length) of this vector: {math.sqrt(sum(x * x for x in vector)):.3f}")
    print("\nNo slot is a word any more, and no single slot means anything alone.")
    print("If the magnitude is 1.000, the model already normalized it — and then")
    print("cosine is just the dot product. Check a second note to be sure.")

        # ------------------------------------------------------------------
    rule("2. Step 1's failures, retried")
    # ------------------------------------------------------------------
    for query, query_vector in zip(STEP1_FAILURES, embed(STEP1_FAILURES, QUERY_PREFIX)):
        print(f"\n  query: {query!r}")
        for score, title in ranked(query_vector, doc_vectors, titles)[:3]:
            print(f"      {score:.3f}  {title}")

    # ------------------------------------------------------------------
    rule("3. Eval questions: embeddings vs today's keyword search")
    # ------------------------------------------------------------------
    questions = [q for _, q, _, _ in EVAL]
    question_vectors = embed(questions, QUERY_PREFIX)
    print("  rank of the right note (top 5 counts as found)\n")
    for (qid, _, expected, keyword), qv in zip(EVAL, question_vectors):
        results = ranked(qv, doc_vectors, titles)
        pos = position(expected, results)
        kw = f"#{keyword}" if keyword else "missed"
        print(f"  {qid}  embeddings #{pos:<3} keyword {kw:<7} top 3: {[t for _, t in results[:3]]}")
    print("\nNot a perfectly fair race: keyword ranks were over notes AND tasks, events")
    print("and repos; this only ranks notes. Fewer competitors makes embeddings look better.")

    # ------------------------------------------------------------------
    rule("4. Do the prefixes matter?")
    # ------------------------------------------------------------------
    bare_docs = embed(bodies, "")
    bare_questions = embed(questions, "")
    print("  rank of the right note: with prefixes -> without\n")
    for (qid, _, expected, _), with_q, bare_q in zip(EVAL, question_vectors, bare_questions):
        with_pos = position(expected, ranked(with_q, doc_vectors, titles))
        bare_pos = position(expected, ranked(bare_q, bare_docs, titles))
        print(f"  {qid}   #{with_pos}  ->  #{bare_pos}")

        # ------------------------------------------------------------------
    rule("5. Do long notes crowd everything out?")
    # ------------------------------------------------------------------
    all_query_vectors = embed(STEP1_FAILURES, QUERY_PREFIX) + question_vectors
    top3 = Counter(
        title
        for qv in all_query_vectors
        for _, title in ranked(qv, doc_vectors, titles)[:3]
    )
    words = {n.title: len(n.body.split()) for n in notes}
    print(f"  times each note landed in a top 3, over {len(all_query_vectors)} queries\n")
    for title, count in top3.most_common(6):
        print(f"    {count}x  {title:45} ({words[title]} words)")
    print("\nOne vector per note has to summarise the WHOLE note. A note covering 25")
    print("topics becomes an average of 25 topics — close to many queries, the best")
    print("match for few. If long notes dominate here, that is the case for chunking.")

    # ------------------------------------------------------------------
    rule("6. Is the longest note cut off before it is embedded?")
    # ------------------------------------------------------------------
    longest = max(notes, key=lambda n: len(n.body))
    print(f"  longest note: '{longest.title}', ~{len(longest.body) // 4} tokens (estimate)\n")
    response = httpx.post(
        OLLAMA_EMBED_URL,
        json={"model": MODEL, "input": DOC_PREFIX + longest.body, "truncate": False},
        timeout=120.0,
    )
    if response.status_code == 200:
        read = response.json().get("prompt_eval_count")
        print(f"  embedded whole with truncation OFF. Tokens the model read: {read}")
    else:
        print(f"  REFUSED with truncation off ({response.status_code}): {response.text[:200]}")
        print("  So with the default (truncation on), the end of this note is silently")
        print("  dropped, and its vector only reflects the beginning.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


