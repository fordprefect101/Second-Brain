-- Personal OS — canonical schema
--
-- Five tables, per docs/architecture/phase-0.md §3. The governing rule (Plan.md §2):
-- each external service owns its data. Only ONE table here is canonical.
--
-- The drop test — if this table vanished, what is permanently lost?
--
--   capture_items   REAL DATA.        Needs backup.
--   connections     config, retypable. No.
--   entity_map      invented ids.      Yes — see ADR-004.
--   sync_state      one full resync.   No.
--   search_index    one reindex.       No.
--
-- This file is applied by api/database.py::ensure_schema() and must be safe to run
-- repeatedly. Every statement is IF NOT EXISTS.

-- ---------------------------------------------------------------------------
-- 1. capture_items — THE ONLY CANONICAL TABLE
-- ---------------------------------------------------------------------------
-- A raw capture is a thought with no home yet. No external service owns
-- "an unprocessed idea", so the Personal OS legitimately owns it.
--
-- After routing (Phase 2b), the row stops being the data and becomes the record
-- of an event: "on this date I captured this text and it became that note."
-- It is a SNAPSHOT OF A PAST EVENT, not a cache of current state — so it is never
-- updated when the resulting note changes, and it has no staleness obligation.

create table if not exists capture_items (
    id          uuid        primary key default gen_random_uuid(),
    body        text        not null check (length(trim(body)) > 0),

    -- Manual classification only. Plan.md §11: no AI classification initially.
    kind        text        not null
                check (kind in ('note', 'idea', 'task', 'resource', 'reminder')),

    status      text        not null default 'inbox'
                check (status in ('inbox', 'routed', 'archived')),

    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),

    -- Routing destination. Deliberately NOT a foreign key to entity_map:
    -- capture_items is canonical, entity_map is rebuildable (ADR-002). A FK here
    -- would point from durable data to disposable data — CASCADE would delete real
    -- captures on a rebuild, RESTRICT would block the rebuild entirely.
    routed_at            timestamptz,
    routed_to_entity_id  uuid,

    -- Human-readable snapshot taken at routing time, e.g.
    -- 'obsidian:Ideas/AI guitar teacher.md'. Survives both an entity_map rebuild
    -- and deletion of the note itself, so provenance is never fully lost.
    routed_to_ref        text,

    -- A row is routed exactly when it has a routing timestamp.
    constraint capture_routed_consistent
        check ((status = 'routed') = (routed_at is not null))
);

-- Inbox is the hot path: unrouted items, newest first.
create index if not exists capture_items_inbox_idx
    on capture_items (created_at desc)
    where status = 'inbox';

-- ---------------------------------------------------------------------------
-- 2. connections — application configuration
-- ---------------------------------------------------------------------------
-- Which integrations are enabled and how they are configured. Nobody else owns
-- your configuration of your own tool.
--
-- CREDENTIALS ARE NEVER STORED HERE. OAuth tokens go in the macOS Keychain
-- (Plan.md §22). `config` holds things like a vault path — never a secret.

