"""ObsidianVaultProvider — parsing, path safety, atomic writes.

The parsing cases come from what the experiment in docs/experiments/markdown-parsing
established. They are here so those findings cannot silently regress.

The path-safety cases matter most: this is the only code in the project that writes
to the user's own files.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from api.services import ProviderNote
from tests.conftest import write_note


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_frontmatter_is_positional(provider, vault):
    """'---' counts as frontmatter only on the very first line."""
    write_note(vault, "First.md", "---\ntags: [work]\n---\n\nBody here.\n")
    write_note(vault, "Second.md", "Intro paragraph.\n\n---\n\nNot frontmatter.\n")

    notes = {n.title: n for n in provider.list_notes()}

    assert notes["First"].tags == ["work"]
    # A horizontal rule mid-document must not be parsed as metadata.
    assert notes["Second"].tags == []


def test_tags_merge_frontmatter_and_inline(provider, vault):
    write_note(vault, "N.md", "---\ntags: [work, db]\n---\n\nRead #later today.\n")

    note = provider.list_notes()[0]

    assert note.tags == ["db", "later", "work"]


def test_hashtags_in_code_are_not_tags(provider, vault):
    write_note(
        vault,
        "N.md",
        "Real #tag here.\n\n"
        "```python\n# comment\nx = '#notatag'\n```\n\n"
        "Inline `#alsonot` and a URL https://x.com/docs#fragment\n",
    )

    assert provider.list_notes()[0].tags == ["tag"]


def test_malformed_frontmatter_does_not_lose_the_note(provider, vault):
    """Broken YAML must degrade, not raise. A note is not lost over bad metadata."""
    write_note(vault, "Broken.md", "---\ntags: [unclosed\n  bad: : :\n---\n\nBody.\n")

    notes = provider.list_notes()

    assert len(notes) == 1
    assert notes[0].title == "Broken"


def test_excerpt_is_truncated(provider, vault):
    """Excerpts are for display, never a copy of the note (Plan.md §2)."""
    write_note(vault, "Long.md", "word " * 500)

    excerpt = provider.list_notes()[0].excerpt

    assert len(excerpt) <= 245  # EXCERPT_CHARS plus the ellipsis
    assert excerpt.endswith("…")


def test_obsidian_config_is_skipped(provider, vault):
    write_note(vault, ".obsidian/plugin-notes.md", "Not a real note.")
    write_note(vault, "Real.md", "A real note.")

    assert [n.title for n in provider.list_notes()] == ["Real"]


def test_bad_encoding_does_not_abort_the_listing(provider, vault):
    """One unreadable file must not take out the whole vault."""
    (vault / "Bad.md").write_bytes(b"\xff\xfe invalid utf-8 \x00")
    write_note(vault, "Good.md", "Fine.")

    assert len(provider.list_notes()) == 2


# ---------------------------------------------------------------------------
# Path safety — the important ones
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "../../.ssh/id_rsa",
        "Notes/../../escape",
        "..",
        "...",
        "/etc/passwd",
        "a\\b",
        "with:colon",
        "null\x00byte",
    ],
)
def test_safe_filename_removes_separators(provider, title):
    cleaned = provider.safe_filename(title)

    assert "/" not in cleaned
    assert "\\" not in cleaned
    assert "\x00" not in cleaned
    assert not cleaned.startswith(".")
    assert cleaned  # never empty — an empty filename would raise later


def test_safe_filename_caps_length(provider):
    assert len(provider.safe_filename("x" * 500)) <= 80


def test_created_note_stays_inside_the_vault(provider, vault):
    """The end-to-end property: hostile input cannot write outside the vault."""
    note = provider.create_note("../../escaped", "body")

    written = (vault / note.provider_id).resolve()

    assert vault.resolve() in written.parents


def test_resolve_rejects_paths_outside_the_vault(provider, vault):
    outside = vault.parent / "outside.md"
    outside.write_text("secret")

    assert provider.get_note("../outside.md") is None


def test_resolve_rejects_symlink_escape(provider, vault):
    """resolve() follows symlinks, which is why it must happen before the check."""
    outside = vault.parent / "outside.md"
    outside.write_text("secret")
    (vault / "link.md").symlink_to(outside)

    assert provider.get_note("link.md") is None


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def test_create_note_writes_content(provider, vault):
    note = provider.create_note("My Note", "# Heading\n\nBody.\n")

    assert (vault / note.provider_id).read_text() == "# Heading\n\nBody.\n"
    assert isinstance(note, ProviderNote)


def test_create_never_overwrites(provider, vault):
    first = provider.create_note("Same", "original")
    second = provider.create_note("Same", "different")

    assert first.provider_id != second.provider_id
    assert (vault / first.provider_id).read_text() == "original"


def test_write_leaves_no_temp_files(provider, vault):
    """The temp file used for atomic replacement must always be cleaned up."""
    provider.create_note("Note", "body")

    leftovers = [p.name for p in vault.rglob("*") if "personal-os-tmp" in p.name]

    assert leftovers == []


def test_write_is_atomic_on_failure(provider, vault, monkeypatch):
    """If the write dies midway, the original file must be untouched.

    This is the whole reason for temp-then-replace. With a plain open(path, 'w'),
    the file would already be truncated by the time this failure happened, and the
    note would be gone.
    """
    provider.create_note("Existing", "IMPORTANT ORIGINAL CONTENT")
    path = vault / "Existing.md"

    def explode(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", explode)

    with pytest.raises(OSError):
        provider.update_note("Existing.md", "replacement")

    assert path.read_text() == "IMPORTANT ORIGINAL CONTENT"


def test_update_missing_note_raises(provider):
    with pytest.raises(FileNotFoundError):
        provider.update_note("Nope.md", "body")


def test_read_raw_returns_exact_bytes(provider, vault):
    """Snapshots must capture the file as-is, not a parsed version of it."""
    content = "---\ntags: [x]\n---\n\n  odd   spacing\t\n"
    write_note(vault, "N.md", content)

    assert provider.read_raw("N.md") == content
