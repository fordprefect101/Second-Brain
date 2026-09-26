"""Answering questions from the index, with a local model.

The shape, and why it is this shape:

    question
       -> search()                    deterministic
       -> fetch bodies of top notes   deterministic
       -> assemble context            deterministic
       -> ONE model call              the model only has to write prose
       -> answer + sources

Retrieval happens **before** the model sees anything. The obvious alternative —
give the model a search tool and let it decide when to call it — puts the least
reliable component in charge of the step everything else depends on. A local 8B
model demonstrably fails that step: the first run of the tool-calling experiment
emitted a tool call as plain text and invented a parameter that did not exist.

So the happy path asks the model for one thing only: read this context, answer
this question. It cannot fail to call a tool it was never asked to call.

get_note is still offered, as an escape hatch for when the assembled context is
not enough. If the model fumbles that call, the answer is merely worse rather
than broken, because the pre-assembled context is already in front of it.

No framework (ADR-010). Ollama's /api/chat is plain HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import httpx
import psycopg

from api.entities import lookup_provider_id
from api.search import SearchHit, search
from api.services import NoteService

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "llama3.1:8b"

# Ollama serves a much smaller window than the model supports unless told
# otherwise, and it truncates silently — from the FRONT, which is where the
# system prompt lives. Retrieved documents would push the rules out of context
# and the first thing lost would be "an open task is not a completed one".
#
# 8192 holds two note bodies plus the system prompt without making a CPU-bound
# model crawl.
NUM_CTX = 8192

# How many index rows go into the prompt.
SEARCH_LIMIT = 5

# How many of those get their full body fetched. Only notes have a body worth
# fetching — a task or calendar event is already complete in its index row.
# Two, because a third note buys less relevance than it costs in context.
FULL_TEXT_LIMIT = 2

# A model that keeps re-calling the same tool would otherwise run forever.
MAX_TURNS = 4

REQUEST_TIMEOUT = 180.0


class AssistantUnavailable(RuntimeError):
    """The model is not reachable.

    Distinct from a bad answer, and deliberately so: "Ollama is not running" is a
    thing the user can fix in five seconds, and it must not surface as a generic
    failure — the same reasoning as DatabaseUnavailable.
    """


# What each source's rows are, in words the model can act on.
#
# Kept from the tool-calling experiment, where its absence caused a real failure:
# rendering every row identically let the model read the user's open to-do list as
# a list of accomplishments. index_tasks() only indexes incomplete tasks, so every
# task here is unfinished by construction — a fact that lived in the indexer and
# never reached the model.
KIND_LABELS = {
    "obsidian": "NOTE",
    "personal_os": "CAPTURED IDEA",
    "google_calendar": "CALENDAR EVENT",
    "google_tasks": "OPEN TASK — planned, NOT yet done",
    "github": "REPOSITORY",
}

SOURCE_CAVEATS = {
    "github": "(repository metadata only — no commit history is indexed)",
}

SYSTEM_PROMPT = """You answer questions about the user's own notes, tasks, \
calendar, and repositories.

