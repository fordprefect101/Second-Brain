"""Unified search across every source.

Two pieces:

  index_all()  — read the sources, write rows into search_index
  search()     — query that index

The index is pure cache. Dropping search_index costs one reindex and nothing else
(ADR-002's drop test). It stores titles, tags, and a short excerpt — never note
bodies, because a cached body goes stale the moment the file is edited and a second
copy that can diverge makes this a second source of truth (Plan.md §2). The excerpt
length CHECK in schema.sql enforces that.

Keyword search only, deliberately. Semantic search arrives in the AI layer, once
there are real failed searches to justify it (ADR-007).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

import psycopg

from api.entities import resolve_ids
from api.services import ActivityService, CalendarService, NoteService, TaskService

EXCERPT_LIMIT = 500  # matches the CHECK constraint in schema.sql

# Bump when the indexers change what they PUT in the index — different excerpt
# composition, different tags, different weighting.
#
# Without this, changing indexing logic silently does nothing to existing rows:
# the fingerprint tracks whether the SOURCE changed, and the source has not. That
# is a genuinely confusing failure — the code is right, the index is stale, and
# nothing says so. Folding the version into every fingerprint makes an indexer
# change invalidate the cache automatically.
INDEXER_VERSION = 4


@dataclass
class SearchHit:
    id: UUID
    title: str
    excerpt: str
    source: str
    rank: float
    # What this timestamp MEANS depends on the source, and callers have to know:
    # for a calendar event it is when the event starts, for a task its due date,
    # for a note when the file was last edited, for a repo its last push.
    #
    # Carried anyway, because a result with no date is close to useless — an event
    # called "Practice 2" tells you nothing without a when.
    modified_at: datetime | None


def _hash(text: str) -> str:
    """Content fingerprint, for detecting real changes.

    mtime is the cheap check and it lies: two edits in the same second look
    identical, and copy/sync tools rewrite timestamps unpredictably. The hash is
    the honest answer. Using both is why search_index has both columns.
    """
    return hashlib.sha256(f"v{INDEXER_VERSION}\n{text}".encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------


@dataclass
class Indexable:
    """One thing to put in the index, in the shape search_index needs.

    Every source flattens to this before indexing: a note, a capture, a calendar
    event, a task, a repository. Having one shape means one indexing path — the
    incremental skip, the deletion sweep, and the excerpt truncation are written
    once rather than copied per source and drifting apart.
    """

    provider_id: str
    title: str
    excerpt: str
    tags: list[str]
    modified_at: datetime
    # What changing means for THIS source. A note changes when its text changes; a
    # task changes when it is completed; a repo changes when it is pushed to. Each
    # source decides, because only it knows what "different" means.
    fingerprint: str


def index_source(
    conn: psycopg.Connection,
    provider: str,
    entity_type: str,
    items: list[Indexable],
    *,
    prune: bool = True,
) -> dict[str, int]:
    """Index a batch of items from one provider.

    `prune` removes indexed rows the source no longer returns. That is correct for
    a complete listing (every note in the vault) and WRONG for a windowed one — a
    calendar query covering the next 90 days must not delete last month's events
    just because they fell outside the window.
    """
    stats = {"seen": len(items), "indexed": 0, "skipped": 0, "removed": 0}

    if not items:
        # An empty listing is ambiguous: the source may genuinely be empty, or it
        # may have failed upstream. Pruning here would wipe a good index on a
        # transient error, so it is skipped.
        return stats

    ids = resolve_ids(
        conn,
        provider=provider,
        entity_type=entity_type,
        provider_ids=[i.provider_id for i in items],
    )

    with conn.cursor() as cur:
        cur.execute(
            "select entity_id, content_hash from search_index where provider = %s",
            (provider,),
        )
        known = {row["entity_id"]: row["content_hash"] for row in cur.fetchall()}

    for item in items:
        entity_id = ids[item.provider_id]

        if known.get(entity_id) == item.fingerprint:
            stats["skipped"] += 1
            continue

        _upsert(
            conn,
            entity_id=entity_id,
            provider=provider,
            title=item.title[:200],
            excerpt=item.excerpt[:EXCERPT_LIMIT],
            tags=item.tags,
            modified_at=item.modified_at,
            content_hash=item.fingerprint,
        )
        stats["indexed"] += 1

    if prune:
        # Nothing reports a deletion, so it is inferred: anything indexed for this
        # provider that the listing did not return no longer exists.
        with conn.cursor() as cur:
            cur.execute(
                "delete from search_index where provider = %s "
                "and not (entity_id = any(%s))",
                (provider, list(set(ids.values()))),
            )
            stats["removed"] = cur.rowcount

    conn.commit()
    _record_sync(conn, provider)
    return stats


def hours_since_last_sync(conn: psycopg.Connection) -> float | None:
    """How long since the index was last rebuilt. None if it never has been.

    max(), not min(): a reindex runs every source in one pass, so the newest stamp
    answers "when did we last reindex". Taking the oldest would instead report on
    whichever provider is disconnected — GitHub without a token never stamps at
    all, which would make the index look permanently stale.
    """
    with conn.cursor() as cur:
        cur.execute("select max(last_sync_completed_at) as last from sync_state")
        row = cur.fetchone()

    last = row["last"] if row else None
    if last is None:
        return None

    return (datetime.now(timezone.utc) - last).total_seconds() / 3600


def _record_sync(conn: psycopg.Connection, provider: str) -> None:
    """Stamp when this provider last synced.

    sync_state has been an empty table since step 2, reserved for exactly this.
    Only the timestamp is used today; cursors and etags go here when incremental
    sync replaces full refetching.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into sync_state (provider, last_sync_completed_at)
            values (%s, now())
            on conflict (provider) do update
                set last_sync_completed_at = now(), last_error = null
            """,
            (provider,),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Per-source adapters — each decides what "changed" means
# ---------------------------------------------------------------------------


def index_notes(conn: psycopg.Connection, service: NoteService) -> dict[str, int]:
    """Notes from a knowledge provider."""
    notes = service.list_notes(limit=5000)
    return index_source(
        conn,
        service.source_id,
        "note",
        [
            Indexable(
                provider_id=n.provider_id,
                title=n.title,
                excerpt=n.excerpt,
                tags=n.tags,
                modified_at=n.modified_at,
                # Title and tags are in the fingerprint: renaming or retagging
                # changes what should be searchable even if the body is untouched.
                fingerprint=_hash(f"{n.title}\n{n.excerpt}\n{','.join(n.tags)}"),
            )
            for n in notes
        ],
    )


def index_captures(conn: psycopg.Connection) -> dict[str, int]:
    """Captures, so search covers them alongside everything else.

    These live in Postgres already, so this is table-to-table rather than a
    provider read. They still go through entity_map — results must be able to name
    their source (Plan.md §12), and that needs uniform identity.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select id, body, kind, created_at from capture_items "
            "where status <> 'archived'"
        )
        rows = cur.fetchall()

    return index_source(
        conn,
        "personal_os",
        "capture",
        [
            Indexable(
                provider_id=str(r["id"]),
                # Captures have no title. The first line is the closest honest thing.
                title=r["body"].splitlines()[0][:80] if r["body"] else "(empty)",
                excerpt=r["body"],
                tags=[r["kind"]],
                modified_at=r["created_at"],
                fingerprint=_hash(f"{r['body']}\n{r['kind']}"),
            )
            for r in rows
        ],
    )


