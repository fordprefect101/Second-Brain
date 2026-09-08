"""Read an Obsidian vault, print structured data for each note.

Experiment, not production code. Its job is to make the traps visible before
step 7 turns this into ObsidianVaultProvider.

    uv pip install pyyaml
    .venv/bin/python docs/experiments/markdown-parsing/parse.py

Point it elsewhere with:

    .venv/bin/python docs/experiments/markdown-parsing/parse.py "/path/to/vault"
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_VAULT = Path(
    "/Users/cstech20/Desktop/Personal Projects/Obsidian Vault/Second Brain"
)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class Link:
    """A [[wikilink]] and what it points at."""

    target: str  # what was written, minus alias/heading/block
    resolved: Path | None  # None = the note does not exist (valid in Obsidian)
    is_embed: bool = False  # ![[...]] embeds content rather than linking


@dataclass
class Note:
    path: Path
    title: str
    frontmatter: dict = field(default_factory=dict)
    tags: set[str] = field(default_factory=set)
    links: list[Link] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Filesystem safety
# ---------------------------------------------------------------------------


def is_inside_vault(vault_root: Path, candidate: Path) -> bool:
    """Is `candidate` really inside the vault?

    resolve() FIRST, then compare. It collapses '..' and follows symlinks, so a
    note named '../../.ssh/id_rsa' or a symlink pointing outside is caught here.
    Checking before resolving is the classic bypass — the string looks fine and
    the real path is somewhere else entirely.
    """
    try:
        resolved = candidate.resolve()
        root = vault_root.resolve()
    except OSError:
        return False
    return resolved == root or root in resolved.parents


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Separate the YAML block from the body.

    TRAP: frontmatter is positional. '---' counts only as the very first line;
    anywhere else it is a horizontal rule. So this checks line 1 rather than
    regex-matching '---...---' somewhere in the file.
    """
    if not text.startswith("---\n"):
        return {}, text

    # Find the closing delimiter, starting after the opening one.
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text  # Unterminated: treat the whole thing as body.

    raw = text[4:end]
    body = text[end + 4 :].lstrip("\n")

    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError:
        return {}, text  # Malformed YAML is not a reason to lose the note.

    return (parsed if isinstance(parsed, dict) else {}), body


