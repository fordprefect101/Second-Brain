"""Writing notes into the vault through the API, with undo.

For notes that arrive from outside — another project's repo asking this machine to
file an ADR or a plan (docs/m1-setup.md: only the M1 edits the vault). Same sequence
as api/routing.py and for the same reason:

    1. snapshot what is there now
    2. write atomically
    3. record, then index

The API owns the frontmatter. A caller sends a type, a project and topic tags; it
never sends YAML. That is what keeps every note written this way findable the same
way, and it is why a body that brings its own frontmatter is refused.

A note whose [[links]] would break the shape of the note graph is refused too
(api/link_rules.py), with the fix in the message.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

import psycopg

from api import embeddings, link_rules, tags as tag_rules
from api.entities import lookup_provider_id, resolve_ids
from api.providers.obsidian import ObsidianVaultProvider
from api.search import index_notes
from api.services import ProviderNote
from api.vectors import embed_pending


class NoteWriteError(RuntimeError):
    """The note was refused. Nothing was written."""


class TagNeedsConfirmation(NoteWriteError):
    """A tag that is not in the vault yet. Carries the nearest existing tag, if any."""

    def __init__(
        self, tag: str, similar: tag_rules.SimilarTag | None, checked_meaning: bool
    ):
        self.tag = tag
        self.similar = similar
        self.checked_meaning = checked_meaning
        if similar:
            message = (
                f"'{tag}' is not a tag yet and is close to the existing tag "
                f"'{similar.tag}' (by {similar.by}). Use '{similar.tag}', or send "
                "allowNewTags to add it anyway."
            )
        else:
            message = f"'{tag}' is not a tag yet. Send allowNewTags to add it."
        if not checked_meaning:
            message += (
                " The embedding model was unreachable, so only spelling was compared."
            )
        super().__init__(message)


@dataclass
class WrittenNote:
    entity_id: UUID
    provider_id: str
    tags: list[str]
    new_tags: list[str] = field(default_factory=list)


def vault_tags(provider: ObsidianVaultProvider) -> Counter[str]:
    """Every tag in the vault with how many notes carry it."""
    counts: Counter[str] = Counter()
    for note in provider.list_notes(limit=5000):
        counts.update(tag_rules.normalize(t) for t in note.tags)
    counts.pop("", None)
    return counts


def _compose(
    body: str,
    *,
    project: str | None,
    tags: list[str],
    status: str | None = None,
    repos: list[str] | None = None,
) -> str:
    # Written by hand, not yaml.dump, to match the notes already in the vault:
    # double-quoted wikilink, tags on one line. json.dumps gives a YAML-valid
    # double-quoted string; tags are already reduced to [a-z0-9/-] by normalize().
    lines = []
    if tags[0] == "project":
        # A project page is the project, so it carries the fields the others in
        # Projects/ do instead of a link to itself.
        lines.append("type: project")
        if status:
            lines.append(f"status: {json.dumps(status, ensure_ascii=False)}")
        if repos:
            lines.append("repos:")
            lines.extend(f"  - {json.dumps(r, ensure_ascii=False)}" for r in repos)
    elif project:
        lines.append(f"project: {json.dumps(f'[[{project}]]', ensure_ascii=False)}")
    lines.append(f"tags: [{', '.join(tags)}]")
    return "---\n" + "\n".join(lines) + f"\n---\n\n{body.strip()}\n"


def _frontmatter_block(raw: str) -> str:
    """The frontmatter exactly as written, or '' — kept verbatim across an update."""
    if not raw.startswith("---\n"):
        return ""
    end = raw.find("\n---", 3)
    return raw[: end + 4] + "\n\n" if end != -1 else ""


def _refuse_broken_links(
    provider: ObsidianVaultProvider, provider_id: str, title: str, body: str
) -> None:
    """Judge this note's links as if it were already saved. Other notes are not judged."""
    notes = [
        n
        for n in provider.list_notes(limit=10_000, with_body=True)
        if n.provider_id != provider_id
    ]
    notes.append(
        ProviderNote(
            provider_id=provider_id,
            title=title,
            excerpt="",
            modified_at=datetime.now().astimezone(),
            body=body,
        )
    )
    if broken := link_rules.problems(notes, only=provider_id):
        raise NoteWriteError(" ".join(broken))


def _reindex(conn: psycopg.Connection, provider: ObsidianVaultProvider) -> None:
    index_notes(conn, provider)
    conn.commit()
    try:
        embed_pending(conn)
    except embeddings.EmbeddingsUnavailable:
        # Keyword search already has the note. Its vector is filled in by the next
        # reindex that can reach the model.
        pass