def index_events(
    conn: psycopg.Connection,
    service: CalendarService,
    *,
    days_back: int = 7,
    days_forward: int = 30,
) -> dict[str, int]:
    """Calendar events in a rolling window.

    Deliberately windowed, and narrowly. Indexing every event you have ever had
    makes search worse, not better: a subscribed sports calendar produced 164 of
    212 total index rows on the first run, so searching "formula" returned five
    near-identical race sessions and buried the notes and repos.

    Calendar search answers "what is coming up", not "what happened in March".
    Seven days back and thirty forward is that question's actual span.

    prune=False follows from that: rows outside the current window are stale, not
    deleted, and wiping them each run would mean re-fetching them constantly.
    Old events age out when the index is rebuilt from scratch.
    """
    now = datetime.now(timezone.utc)
    events = service.list_events(
        now - timedelta(days=days_back), now + timedelta(days=days_forward)
    )

    return index_source(
        conn,
        service.source_id,
        "event",
        [
            Indexable(
                provider_id=e.provider_id,
                title=e.title,
                # Location and calendar are worth searching: "which calendar was
                # that race on" and "what was at the Barcelona circuit" both work.
                excerpt=" · ".join(
                    part for part in [e.location, e.calendar_name, e.description] if part
                ),
                tags=[t for t in [e.calendar_name] if t],
                modified_at=e.start if e.start.tzinfo else e.start.replace(tzinfo=timezone.utc),
                fingerprint=_hash(f"{e.title}\n{e.start}\n{e.location or ''}"),
            )
            for e in events
        ],
        prune=False,
    )


