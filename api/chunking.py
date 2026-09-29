"""Splitting text into chunks for embedding (ADR-011).

Production version of docs/experiments/embeddings/step3_chunking.py. The experiment
found the rules; this is them, behind a small surface:

    chunk_markdown(body)             -> [Chunk]   a note, split at its structure
    embedding_text(title, chunk, mode) -> str     what actually gets embedded

A note is split at its headings and horizontal rules. A section too long to be
about one thing is cut at paragraph breaks, with a little overlap; a piece too
small to mean much is merged into the next. Pure functions — no database, no
model — so every rule is testable on its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

MAX_TOKENS = 400  # about one idea — and far below the embedding model's limit
MIN_TOKENS = 40  # smaller than this carries too little meaning to embed alone
OVERLAP_CHARS = 200  # ~50 tokens repeated when a long section is cut mid-way

HEADING = re.compile(r"^(#{1,6})\s+(.*)")
FENCE = re.compile(r"^\s*(```|~~~)")  # \s*: a code block inside a bullet is indented
BREAK = re.compile(r"^\s*(---+|\*\*\*+)\s*$")  # horizontal rule: a break with no title

# What sits above a chunk when it is embedded:
#   none     the chunk text alone
#   section  the nearest heading only ("Options") — short, no repeated title
#   full     note title and every heading above ("ADR-004 › ADR-004 — … › Options")
# Step 3 found `full` moved q006 from #2 to #12; the eval chooses the default.
HeaderMode = Literal["none", "section", "full"]


def tokens(text: str) -> int:
    """Rough token count: ~4 characters per token for English."""
    return len(text) // 4


@dataclass
class Chunk:
    text: str
    path: list[str] = field(default_factory=list)  # headings above, outermost first


def embedding_text(title: str, chunk: Chunk, mode: HeaderMode) -> str:
    """The chunk as the model sees it, with the header the mode asks for."""
    if mode == "section" and chunk.path:
        return f"{chunk.path[-1]}\n\n{chunk.text}"
    if mode == "full":
        return f"{' › '.join([title, *chunk.path])}\n\n{chunk.text}"
    return chunk.text


def _sections(body: str) -> list[Chunk]:
    """Split at headings and horizontal rules, never inside a code block.

    `path` is a stack: a heading closes every heading at its level or deeper, which
    is how nesting like "Project phases › Phase 0 › Inspect" is kept.
    """
    result: list[Chunk] = []
    path: list[tuple[int, str]] = []
    lines: list[str] = []
    in_code = False

    def flush() -> None:
        text = "\n".join(lines).strip()
        if text:
            result.append(Chunk(text, [h for _, h in path]))
        lines.clear()

    for line in body.splitlines():
        if FENCE.match(line):
            in_code = not in_code
        heading = None if in_code else HEADING.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            path = [(lv, h) for lv, h in path if lv < level] + [(level, heading.group(2).strip())]
        elif not in_code and BREAK.match(line):
            flush()
        else:
            lines.append(line)
    flush()
    return result


def _split_long(text: str) -> list[str]:
    """Cut an over-long section at paragraph breaks, overlapping each cut."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    current = ""
    for paragraph in paragraphs:
        # A single paragraph bigger than a whole chunk (a long table, a code
        # block) is cut at word boundaries rather than sent whole and refused.
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
            # Start the next piece with the end of this one, so an idea cut in half
            # still appears whole in at least one piece.
            tail = current[-OVERLAP_CHARS:]
            current = tail[tail.find(" ") + 1 :] + "\n\n" + paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        pieces.append(current)
    return pieces


def chunk_markdown(body: str) -> list[Chunk]:
    """A note as chunks. A short note comes back as one chunk; empty text as none."""
    chunks: list[Chunk] = []
    for section in _sections(body):
        if tokens(section.text) > MAX_TOKENS:
            chunks.extend(Chunk(piece, section.path) for piece in _split_long(section.text))
        else:
            chunks.append(section)

    # Merge chunks too small to mean much into the next (or, for the last, into
    # the previous). The merged chunk keeps the bigger part's headings: the small
    # one was too small to be what the chunk is about.
    merged: list[Chunk] = []
    carry: Chunk | None = None
    for chunk in chunks:
        if carry:
            chunk = Chunk(f"{carry.text}\n\n{chunk.text}", chunk.path)
            carry = None
        if tokens(chunk.text) < MIN_TOKENS:
            carry = chunk
        else:
            merged.append(chunk)
    if carry:
        if merged:
            merged[-1] = Chunk(f"{merged[-1].text}\n\n{carry.text}", merged[-1].path)
        else:
            merged.append(carry)
    return merged