def create_note(
    conn: psycopg.Connection,
    provider: ObsidianVaultProvider,
    *,
    title: str,
    body: str,
    note_type: str,
    project: str | None = None,
    topics: list[str] | None = None,
    folder: str = "",
    allow_new_tags: bool = False,
    status: str | None = None,
    repos: list[str] | None = None,
) -> WrittenNote:
    if note_type not in tag_rules.TYPE_TAGS:
        raise NoteWriteError(
            f"Unknown note type '{note_type}'. One of: {', '.join(tag_rules.TYPE_TAGS)}"
        )
    if body.lstrip().startswith("---"):
        raise NoteWriteError(
            "Send the note without frontmatter: type, project and tags are separate "
            "fields and the frontmatter is written from them."
        )
    if provider.note_exists(title, folder):
        raise NoteWriteError(
            f"A note named '{provider.safe_filename(title)}' already exists there. "
            "Update it instead, or choose another title."
        )
    filename = provider.safe_filename(title)
    _refuse_broken_links(
        provider, f"{folder}/{filename}.md" if folder else f"{filename}.md", filename, body
    )

    project_tag = tag_rules.slug(project) if project else None
    fixed = [note_type, *([project_tag] if project_tag else [])]

    wanted = list(dict.fromkeys(tag_rules.normalize(t) for t in topics or []))
    wanted = [t for t in wanted if t and t not in fixed]
    if clash := [t for t in wanted if t in tag_rules.TYPE_TAGS]:
        raise NoteWriteError(f"A note has one type; '{clash[0]}' cannot be a topic tag.")
    if len(wanted) > tag_rules.MAX_TOPIC_TAGS:
        raise NoteWriteError(
            f"At most {tag_rules.MAX_TOPIC_TAGS} topic tags; got {len(wanted)}."
        )

    existing = vault_tags(provider)
    new_tags = [t for t in wanted if t not in existing]
    if new_tags and not allow_new_tags:
        similar, checked = tag_rules.similar_existing(new_tags[0], list(existing))
        raise TagNeedsConfirmation(new_tags[0], similar, checked)

    all_tags = [*fixed, *wanted]
    content = _compose(
        body, project=project, tags=all_tags, status=status, repos=repos
    )

    # 1. Snapshot: a create has no prior content, so the row holds NULL and undo
    #    means taking the file back out.  2. Write.  3. Record.
    note = provider.create_note(title, content, folder=folder)
    entity_id = resolve_ids(
        conn,
        provider=provider.source_id,
        entity_type="note",
        provider_ids=[note.provider_id],
    )[note.provider_id]

    with conn.cursor() as cur:
        cur.execute(
            """
            insert into note_snapshots
                (entity_id, provider, provider_id, content, operation)
            values (%s, %s, %s, null, 'create')
            """,
            (entity_id, provider.source_id, note.provider_id),
        )
    conn.commit()
    _reindex(conn, provider)

    return WrittenNote(entity_id, note.provider_id, all_tags, new_tags)


def update_note(
    conn: psycopg.Connection,
    provider: ObsidianVaultProvider,
    entity_id: UUID,
    *,
    body: str,
    base_modified_at: datetime,
) -> WrittenNote:
    """Replace a note's text, keeping its frontmatter.

    base_modified_at is the modifiedAt the caller read. If the file has changed
    since — edited in Obsidian, or by another write — the update is refused rather
    than silently replacing text the caller never saw.
    """
    mapping = lookup_provider_id(conn, entity_id)
    if mapping is None or mapping[0] != provider.source_id:
        raise NoteWriteError("Unknown note id")
    provider_id = mapping[1]

    current = provider.get_note(provider_id)
    raw = provider.read_raw(provider_id)
    if current is None or raw is None:
        raise NoteWriteError("Note no longer exists in the vault")
    if abs((current.modified_at - base_modified_at).total_seconds()) > 0.001:
        raise NoteWriteError(
            "The note changed since it was read. Read it again and reapply the edit."
        )
    if body.lstrip().startswith("---"):
        raise NoteWriteError("Send the note text only; its frontmatter is kept as is.")
    _refuse_broken_links(provider, provider_id, current.title, body)

    with conn.cursor() as cur:
        cur.execute(
            """
            insert into note_snapshots
                (entity_id, provider, provider_id, content, operation)
            values (%s, %s, %s, %s, 'update')
            """,
            (entity_id, provider.source_id, provider_id, raw),
        )
    conn.commit()

    updated = provider.update_note(
        provider_id, f"{_frontmatter_block(raw)}{body.strip()}\n"
    )
    _reindex(conn, provider)
    return WrittenNote(entity_id, provider_id, updated.tags)


def undo_last_write(
    conn: psycopg.Connection, provider: ObsidianVaultProvider, entity_id: UUID
) -> str:
    """Reverse the most recent API write to a note. Returns what was done.

    A created note is moved to the vault's .trash/, never deleted: it may have been
    edited since, and a moved file can be dragged back. An updated note gets its
    previous contents back.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select 1 from capture_items where routed_to_entity_id = %s", (entity_id,)
        )
        if cur.fetchone():
            raise NoteWriteError(
                "This note came from a capture. Undo it from the inbox, which also "
                "returns the capture."
            )
        cur.execute(
            """
            select id, provider_id, content, operation from note_snapshots
             where entity_id = %s and undone_at is null
             order by taken_at desc limit 1
            """,
            (entity_id,),
        )
        snapshot = cur.fetchone()

    if snapshot is None:
        raise NoteWriteError("Nothing to undo for that note")

    provider_id = snapshot["provider_id"]
    if snapshot["operation"] == "create":
        trashed = provider.trash_note(provider_id)
        done = f"moved to {trashed}" if trashed else "already gone"
        with conn.cursor() as cur:
            # The note is out of the vault; a search result for it would 404.
            cur.execute("delete from search_index where entity_id = %s", (entity_id,))
    else:
        provider.update_note(provider_id, snapshot["content"] or "")
        done = "previous contents restored"

    with conn.cursor() as cur:
        cur.execute(
            "update note_snapshots set undone_at = now() where id = %s", (snapshot["id"],)
        )
    conn.commit()
    if snapshot["operation"] == "update":
        _reindex(conn, provider)
    return done
