# Personal OS

A unified control layer over my existing digital tools — Obsidian, Google Calendar/Tasks,
Notion, Drive, Sheets, Gmail, GitHub, Spotify.

This is three things at once: a tool I intend to use daily, a serious engineering project,
and a vehicle for understanding the technologies it uses.

Design documents, architecture decision records, and the learning log are maintained
alongside the code but kept local rather than published. This README is the self-contained
summary.

## Core principle

**Each service stays the source of truth for what it owns.** The Personal OS does not
become the canonical database for everything. It provides unified navigation, search,
capture, dashboard, cross-service context, and actions.

The local database holds only five kinds of thing — app state, integration mappings,
sync state, cached metadata, and search indexes. Never a canonical copy of external data.

## Architecture

```text
React UI
   ↓  HTTP (domain vocabulary, never provider vocabulary)
FastAPI routes
   ↓
Service interfaces        NoteService, TaskService, CalendarService  (Python Protocol)
   ↓
Providers                 ObsidianVaultProvider, GoogleTasksProvider, ...
   ↓
Filesystem / REST API / MCP
```

The UI never learns that a note came from a file or that a task came from Google.

## Stack

| Layer | Choice | Rationale |
|---|---|---|
| API | FastAPI (Python 3.14) | Async, pydantic validation at the boundary where untrusted external API responses arrive |
| Database | Postgres 17 in Docker | Better full-text search than SQLite, and `pgvector` is one extension away when semantic search arrives — avoiding a migration at exactly the wrong moment. Nothing installed system-wide |
| DB access | psycopg 3, raw SQL, `.sql` schema files | No ORM: the SQL that matters most here (`tsvector`, GIN indexes, ranking) is precisely what an ORM hides |
| UI | React 19 + Vite + TypeScript | Known stack; the learning budget goes to integration and retrieval concepts instead |
| Integrations | Direct APIs inbound; MCP to *expose* the Personal OS | An MCP server exposes tools to an LLM client — it is not an integration bus for a Python backend. Direct APIs going in; one MCP server going out, so Claude can query the unified system |

**Schema — five tables, only one of them canonical.** The rule is the drop test: if a table
vanished, what is permanently lost?

| Table | Holds | Lose it and… |
|---|---|---|
| `capture_items` | raw captures, pre-classification | **real data is gone.** Nothing else has a copy |
| `connections` | which integrations are configured | retype the config |
| `entity_map` | internal id ↔ provider id | ids are *assigned*, not computed — rebuild changes them and references dangle |
| `sync_state` | cursors, etags | one full resync |
| `search_index` | title, tags, short excerpt, tsvector | one reindex |

`search_index` caches what the *index* needs, never what the *reader* needs — a cached note
body goes stale the moment the file is edited, and a second copy that can diverge makes this
a second source of truth. The excerpt column carries a length constraint so that rule cannot
be broken by accident.

## Local setup

Runs local-only, single user. Nothing is installed system-wide except the prerequisites.

### Prerequisites

| | Version | Notes |
|---|---|---|
| Docker Desktop | any current | Postgres runs in a container; nothing else needs it |
| Python | 3.14 | |
| [uv](https://docs.astral.sh/uv/) | any current | `brew install uv` |
| Node | 20+ | Not needed until the frontend exists (step 4) |

### First run

```bash
cd "Personal-OS system"

# 1. Configuration. .env is gitignored and must never be committed (§22).
cp .env.example .env
#    Edit .env and set POSTGRES_PASSWORD to anything local.
#    Then update the password inside DATABASE_URL to match.

# 2. Start Postgres (Docker Desktop must be running)
docker compose up -d
docker compose ps          # expect: personal-os-db, healthy

# 3. Python environment
uv venv --python 3.14
uv pip install -r api/requirements.txt

# 4. Apply the schema
.venv/bin/python api/database.py
```

Step 4 should print the Postgres version, `Schema applied.`, five tables marked `+`, and
`Re-applied cleanly — schema is idempotent.` It applies the schema twice on purpose —
idempotency is a claim, so it gets tested rather than asserted.

### Day to day

```bash
docker compose up -d       # start the database
docker compose stop        # stop it, keeping data
```

### Troubleshooting

**`DatabaseUnavailable: Cannot reach Postgres…`** — the container isn't running.
`docker compose up -d`. This error is deliberately distinct from a query failure, because
for a tool you open daily it's by far the most common one.

**Port 5433 already in use** — change `POSTGRES_PORT` in `.env`, and change the port inside
`DATABASE_URL` to match. Both must agree. (5433 rather than 5432 so this can't collide with
another Postgres already on the machine.)

**Resetting the database**

```bash
docker compose down -v && rm -rf pgdata/
docker compose up -d && .venv/bin/python api/database.py
```

> ⚠️ **This destroys your captures.** Four of the five tables are rebuildable cache, so
> resetting costs only a reindex — but `capture_items` is canonical, and nothing else holds
> a copy of it. Until capture routing exists (Phase 2b), a reset is permanent data loss.
> Export first if there is anything in the inbox you want.

## Status

**Phase 0 complete** — discovery done, architecture decided.

**Phase 1 in progress.** V1 is Capture + Today + Obsidian read-only; everything else is
explicitly deferred. Obsidian is read-only until an undo mechanism exists — a bug in new
filesystem code writing to a personal knowledge vault is the highest-regret failure mode
in this project.

| Step | | |
|---|---|---|
| 1 | Repo skeleton, docs tree, ADRs | done |
| 2 | Docker Postgres, five-table schema, `ensure_schema()` | done |
| 3 | FastAPI skeleton, health route, env config | next |
| 4 | React/Vite shell with mock data | |
| 5 | Capture end-to-end (raw `fetch`) → 5.5 TanStack Query comparison | |
| 6 | *Learn:* markdown-as-data, filesystem safety | |
| 7 | `NoteService` + `ObsidianVaultProvider` (list/read) | |
| 8 | *Learn:* Postgres full-text search | |
| 9 | Index + search endpoint + search UI | |
| 10 | Today view, review, tag V1 | |

Later phases: Google Calendar and Tasks behind provider-independent interfaces, then Notion,
Drive, Sheets, Gmail, GitHub, Spotify — one at a time. Unified search starts as keyword
search and only becomes semantic once the keyword baseline's limits are demonstrable. AI is
an additional layer; the system stays useful with all of it disabled.

## Repository layout

```text
api/
  database.py      connection handling + idempotent ensure_schema()
  db/schema.sql    the five tables
  requirements.txt
web/               React + Vite frontend (from step 4)
evals/             retrieval and agent evaluation harness (later phases)
docs/              design notes, ADRs, learning log — maintained locally, not published
```
