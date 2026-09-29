"""Chunking rules (ADR-011). Pure functions: no database, no model.

Each rule here was found by the step 3 experiment on real notes; these pin them so
a change to the splitter cannot quietly undo one.
"""

from __future__ import annotations

from api.chunking import MAX_TOKENS, Chunk, chunk_markdown, embedding_text, tokens

# Enough words that a section is not merged away as too small.
FILLER = " ".join(["lorem"] * 60)


def test_splits_at_headings_and_keeps_the_heading_path():
    body = f"# Title\n{FILLER}\n\n## Decision\n{FILLER}\n\n### Detail\n{FILLER}\n\n## Tradeoffs\n{FILLER}"

    paths = [c.path for c in chunk_markdown(body)]

    assert paths == [
        ["Title"],
        ["Title", "Decision"],
        ["Title", "Decision", "Detail"],
        ["Title", "Tradeoffs"],  # a ## closes the ### below the previous ##
    ]


def test_a_hash_inside_a_code_block_is_not_a_heading():
    body = f"## Setup\n{FILLER}\n\n```bash\n# install it\nmake install\n```\n\n{FILLER}"

    chunks = chunk_markdown(body)

    assert [c.path for c in chunks] == [["Setup"]]
    assert "# install it" in chunks[0].text


def test_an_indented_code_block_is_still_a_code_block():
    """ADR-004's code block sits inside a bullet, so its fence is indented."""
    body = f"## Schema\n{FILLER}\n\n  ```sql\n# not a heading\n  ```\n\n{FILLER}"

    assert [c.path for c in chunk_markdown(body)] == [["Schema"]]


def test_horizontal_rules_split_notes_without_headings():
    """Architectural Intelligence has no headings — its versions are split by ---."""
    body = f"V1 {FILLER}\n\n---\n\nV2 {FILLER}\n\n---\n\nV3 {FILLER}"

    chunks = chunk_markdown(body)

    assert [c.text[:2] for c in chunks] == ["V1", "V2", "V3"]


def test_long_sections_are_cut_below_the_limit_with_overlap():
    paragraphs = [f"paragraph{i} " + " ".join(["word"] * 80) for i in range(12)]
    body = "## Long\n" + "\n\n".join(paragraphs)

    chunks = chunk_markdown(body)

    assert len(chunks) > 1
    # Overlap can push a piece a little past the target — never near the limit
    # that made the model refuse a whole note.
    assert all(tokens(c.text) < MAX_TOKENS * 1.25 for c in chunks)
    # The start of each later piece repeats the end of the one before it.
    assert chunks[0].text[-40:].split()[-1] in chunks[1].text[:300]


def test_tiny_chunks_merge_forward_and_take_the_bigger_parts_heading():
    body = f"- Date: today\n- Status: accepted\n\n## Context\n{FILLER}"

    chunks = chunk_markdown(body)

    assert len(chunks) == 1
    assert chunks[0].path == ["Context"]
    assert chunks[0].text.startswith("- Date: today")


def test_a_short_note_is_one_chunk():
    assert len(chunk_markdown("Just a couple of lines.\n\nNothing more.")) == 1


def test_empty_text_has_no_chunks():
    assert chunk_markdown("   \n\n  ") == []


def test_header_modes():
    chunk = Chunk("the text", ["ADR-004 — entity map", "Options"])

    assert embedding_text("ADR-004", chunk, "none") == "the text"
    assert embedding_text("ADR-004", chunk, "section") == "Options\n\nthe text"
    assert embedding_text("ADR-004", chunk, "full") == (
        "ADR-004 › ADR-004 — entity map › Options\n\nthe text"
    )
