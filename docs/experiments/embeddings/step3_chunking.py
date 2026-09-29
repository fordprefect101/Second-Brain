"""Step 3: split notes into chunks, embed each chunk, rank notes by their best chunk.

Step 2 found two things chunking should fix:
  · long notes are cut off before embedding — Plan was refused whole
  · a whole-note vector blurs the one paragraph that answers a question (q006)

This tests both, on the same notes and questions, and compares against step 2's
whole-note vectors side by side.

    .venv/bin/python docs/experiments/embeddings/step3_chunking.py
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass

import httpx

# Reuse step 2 instead of copying it. Running a script puts its own folder on the
# import path, so step2_embeddings.py next to this file imports directly.
from step2_embeddings import (
    DOC_PREFIX,
    EVAL,
    MODEL,
    OLLAMA_EMBED_URL,
    QUERY_PREFIX,
    STEP1_FAILURES,
    config,
    cosine,
    embed,
    rule,
)
from api.providers.obsidian import ObsidianVaultProvider

MAX_TOKENS = 400  # a chunk should be about one thing — and far below the model's limit
MIN_TOKENS = 40  # smaller than this carries too little meaning; merge it into the next
OVERLAP_CHARS = 200  # ~50 tokens repeated when a long section is cut mid-way
BATCH = 32  # chunks per embedding request


def tokens(text: str) -> int:
    """Rough token count: ~4 characters per token for English."""
    return len(text) // 4


@dataclass
class Chunk:
    note: str
    path: list[str]  # the headings above this chunk, outermost first
    text: str

    def with_context(self) -> str:
        """The chunk as it gets embedded: note title and headings on top.

        A chunk reading "Option 2 — build it now" means nothing on its own. With
        "ADR-004 › Decision" above it, the vector knows what it is about.
        """
        header = " › ".join([self.note, *self.path])
        return f"{header}\n\n{self.text}"

# ---------------------------------------------------------------------------
# 1. Splitting
# ---------------------------------------------------------------------------

HEADING = re.compile(r"^(#{1,6})\s+(.*)")
FENCE = re.compile(r"^\s*(```|~~~)")  # \s*: a code block inside a bullet is indented
BREAK = re.compile(r"^\s*(---+|\*\*\*+)\s*$")  # a horizontal rule: a section break with no title


def sections(body: str) -> list[tuple[list[str], str]]:
    """Split at headings and horizontal rules. Returns (heading path, text) pairs.

    Lines inside a code block are never treated as headings: a '# comment' in a
    bash snippet is code, not structure.
    """
    result: list[tuple[list[str], str]] = []
    path: list[tuple[int, str]] = []  # (level, heading) — a stack
    lines: list[str] = []
    in_code = False

    def flush() -> None:
        text = "\n".join(lines).strip()
        if text:
            result.append(([h for _, h in path], text))
        lines.clear()

    for line in body.splitlines():
        if FENCE.match(line):
            in_code = not in_code
        heading = None if in_code else HEADING.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            # A heading closes every heading at its level or deeper.
            path = [(lv, h) for lv, h in path if lv < level] + [(level, heading.group(2).strip())]
        elif not in_code and BREAK.match(line):
            flush()
        else:
            lines.append(line)
    flush()
    return result

def split_long(text: str) -> list[str]:
    """Cut a section that is too long, at paragraph breaks, with a little overlap."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    current = ""
    for paragraph in paragraphs:
        # One paragraph bigger than a whole chunk (a long table, a code block):
        # cut it at word boundaries rather than send something the model refuses.
        while tokens(paragraph) > MAX_TOKENS:
            cut = paragraph.rfind(" ", 0, MAX_TOKENS * 4)
            cut = cut if cut > 0 else MAX_TOKENS * 4
            if current:
                pieces.append(current)
                current = ""
            pieces.append(paragraph[:cut])
            paragraph = paragraph[cut:].strip()

        if current and tokens(current + paragraph) > MAX_TOKENS:
            pieces.append(current)
            # Overlap: start the next piece with the end of this one, so an idea cut
            # in half still appears whole in at least one piece.
            tail = current[-OVERLAP_CHARS:]
            current = tail[tail.find(" ") + 1 :] + "\n\n" + paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        pieces.append(current)
    return pieces

def chunk_note(title: str, body: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path, text in sections(body):
        pieces = split_long(text) if tokens(text) > MAX_TOKENS else [text]
        chunks.extend(Chunk(title, path, piece) for piece in pieces)

    # Merge chunks too small to mean much into the next one (or, if it is the last,
    # into the previous one). The merged chunk keeps the headings of the bigger
    # part — the small one was too small to be what the chunk is about.
    merged: list[Chunk] = []
    carry: Chunk | None = None
    for chunk in chunks:
        if carry:
            chunk = Chunk(title, chunk.path, f"{carry.text}\n\n{chunk.text}")
            carry = None
        if tokens(chunk.text) < MIN_TOKENS:
            carry = chunk
        else:
            merged.append(chunk)
    if carry:
        if merged:
            last = merged[-1]
            merged[-1] = Chunk(title, last.path, f"{last.text}\n\n{carry.text}")
        else:
            merged.append(carry)  # the whole note is one small chunk
    return merged

# ---------------------------------------------------------------------------
# 2. Embedding many chunks — batched, and with truncation OFF
# ---------------------------------------------------------------------------


def embed_all(texts: list[str], prefix: str) -> list[list[float]]:
    """Like step 2's embed(), but in batches, and refusing to silently cut anything.

    truncate=False makes Ollama return an error for an input that is too long,
    instead of quietly embedding only its beginning. If every chunk goes through,
    that is proof no chunk was cut.
    """
    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH):
        batch = [prefix + t for t in texts[start : start + BATCH]]
        response = httpx.post(
            OLLAMA_EMBED_URL,
            json={"model": MODEL, "input": batch, "truncate": False},
            timeout=300.0,
        )
        response.raise_for_status()
        vectors.extend(response.json()["embeddings"])
    return vectors