def index_tasks(conn: psycopg.Connection, service: TaskService) -> dict[str, int]:
    """Open tasks.

    Only incomplete ones are listed, so completing a task removes it from the
    index on the next run — which is what prune is for and why it stays on here.
    """
    tasks = service.list_tasks(include_completed=False)

    return index_source(
        conn,
        service.source_id,
        "task",
        [
            Indexable(
                provider_id=t.provider_id,
                title=t.title,
                # The list is prefixed onto the excerpt as well as tagged, because
                # excerpt is what reaches a consumer through SearchHit. A reading
                # list item and a work item are otherwise indistinguishable once
                # they are rows — which is how "what have we done?" once got
                # answered with a to-do list.
                excerpt=f"[{t.list_name}] {t.notes or ''}".strip()
                if t.list_name
                else (t.notes or ""),
                # The list name is deliberately NOT a tag.
                #
                # It was, briefly, and it broke ranking badly. Tags are weight A —
                # the same as a title — and these lists are named after projects
                # ("Resume Builder", "AudioTour"). Every task in a list therefore
                # scored as though its title were the list name, and searching
                # "resume builder" returned seven to-do items at rank 1.000 while
                # the actual note fell to eighth and the repo off the page.
                #
                # It lives in the excerpt instead (weight B, above), which still
                # matches "To Read" without letting a list name outrank the thing
                # the list is about.
                tags=["task"],
                modified_at=t.due or datetime.now(timezone.utc),
                # Completion is part of the fingerprint so a status change
                # reindexes even when the title is identical.
                fingerprint=_hash(
                    f"{t.title}\n{t.notes or ''}\n{t.completed}\n{t.list_name or ''}"
                ),
            )
            for t in tasks
        ],
    )


def index_repositories(
    conn: psycopg.Connection, service: ActivityService
) -> dict[str, int]:
    """Repositories, so a project's code is findable next to notes about it."""
    repos = service.list_repositories(limit=100)

    return index_source(
        conn,
        service.source_id,
        "repository",
        [
            Indexable(
                provider_id=r.provider_id,
                title=r.name,
                # provider_id is 'owner/name'. Two collaborators can each have a
                # repo called AudioTourApp, and the bare name makes them look like
                # a duplicate — so the owner goes in the excerpt and the tags.
                excerpt=" · ".join(
                    part
                    for part in [
                        r.provider_id.split("/")[0],
                        r.description,
                        r.language,
                    ]
                    if part
                ),
                tags=[
                    t
                    for t in [
                        r.provider_id.split("/")[0],
                        r.language,
                        "private" if r.private else None,
                    ]
                    if t
                ],
                modified_at=r.pushed_at,
                # pushed_at is the fingerprint: a repo "changes" when work lands in
                # it, which is the signal worth reindexing on.
                fingerprint=_hash(f"{r.name}\n{r.description or ''}\n{r.pushed_at}"),
            )
            for r in repos
        ],
    )



def _upsert(
    conn: psycopg.Connection,
    *,
    entity_id: UUID,
    provider: str,
    title: str,
    excerpt: str,
    tags: list[str],
    modified_at: datetime,
    content_hash: str,
) -> None:
    """Write one row, building the tsvector in SQL.

    Weighting matters more than it looks. setweight marks title terms 'A' and body
    terms 'B', and ts_rank scores A higher — so a note *called* "Audiotour" beats one
    that merely mentions it. Without this, title matches and passing mentions rank
    identically, which is almost never what you want.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into search_index
                (entity_id, provider, title, excerpt, tags,
                 source_modified_at, content_hash, indexed_at, document)
            values (%(id)s, %(provider)s, %(title)s, %(excerpt)s, %(tags)s,
                    %(modified)s, %(hash)s, now(),
                    setweight(to_tsvector('english', %(title)s), 'A') ||
                    setweight(to_tsvector('english', %(tagtext)s), 'A') ||
                    setweight(to_tsvector('english', %(excerpt)s), 'B'))
            on conflict (entity_id) do update set
                provider           = excluded.provider,
                title              = excluded.title,
                excerpt            = excluded.excerpt,
                tags               = excluded.tags,
                source_modified_at = excluded.source_modified_at,
                content_hash       = excluded.content_hash,
                indexed_at         = excluded.indexed_at,
                document           = excluded.document
            """,
            {
                "id": entity_id,
                "provider": provider,
                "title": title,
                "excerpt": excerpt,
                "tags": tags,
                "tagtext": " ".join(tags),
                "modified": modified_at,
                "hash": content_hash,
            },
        )