# Fenced blocks first (``` or ~~~), then inline `code`.
FENCE_RE = re.compile(r"^(```|~~~).*?^\1", re.MULTILINE | re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def strip_code(text: str) -> str:
    """Remove code so its contents are not mistaken for tags or links."""
    return INLINE_CODE_RE.sub(" ", FENCE_RE.sub(" ", text))


# A tag must follow whitespace or a line start. That single condition excludes
# URL fragments (https://x.com/page#section) — the '#' there follows a letter.
# Markdown headings are excluded too, since '# Heading' has a space after the #.
INLINE_TAG_RE = re.compile(r"(?:^|\s)#([A-Za-z][\w/-]*)")


def extract_tags(frontmatter: dict, body: str) -> set[str]:
    """Tags from both sources.

    Obsidian treats frontmatter `tags:` and inline `#tag` as ONE namespace, so
    they are merged. Frontmatter accepts either a list or a comma-separated
    string, because both appear in real vaults.
    """
    tags: set[str] = set()

    raw = frontmatter.get("tags") or frontmatter.get("tag")
    if isinstance(raw, str):
        tags.update(t.strip().lstrip("#") for t in raw.split(",") if t.strip())
    elif isinstance(raw, list):
        tags.update(str(t).strip().lstrip("#") for t in raw if str(t).strip())

    tags.update(INLINE_TAG_RE.findall(strip_code(body)))
    return tags


# Optional leading '!' marks an embed. Everything up to ']]' is the target.
WIKILINK_RE = re.compile(r"(!?)\[\[([^\]\n]+)\]\]")


def extract_links(body: str) -> list[tuple[str, bool]]:
    """Find [[wikilinks]], returning (target, is_embed).

    TRAP: the text inside the brackets is not just a filename. All of these
    point at the same note:

        [[Note]]  [[Note|alias]]  [[Note#Heading]]  [[Note#^block-id]]

    So the alias, heading, and block parts are stripped off. Order matters —
    split on '|' first, because an alias can itself contain a '#'.
    """
    links: list[tuple[str, bool]] = []

    for bang, raw in WIKILINK_RE.findall(strip_code(body)):
        target = raw.split("|", 1)[0]  # drop alias
        target = target.split("#", 1)[0]  # drop heading and block
        target = target.strip()
        if target:  # '[[#Heading]]' is a same-note link; no target
            links.append((target, bang == "!"))

    return links


# ---------------------------------------------------------------------------
# Link resolution
# ---------------------------------------------------------------------------


def build_index(vault: Path) -> dict[str, list[Path]]:
    """Map lowercased basename -> every file with that name.

    A list, not a single path, because duplicate names are the interesting case.
    """
    index: dict[str, list[Path]] = {}
    for path in vault.rglob("*.md"):
        if ".obsidian" in path.parts or ".trash" in path.parts:
            continue
        index.setdefault(path.stem.lower(), []).append(path)
    return index


def resolve_link(
    target: str, source: Path, vault: Path, index: dict[str, list[Path]]
) -> Path | None:
    """Work out which file a [[link]] means.

    TRAP: a link is not a path. Obsidian resolves in roughly this order:

      1. as a path relative to the vault root ('Folder/Note')
      2. by unique basename anywhere in the vault
      3. if several files share the name, the one nearest the linking note

    Returns None for links to notes that do not exist. That is NORMAL in
    Obsidian — the link shows greyed out and clicking it creates the file. An
    unresolved link is not an error.

    VERIFY THIS against your own vault rather than trusting it. Create two notes
    with the same name in different folders, link to them from each, and see
    what Obsidian actually does.
    """
    # 1. Explicit path from the vault root.
    if "/" in target or target.endswith(".md"):
        candidate = vault / (target if target.endswith(".md") else f"{target}.md")
        if candidate.exists() and is_inside_vault(vault, candidate):
            return candidate

    matches = index.get(target.lower(), [])

    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    # 3. Ambiguous. Prefer the match sharing the most folders with the source,
    #    then the shallowest path as a tiebreak.
    def proximity(path: Path) -> tuple[int, int]:
        shared = 0
        for a, b in zip(source.parts, path.parts):
            if a != b:
                break
            shared += 1
        return (-shared, len(path.parts))

    return min(matches, key=proximity)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def parse_note(path: Path, vault: Path, index: dict[str, list[Path]]) -> Note:
    # errors='replace' so one badly-encoded file cannot abort the whole scan.
    text = path.read_text(encoding="utf-8", errors="replace")
    frontmatter, body = split_frontmatter(text)

    links = [
        Link(target=target, resolved=resolve_link(target, path, vault, index), is_embed=embed)
        for target, embed in extract_links(body)
    ]

    return Note(
        path=path,
        # Obsidian uses the filename as the title, not the first heading.
        title=path.stem,
        frontmatter=frontmatter,
        tags=extract_tags(frontmatter, body),
        links=links,
    )


def parse_vault(vault: Path) -> list[Note]:
    index = build_index(vault)
    notes = [
        parse_note(path, vault, index)
        for path in sorted(vault.rglob("*.md"))
        if ".obsidian" not in path.parts and ".trash" not in path.parts
    ]
    return notes


def main() -> int:
    vault = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_VAULT

    if not (vault / ".obsidian").is_dir():
        print(f"No .obsidian/ in {vault}", file=sys.stderr)
        print("That folder is not a vault root. Check the path.", file=sys.stderr)
        return 1

    notes = parse_vault(vault)
    print(f"{vault}\n{len(notes)} notes\n")

    unresolved: list[tuple[str, str]] = []

    for note in notes:
        print(f"── {note.title}")
        print(f"   path        {note.path.relative_to(vault)}")
        print(f"   frontmatter {note.frontmatter or '—'}")
        print(f"   tags        {sorted(note.tags) or '—'}")

        if not note.links:
            print("   links       —")
        for link in note.links:
            kind = "embed" if link.is_embed else "link "
            if link.resolved:
                where = link.resolved.relative_to(vault)
            else:
                where = "UNRESOLVED (note does not exist)"
                unresolved.append((note.title, link.target))
            print(f"   {kind}       {link.target} -> {where}")
        print()

    # Backlinks are stored nowhere. They are derived by scanning every note for
    # links pointing at a target — which is exactly why an index gets built once
    # rather than answering "what links here?" per query.
    backlinks: dict[Path, list[str]] = {}
    for note in notes:
        for link in note.links:
            if link.resolved:
                backlinks.setdefault(link.resolved, []).append(note.title)

    if backlinks:
        print("── backlinks (derived, not stored)")
        for path, sources in sorted(backlinks.items()):
            print(f"   {path.relative_to(vault)} <- {', '.join(sorted(sources))}")
        print()

    if unresolved:
        print(f"── {len(unresolved)} unresolved link(s) — normal in Obsidian")
        for source, target in unresolved:
            print(f"   {source} -> [[{target}]]")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