def rank_notes(query: list[float], chunks: list[Chunk], vectors: list[list[float]]):
    """Score every note by its BEST chunk. Returns [(score, note, best chunk)], best first.

    "Best chunk wins" (max-pooling): a note is as relevant as its most relevant
    part. Averaging its chunks instead would bring back step 2's blurring.
    """
    best: dict[str, tuple[float, Chunk]] = {}
    for chunk, vector in zip(chunks, vectors):
        score = cosine(query, vector)
        if chunk.note not in best or score > best[chunk.note][0]:
            best[chunk.note] = (score, chunk)
    return sorted(((s, note, c) for note, (s, c) in best.items()), key=lambda r: r[0], reverse=True)


def note_position(note: str, results) -> int:
    return next(i for i, (_, n, *_rest) in enumerate(results, start=1) if n == note)


def main() -> int:
    if config.vault_path is None:
        print("Set OBSIDIAN_VAULT_PATH in .env first.")
        return 1

    provider = ObsidianVaultProvider(config.vault_path)
    notes = [provider.get_note(n.provider_id) for n in provider.list_notes()]
    notes = [n for n in notes if n and n.body and len(n.body) > 100]
    chunks = [c for n in notes for c in chunk_note(n.title, n.body)]

    # ------------------------------------------------------------------
    rule("1. What the chunks look like")
    # ------------------------------------------------------------------
    sizes = [tokens(c.text) for c in chunks]
    print(f"{len(notes)} notes -> {len(chunks)} chunks")
    print(f"chunk size (est. tokens): min {min(sizes)}, median {int(statistics.median(sizes))}, max {max(sizes)}\n")
    per_note = Counter(c.note for c in chunks)
    for note, count in per_note.most_common(5):
        print(f"    {count:>3} chunks  {note}")
    print("\nADR-004, the note q006 needs, split into:")
    for c in (c for c in chunks if c.note.startswith("ADR-004")):
        first_line = c.text.splitlines()[0][:60]
        print(f"    [{' › '.join(c.path) or '(no heading)'}]  ~{tokens(c.text)} tok  \"{first_line}…\"")

    try:
        chunk_vectors = embed_all([c.with_context() for c in chunks], DOC_PREFIX)
    except httpx.ConnectError:
        print("\nOllama is not running. Start it with `ollama serve`.")
        return 1
    except httpx.HTTPStatusError as exc:
        print(f"\nA chunk was REFUSED as too long — the splitter let one through: {exc.response.text[:200]}")
        return 1

    # ------------------------------------------------------------------
    rule("2. Is anything cut off now?")
    # ------------------------------------------------------------------
    print(f"  all {len(chunks)} chunks embedded with truncation OFF — none were cut.")
    print(f"  In step 2, 'Plan' was refused whole. It is now {per_note['Plan']} chunks, all embedded in full.")

    # ------------------------------------------------------------------
    rule("3. Whole notes (step 2) vs chunks (step 3)")
    # ------------------------------------------------------------------
    titles = [n.title for n in notes]
    whole_vectors = embed([n.body for n in notes], DOC_PREFIX)  # step 2's way: truncated
    questions = [q for _, q, _, _ in EVAL]
    question_vectors = embed(questions, QUERY_PREFIX)
    print("  rank of the right note          which chunk matched\n")
    for (qid, _, expected, _), qv in zip(EVAL, question_vectors):
        whole = sorted(((cosine(qv, v), t) for v, t in zip(whole_vectors, titles)), reverse=True)
        whole_pos = next(i for i, (_, t) in enumerate(whole, start=1) if t == expected)
        results = rank_notes(qv, chunks, chunk_vectors)
        pos = note_position(expected, results)
        matched = next(c for _, n, c in results if n == expected)
        where = " › ".join(matched.path) or "(no heading)"
        print(f"  {qid}  whole #{whole_pos:<3} -> chunks #{pos:<3}   [{where[:45]}]")

    # ------------------------------------------------------------------
    rule("4. Does the title-and-heading header help?")
    # ------------------------------------------------------------------
    bare_vectors = embed_all([c.text for c in chunks], DOC_PREFIX)
    print("  rank of the right note: with header -> chunk text only\n")
    for (qid, _, expected, _), qv in zip(EVAL, question_vectors):
        with_pos = note_position(expected, rank_notes(qv, chunks, chunk_vectors))
        bare_pos = note_position(expected, rank_notes(qv, chunks, bare_vectors))
        print(f"  {qid}   #{with_pos}  ->  #{bare_pos}")

    # ------------------------------------------------------------------
    rule("5. Hub notes: still crowding?")
    # ------------------------------------------------------------------
    all_query_vectors = embed(STEP1_FAILURES, QUERY_PREFIX) + question_vectors
    top3 = Counter(
        note
        for qv in all_query_vectors
        for _, note, _ in rank_notes(qv, chunks, chunk_vectors)[:3]
    )
    print(f"  times each note landed in a top 3, over {len(all_query_vectors)} queries (step 2: Personal OS System 6x, Audiotour 4x)\n")
    for note, count in top3.most_common(6):
        print(f"    {count}x  {note}")
    print("\nA note with many chunks gets many chances to match. If long notes now")
    print("crowd the top instead of short ones, that is the cost of best-chunk-wins.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
