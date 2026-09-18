"""The tool-calling loop, by hand, against a local model and the real search index.

No SDK and no framework (ADR-010). Ollama's /api/chat is plain HTTP, so every part
of the exchange is visible: what gets sent, what comes back, who actually executes
the tool.

    ollama serve                 # in another tab, if it is not already running
    docker compose up -d         # search() talks to Postgres directly
    .venv/bin/python docs/experiments/tool-calling/step1_ollama.py

The one thing worth internalising: **the model never runs anything.** It emits a
request — "call search_personal_os with query=X" — and stops. This script runs the
function, appends the result as a new message, and asks again. That round trip is
the entire mechanic behind every agent, and it is about fifteen lines.

Model choice is local per ADR-008: the constraint is avoiding payment details, not
cost. Expect the tool calls to be less reliable than a hosted model would manage —
that unreliability is itself part of what this experiment shows.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from api.database import connect  # noqa: E402
from api.search import search  # noqa: E402

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "llama3.1:8b"

# A loop with no ceiling is one of the failure modes LEARNING.md lists under tool
# calling. A model that keeps re-calling the same tool would otherwise run forever.
MAX_TURNS = 6


# ---------------------------------------------------------------------------
# The tool: the real search, not a stand-in
# ---------------------------------------------------------------------------


# What each source's rows actually ARE, in words the model can act on.
#
# This exists because of a real failure. Rendering every hit as "[source] title"
# made an open Google task look exactly like a note, and the model answered "what
# have we done?" by listing the user's to-do items as accomplishments. Every claim
# was traceable to a retrieved row — grounded, and entirely wrong.
#
# index_tasks() calls list_tasks(include_completed=False), so *every* task in the
# index is unfinished by construction. That fact lived in the indexer and never
# reached the model. No amount of model capability recovers a fact that was never
# sent; this is what context engineering means (Plan_2 §5).
#
# Mapping from source rather than a kind column because they are 1:1 today and this
# is an experiment. If a source ever indexes two kinds, read the kind column instead.
KIND_LABELS = {
    "obsidian": "NOTE",
    "personal_os": "CAPTURED IDEA",
    "google_calendar": "CALENDAR EVENT",
    "google_tasks": "OPEN TASK — planned, NOT yet done",
    "github": "REPOSITORY",
}

# Stated limitations, per source. Naming what the index does *not* contain is as
# important as naming what it does: the repo rows hold name and description only,
# so without this the model reads a repo's existence as evidence of progress in it.
SOURCE_CAVEATS = {
    "github": "(repository metadata only — no commit history is indexed)",
}


def search_personal_os(query: str) -> str:
    """Run the production keyword search and render it as text for the model.

    The tool result has to be a string. SearchHit objects mean nothing to a model,
    so source/kind/title/excerpt get flattened into lines it can read.

    Every hit names its source, for the same reason the UI does (Plan.md §12): an
    answer built on a calendar event and one built on a note are different claims,
    and the model cannot tell them apart unless told. It also names its *kind*, for
    the reason documented above KIND_LABELS.

    The row factory is not optional. search() reads rows as dicts (row["entity_id"]),
    which a bare connect() does not provide — the routes get it from ConnDep in
    api/captures.py. Calling search() from outside FastAPI means setting it here, or
    every row access raises "tuple indices must be integers".
    """
    with connect() as conn:
        conn.row_factory = dict_row
        hits = search(conn, query, limit=5)

    if not hits:
        # An explicit sentence, not an empty string. "No results" is information;
        # a blank tool result reads as a malfunction and invites invention.
        return "No results found."

    blocks = []
    for hit in hits:
        kind = KIND_LABELS.get(hit.source, "ITEM")
        caveat = SOURCE_CAVEATS.get(hit.source)

        lines = [f"[{hit.source}] {kind}: {hit.title}"]
        if caveat:
            lines.append(f"  {caveat}")
        if hit.excerpt:
            lines.append(f"  {hit.excerpt}")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_personal_os",
            # The description is the only thing the model has to decide *whether*
            # to call this. Naming the sources matters: "search notes" alone would
            # not suggest this can answer a question about repositories or tasks.
            "description": (
                "Search the user's personal knowledge base — Obsidian notes, quick "
                "captures, calendar events, tasks, and GitHub repositories — by "
                "keyword. Use this whenever the question is about the user's own "
                "projects, decisions, notes, or activity."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keywords to search for.",
                    }
                },
                "required": ["query"],
            },
        },
    }
]

# Name -> callable. Swapping the fake weather tool for the real one was a change to
# this dict and the schema above; the loop below never changed.
AVAILABLE_TOOLS = {"search_personal_os": search_personal_os}


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


# The first run had no system prompt at all, which is its own finding: with no
# instruction, the model treated everything retrieved as equally true and equally
# past-tense, and had no licence to say "I don't know".
#
# Each line here earns its place from an observed failure rather than from a sense
# that prompts should be long:
#   - the task/plan distinction is the bug documented above KIND_LABELS
#   - the repo line stops "this repo exists" being read as "work happened in it"
#   - the last line is what the benchmark's unanswerable case needs: nothing
#     currently tells the model that returning nothing is an acceptable answer
SYSTEM_PROMPT = """You answer questions about the user's own notes, tasks, \
calendar, and repositories, using the search_personal_os tool.