You are given search results from their personal system. Rules:
- Answer only from those results. Do not add knowledge of your own.
- An OPEN TASK is something the user PLANS to do and has NOT done. Never report \
an open task as completed work.
- A REPOSITORY result proves the repo exists. It says nothing about what was done \
in it — commit history is not indexed.
- If the results do not answer the question, say so plainly. "I don't have \
anything on that" is a correct and useful answer.
- Be concise. Do not repeat the results back; answer the question."""


@dataclass
class Source:
    """One retrieved item, for attribution beneath the answer."""

    id: UUID
    title: str
    source: str


@dataclass
class Answer:
    text: str
    sources: list[Source]


def _fetch_bodies(
    conn: psycopg.Connection, hits: list[SearchHit], notes: NoteService
) -> dict[UUID, str]:
    """Full text for the top few note hits.

    Bodies are never in the index — search_index holds a 500-character excerpt and
    nothing more, because a cached body goes stale the moment the file is edited
    (Plan.md §2). Answering from excerpts alone means answering from previews, so
    the real content is read here, on demand.

    A note in the index but missing from the vault is normal rather than
    exceptional — it was renamed or deleted — so it is skipped, not raised.
    """
    bodies: dict[UUID, str] = {}

    for hit in hits:
        if len(bodies) >= FULL_TEXT_LIMIT:
            break
        if hit.source != "obsidian":
            continue

        mapping = lookup_provider_id(conn, hit.id)
        if mapping is None:
            continue

        _, provider_id = mapping
        note = notes.get_note(provider_id)
        if note is not None and note.body:
            bodies[hit.id] = note.body

    return bodies


def build_context(hits: list[SearchHit], bodies: dict[UUID, str]) -> str:
    """Render retrieved rows as text the model can reason over.

    Every row names its source and its kind, for the reason documented above
    KIND_LABELS. Rows with a fetched body show the body instead of the excerpt —
    that substitution is the whole point of fetching it.
    """
    if not hits:
        return "No results found in the user's personal system."

    blocks = []
    for index, hit in enumerate(hits, start=1):
        kind = KIND_LABELS.get(hit.source, "ITEM")
        lines = [f"[{index}] {kind} — {hit.title}  (source: {hit.source})"]

        caveat = SOURCE_CAVEATS.get(hit.source)
        if caveat:
            lines.append(f"    {caveat}")

        if hit.modified_at:
            lines.append(f"    date: {hit.modified_at:%Y-%m-%d %H:%M}")

        content = bodies.get(hit.id) or hit.excerpt
        if content:
            lines.append(f"    {content}")

        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def _chat(messages: list[dict], tools: list[dict] | None = None) -> dict:
    """One call to Ollama. Raises AssistantUnavailable if it is not running."""
    payload: dict = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "options": {"num_ctx": NUM_CTX},
    }
    if tools:
        payload["tools"] = tools

    try:
        response = httpx.post(OLLAMA_URL, json=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except httpx.ConnectError as exc:
        raise AssistantUnavailable(
            "The local model is not running. Start it with `ollama serve`, "
            f"and check that {MODEL} is pulled."
        ) from exc
    except httpx.HTTPError as exc:
        raise AssistantUnavailable(f"The local model failed: {exc}") from exc

    return response.json()["message"]


def ask(conn: psycopg.Connection, question: str, notes: NoteService) -> Answer:
    """Answer a question from the user's own indexed data."""
    # "any": a whole question never has all its words in one note (see search()).
    hits = search(conn, question, limit=SEARCH_LIMIT, match="any")
    bodies = _fetch_bodies(conn, hits, notes)
    context = build_context(hits, bodies)

    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Search results:\n\n{context}\n\nQuestion: {question}",
        },
    ]

    sources = [Source(id=h.id, title=h.title, source=h.source) for h in hits]

    # The loop exists only for the get_note escape hatch. On the common path the
    # first response has no tool calls and this returns immediately.
    for _ in range(MAX_TURNS):
        message = _chat(messages, tools=_TOOLS)
        messages.append(message)

        tool_calls = message.get("tool_calls")
        if not tool_calls:
            return Answer(text=message.get("content", "").strip(), sources=sources)

        for call in tool_calls:
            name = call.get("function", {}).get("name")
            args = call.get("function", {}).get("arguments") or {}
            result = _run_tool(conn, name, args, hits, notes)
            messages.append(
                {"role": "tool", "content": result, "tool_name": name or "unknown"}
            )

    # Out of turns. Say so rather than returning an empty string that looks like a
    # considered "I don't know".
    return Answer(
        text="I could not settle on an answer — the model kept asking for more "
        "information without concluding.",
        sources=sources,
    )


# The tool takes the RESULT NUMBER, not an id.
#
# It took a UUID at first, which made it uncallable: the context labels results
# [1], [2], [3] and the UUID appears nowhere in it, so the model had nothing valid
# to pass. It sent "1" — the only identifier on screen — the call failed, and it
# concluded it had no information.
#
# Putting UUIDs in the context would have been the other fix, and a worse one:
# thirty-six characters of hex per result, spent on something a small model
# reliably mangles. Asking for the number it can see is both cheaper and safer.
_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_note",
            "description": (
                "Read the full text of one note from the search results. Pass the "
                "number shown in brackets before it, e.g. 2 for the result labelled "
                "[2]. Use only when the text already shown is not enough to answer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "result_number": {
                        "type": "integer",
                        "description": "The bracketed number of the result to read.",
                    }
                },
                "required": ["result_number"],
            },
        },
    }
]


def _run_tool(
    conn: psycopg.Connection,
    name: str | None,
    args: dict,
    hits: list[SearchHit],
    notes: NoteService,
) -> str:
    """Execute a tool call. Errors are returned to the model, not raised.

    A hallucinated tool name or an out-of-range number is a normal failure mode for
    a small model, and one it can often recover from when told plainly. Raising
    would end the run and discard an answer it could still have given.
    """
    if name != "get_note":
        return f"Error: no tool named {name!r}."

    raw = args.get("result_number")
    try:
        # Small models often send "2" rather than 2, despite the integer schema.
        index = int(str(raw).strip())
    except (TypeError, ValueError):
        return f"Error: {raw!r} is not a result number. Use the number in brackets."

    if not 1 <= index <= len(hits):
        return f"Error: there is no result [{index}]. Results are 1 to {len(hits)}."

    hit = hits[index - 1]
    if hit.source != "obsidian":
        return (
            f"Result [{index}] is a {hit.source} item, not a note — "
            "it has no further text to read beyond what you were shown."
        )

    mapping = lookup_provider_id(conn, hit.id)
    if mapping is None:
        return f"Error: result [{index}] could not be resolved."

    _, provider_id = mapping
    note = notes.get_note(provider_id)
    if note is None:
        return "That note is in the index but no longer in the vault."

    return note.body or "(the note is empty)"
