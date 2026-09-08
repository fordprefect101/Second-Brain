"""Obsidian vault provider — reads markdown files from disk.

Production version of docs/experiments/markdown-parsing/parse.py. The experiment
established what the traps are; this handles them behind the NoteService interface.

READ ONLY. create_note and update_note raise deliberately (ADR-005): writing to a
personal knowledge vault needs atomic writes, snapshots, and an undo path first.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from api.services import NoteWritesNotSupported, ProviderNote

EXCERPT_CHARS = 240
SKIP_DIRS = {".obsidian", ".trash"}

# See parse.py for why each of these is shaped the way it is.
FENCE_RE = re.compile(r"^(```|~~~).*?^\1", re.MULTILINE | re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
INLINE_TAG_RE = re.compile(r"(?:^|\s)#([A-Za-z][\w/-]*)")
WIKILINK_RE = re.compile(r"!?\[\[([^\]\n]+)\]\]")
MD_SYNTAX_RE = re.compile(r"^[#>\-*\s]+|\*\*|__|[*_`]")


class ObsidianVaultProvider:
    """Satisfies NoteService structurally — no inheritance needed."""

    source_id = "obsidian"

    def __init__(self, vault_root: Path):
        self.vault_root = vault_root.resolve()

    # -- safety ----------------------------------------------------------

    def _is_inside_vault(self, candidate: Path) -> bool:
        """Resolve FIRST, then compare.

        resolve() collapses '..' and follows symlinks. Checking a path before
        resolving it is the classic traversal bypass — the string looks fine and
        the real target is somewhere else entirely.
        """
        try:
            resolved = candidate.resolve()
        except OSError:
            return False
        return self.vault_root in resolved.parents

    def _resolve(self, provider_id: str) -> Path | None:
        """Vault-relative path -> absolute path, or None if it escapes the vault."""
        candidate = self.vault_root / provider_id
        if not self._is_inside_vault(candidate) or not candidate.is_file():
            return None
        return candidate

    # -- parsing ---------------------------------------------------------

    @staticmethod
    def _split_frontmatter(text: str) -> tuple[dict, str]:
        """Frontmatter is positional: '---' counts only as the very first line."""
        if not text.startswith("---\n"):
            return {}, text

        end = text.find("\n---", 3)
        if end == -1:
            return {}, text

        try:
            parsed = yaml.safe_load(text[4:end])
        except yaml.YAMLError:
            return {}, text  # Malformed YAML is not a reason to lose the note.

        body = text[end + 4 :].lstrip("\n")
        return (parsed if isinstance(parsed, dict) else {}), body

    @staticmethod
    def _strip_code(text: str) -> str:
        return INLINE_CODE_RE.sub(" ", FENCE_RE.sub(" ", text))

    @classmethod
    def _extract_tags(cls, frontmatter: dict, body: str) -> list[str]:
        """Frontmatter `tags:` and inline `#tag` are one namespace in Obsidian."""
        tags: set[str] = set()

        raw = frontmatter.get("tags") or frontmatter.get("tag")
        if isinstance(raw, str):
            tags.update(t.strip().lstrip("#") for t in raw.split(",") if t.strip())
        elif isinstance(raw, list):
            tags.update(str(t).strip().lstrip("#") for t in raw if str(t).strip())

        tags.update(INLINE_TAG_RE.findall(cls._strip_code(body)))
        return sorted(tags)

    @classmethod
    def _excerpt(cls, body: str) -> str:
        """Short preview for lists.

        Deliberately truncated. Caching whole note bodies would make this app a
        second source of truth for the vault's contents (Plan.md §2) — the excerpt
        is derived for display, not a copy of the note.
        """
        text = cls._strip_code(body)
        text = WIKILINK_RE.sub(lambda m: m.group(1).split("|")[-1], text)

        lines = [
            stripped
            for line in text.splitlines()
            if (stripped := MD_SYNTAX_RE.sub("", line).strip())
        ]
        joined = " ".join(lines)
        return joined[:EXCERPT_CHARS].rstrip() + "…" if len(joined) > EXCERPT_CHARS else joined

    def _read(self, path: Path, *, with_body: bool) -> ProviderNote:
        # errors='replace' so one badly-encoded file cannot break the whole listing.
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, body = self._split_frontmatter(text)

        return ProviderNote(
            provider_id=str(path.relative_to(self.vault_root)),
            # Obsidian uses the filename as the title, not the first heading.
            title=path.stem,
            excerpt=self._excerpt(body),
            modified_at=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
            tags=self._extract_tags(frontmatter, body),
            body=body if with_body else None,
        )

    # -- NoteService -----------------------------------------------------

    def list_notes(self, limit: int = 200) -> list[ProviderNote]:
        """Every note, newest first.

        Reads the whole vault on every call — O(n) file reads, no caching. Fine at
        a few hundred notes on an SSD, and wrong at ten thousand. Step 9 adds the
        index that fixes it; doing it now would mean building cache invalidation
        before there is anything to invalidate.
        """
        paths = [
            p
            for p in self.vault_root.rglob("*.md")
            if not SKIP_DIRS.intersection(p.parts) and p.is_file()
        ]
        paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        return [self._read(p, with_body=False) for p in paths[:limit]]

    def get_note(self, provider_id: str) -> ProviderNote | None:
        path = self._resolve(provider_id)
        return self._read(path, with_body=True) if path else None

    # -- Phase 2b --------------------------------------------------------

    def create_note(self, title: str, body: str) -> ProviderNote:
        raise NoteWritesNotSupported(
            "Vault writes are disabled until an undo mechanism exists (ADR-005)."
        )

    def update_note(self, provider_id: str, body: str) -> ProviderNote:
        raise NoteWritesNotSupported(
            "Vault writes are disabled until an undo mechanism exists (ADR-005)."
        )