create table if not exists connections (
    provider     text        primary key,      -- 'obsidian', 'google_tasks', ...
    status       text        not null default 'disconnected'
                 check (status in ('disconnected', 'connected', 'error')),

    config       jsonb       not null default '{}'::jsonb,
    scopes       text[]      not null default '{}',   -- least-privilege audit trail

    last_error   text,
    connected_at timestamptz,
    updated_at   timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- 3. entity_map — invented identity (ADR-004)
-- ---------------------------------------------------------------------------
-- Maps a stable internal id to a provider's native identifier.
--
-- Mostly derived, but `id` is ASSIGNED, not computed — it exists nowhere in the
-- outside world. Rebuild the table and every id differs, so anything pointing at
-- one dangles. You can recompute a hash; you cannot recompute a serial number.
-- That is why this one is in the backup column despite looking like cache.

create table if not exists entity_map (
    id            uuid        primary key default gen_random_uuid(),
    provider      text        not null,
    provider_id   text        not null,   -- vault-relative path, Google task id, ...
    entity_type   text        not null,   -- 'note', 'task', 'event', 'file', ...

    first_seen_at timestamptz not null default now(),
    last_seen_at  timestamptz not null default now(),

    unique (provider, provider_id)
);

create index if not exists entity_map_provider_type_idx
    on entity_map (provider, entity_type);

-- ---------------------------------------------------------------------------
-- 4. sync_state — operational bookkeeping
-- ---------------------------------------------------------------------------
-- Per-provider cursors and etags. Pure mechanism; losing it costs one full resync.

create table if not exists sync_state (
    provider               text primary key,
    cursor                 text,          -- opaque, provider-defined
    etag                   text,
    last_sync_started_at   timestamptz,
    last_sync_completed_at timestamptz,
    last_error             text
);

-- ---------------------------------------------------------------------------
-- 5b. note_snapshots — CANONICAL (added in Phase 2b)
-- ---------------------------------------------------------------------------
-- The undo path ADR-005 requires before any vault write is allowed.
--
-- This holds note content, which looks like it violates Plan.md §2. It does not,
-- for the same reason a routed capture_items row does not: this is a SNAPSHOT OF A
-- PAST STATE, not a cache of current state. It records what a file contained before
-- we modified it. It is never read as "the note", never refreshed, and has no
-- staleness obligation — the file remains canonical for what the note IS.
--
-- Drop test: losing this loses undo history. Real, though lower stakes than
-- capture_items. It is the third table that does not survive a rebuild for free.
--
-- No FK to entity_map: this table is canonical and entity_map is rebuildable, so a
-- FK would point from durable data to disposable data (the ADR-004 lesson).
-- provider_id is denormalised for the same reason — undo must still work after the
-- database is rebuilt and every internal id has changed.

create table if not exists note_snapshots (
    id           uuid        primary key default gen_random_uuid(),
    entity_id    uuid        not null,
    provider     text        not null,
    provider_id  text        not null,

    -- NULL means the note did not exist before this operation, so undoing a
    -- 'create' means deleting the file rather than restoring content.
    content      text,
    operation    text        not null check (operation in ('create', 'update')),

    taken_at     timestamptz not null default now(),
    undone_at    timestamptz
);

create index if not exists note_snapshots_entity_idx
    on note_snapshots (entity_id, taken_at desc);

-- ---------------------------------------------------------------------------
-- 5. search_index — derived, disposable
-- ---------------------------------------------------------------------------
-- THE LINE THAT MUST NOT BE CROSSED: cache what the INDEX needs, not what the
-- READER needs. Title, tags, and a short excerpt for result previews. Never the
-- full body.
--
-- The reason is correctness, not disk. A cached body goes stale the moment the
-- note is edited, and a second copy that can diverge makes the Personal OS a
-- second source of truth — exactly what Plan.md §2 forbids. The excerpt length
-- check below enforces that architectural rule in the schema itself.
--
-- `document` is built from the FULL body, which does not cross that line: it
-- holds stemmed lexemes and positions, not text anyone could read back. It can
-- find a note; it cannot replace one. The line is about storing a readable copy.
--
-- FK to entity_map WITH cascade is correct here: both tables are cache, so they
-- are rebuilt together. Contrast capture_items above, which has no FK precisely
-- because it is canonical.

create table if not exists search_index (
    entity_id          uuid        primary key
                       references entity_map(id) on delete cascade,
    provider           text        not null,     -- denormalised: results must
                                                 -- name their source (Plan.md §12)
    title              text        not null,
    excerpt            text        check (excerpt is null or length(excerpt) <= 500),
    tags               text[]      not null default '{}',

    -- Incremental indexing (step 9): detect what actually changed rather than
    -- reindexing the world. mtime is the cheap check, hash is the honest one.
    source_modified_at timestamptz,
    content_hash       text,
    indexed_at         timestamptz not null default now(),

    -- Populated at step 9, after step 8 covers to_tsvector/tsquery by hand.
    -- Left as a plain column rather than GENERATED ALWAYS so the tsvector
    -- expression is written explicitly first; revisit once FTS is understood.
    document           tsvector
);

create index if not exists search_index_document_idx
    on search_index using gin (document);

create index if not exists search_index_provider_idx
    on search_index (provider);