# ---------------------------------------------------------------------------
# Querying
# ---------------------------------------------------------------------------


def search(
    conn: psycopg.Connection,
    query: str,
    *,
    limit: int = 20,
    source: str | None = None,
    match: Literal["all", "any"] = "all",
) -> list[SearchHit]:
    """Keyword search. Two modes, because the two callers want different things.

    match="all" — the search box. Someone typing "postgres docker" wants notes
    about both, so every word must match. websearch_to_tsquery parses what people
    actually type — quoted phrases, OR, and -exclusions — instead of demanding
    tsquery's & and | syntax. It does not do prefix matching, so the last word is
    additionally matched as a prefix: typing "transcrip" finds "transcription"
    while you are still typing. The two queries are OR'd, so a full match still
    wins on rank.

    match="any" — the assistant, which sends a whole question. A question never
    has all its words in one note, so every-word matching found nothing and only
    the last-word prefix ever matched: the first eval run scored 0 of 9. Here a
    note matches if it contains ANY of the words, and ts_rank scores a note higher
    the more of them it contains. No prefix: the question is finished, and a
    finished word matched as a prefix is noise ("app" found "apply").
    """
    text = query.strip()
    if not text:
        return []

    if match == "any":
        prefix = ""
    else:
        last_word = text.split()[-1]
        # Strip characters that would be interpreted as tsquery operators.
        prefix = "".join(c for c in last_word if c.isalnum() or c == "_")

    with conn.cursor() as cur:
        cur.execute(
            """
            with q as (
                select case
                         -- plainto_tsquery stems and drops stop words, and joins
                         -- the survivors with ' & '. Swapping that for ' | ' is
                         -- the whole of match="any". An all-stop-word question
                         -- becomes an empty query, which matches nothing.
                         when %(match)s = 'any'
                           then replace(plainto_tsquery('english', %(text)s)::text,
                                        ' & ', ' | ')::tsquery
                         else websearch_to_tsquery('english', %(text)s)
                       end as words,
                       case when %(prefix)s = '' then null
                            else to_tsquery('english', %(prefix)s || ':*')
                       end as prefix
            )
            select s.entity_id, s.title, s.excerpt, s.provider, s.source_modified_at,
                   -- Score against whichever query actually matched, not just the
                   -- words one: a prefix-only hit would otherwise rank 0.0 and sort
                   -- below everything. greatest() also means a full-word match
                   -- still outranks a prefix match on the same row.
                   greatest(
                     case when q.words  is null then 0
                          else ts_rank(s.document, q.words) end,
                     case when q.prefix is null then 0
                          else ts_rank(s.document, q.prefix) end
                   ) as rank
              from search_index s, q
             -- The OR group MUST be parenthesised. AND binds tighter than OR, so
             -- without these brackets the source filter would apply only to the
             -- prefix branch and word matches would ignore it entirely.
             where (
                     (q.words is not null and s.document @@ q.words)
                  or (q.prefix is not null and s.document @@ q.prefix)
                   )
               -- ::text because Postgres cannot infer a NULL parameter's type.
               and (%(source)s::text is null or s.provider = %(source)s::text)
             order by rank desc, s.source_modified_at desc nulls last
             limit %(limit)s
            """,
            {
                "text": text,
                "prefix": prefix,
                "match": match,
                "source": source,
                "limit": limit,
            },
        )
        rows = cur.fetchall()

    return [
        SearchHit(
            id=row["entity_id"],
            title=row["title"],
            excerpt=row["excerpt"] or "",
            source=row["provider"],
            rank=float(row["rank"]),
            modified_at=row["source_modified_at"],
        )
        for row in rows
    ]