Rules:
- Answer only from what the tool returns. Do not add knowledge of your own.
- An OPEN TASK is something the user PLANS to do and has NOT done. Never report an \
open task as completed work.
- A REPOSITORY result proves the repo exists. It says nothing about what was done \
in it — commit history is not indexed.
- If the results do not answer the question, say so plainly. "I don't have anything \
on that" is a correct and useful answer.
- Say which result each part of your answer came from."""


def run_conversation(question: str) -> str:
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]

    for turn in range(MAX_TURNS):
        response = httpx.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "messages": messages,
                "tools": TOOLS,
                "stream": False,
            },
            timeout=120.0,  # a local 8B model on CPU/GPU is not fast
        )
        response.raise_for_status()
        message = response.json()["message"]

        # The assistant's own turn goes back into history verbatim. Dropping it
        # loses the record of which tool was requested, and the next request stops
        # making sense.
        messages.append(message)

        tool_calls = message.get("tool_calls")
        if not tool_calls:
            return message.get("content", "")

        for call in tool_calls:
            name = call["function"]["name"]
            args = call["function"]["arguments"]  # already a dict, not a JSON string

            print(f"  → turn {turn + 1}: model asked for {name}({json.dumps(args)})")

            fn = AVAILABLE_TOOLS.get(name)
            failed = False
            if fn is None:
                # Hallucinated tool names are a real failure mode. Reporting it back
                # lets the model correct itself; raising would end the run.
                result = f"Error: no tool named {name!r}."
                failed = True
            else:
                try:
                    result = fn(**args)
                except Exception as exc:  # noqa: BLE001 - surfacing beats crashing
                    result = f"Error running {name}: {exc}"
                    failed = True

            # Print a preview, not just a length. "tool returned 77 chars" once hid
            # an exception message in plain sight — the run looked like it worked
            # and the model was left to explain a failure nobody could see.
            preview = str(result).replace("\n", " ")[:150]
            marker = "!!" if failed else "  "
            print(f"    {marker} {len(str(result))} chars: {preview}")

            messages.append(
                {"role": "tool", "content": str(result), "tool_name": name}
            )

    return f"Stopped after {MAX_TURNS} turns without a final answer."


def main() -> int:
    # A real benchmark question, deliberately. You already know by hand which note
    # should answer it, so the thing to check is not whether the prose reads well —
    # it is whether the answer is grounded in that note or invented around it.
    question = "What have we done so far in resume builder?"

    print(f"Q: {question}\n")
    answer = run_conversation(question)
    print(f"\nA: {answer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
