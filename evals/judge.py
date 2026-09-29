"""AI grader for the answer eval (ADR-014): grade runs, and validate the grader.

    .venv/bin/python -m evals.judge validate NAME [NAME ...]   # agreement with existing grades
    .venv/bin/python -m evals.judge grade NAME [--only q001,q005] [--force]

Trust comes first (ADR-008, ADR-014): `validate` grades answers that already have
grades and measures agreement — above all with the user's own hand grades, the
only purely human ones. The grader is used for routine runs only if it agrees on
correct-vs-not with at least 85% of them.

What the grader is shown, per answer:
  QUESTION     what was asked
  REFERENCE    the note(s) that actually answer it — decides whether it is RIGHT
  CONTEXT      what the assistant was shown — decides whether it INVENTED something
  ANSWER       what the assistant said

Its grades go to `ai_grade` / `ai_reason` / `ai_model` and never overwrite `grade`.
OpenAI is called over plain HTTP (httpx): no new dependency. The key is read from
the Keychain first, then OPENAI_API_KEY (.env) — and never printed, anywhere.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import date

import httpx
from psycopg.rows import dict_row

from api.assistant import SEARCH_LIMIT, build_context
from api.config import config
from api.database import connect
from api.hybrid import retrieve
from api.providers.obsidian import ObsidianVaultProvider
from api.tokens import TokenStoreError, load_secret
from evals.run_answers import GRADES, load_records, print_summary, save_records, summarize
from evals.run_retrieval import BASELINES

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
MODEL = os.getenv("OPENAI_GRADER_MODEL", "gpt-4.1-mini")
GRADE_NAMES = [name for name, _ in GRADES.values()]
MAX_NOTE_CHARS = 24_000  # the longest note is ~16K characters; this is a safety cap
GATE = 0.85  # correct-vs-not agreement with the user's grades required to trust it

RUBRIC = """You grade answers from a personal assistant. It answers questions about one \
person's own notes, tasks and projects, and it was given search results (the CONTEXT) \
and told to answer only from them.

You are shown the QUESTION; whether it is ANSWERABLE from the person's notes; the \
REFERENCE, which is the note(s) that actually answer it (none for a trap question); the \
CONTEXT the assistant was shown; and the ANSWER.

Pick exactly one grade. Check in this order and stop at the first that applies:

1. malformed: not an answer at all. A JSON blob or tool call written out as text, \
empty, or cut off mid-sentence.
2. hallucinated: states as fact something supported by neither the REFERENCE nor the \
CONTEXT: an invented decision, number, name, citation, or expansion of an acronym. One \
invented claim is enough, even inside an otherwise good answer or next to a refusal. \
Accepting a false premise in the question also counts ("we switched X because ...").
3. refused: says it has no information on this, without inventing anything.
4. correct: answers what was asked, consistent with the REFERENCE. Missing minor \
details is fine; missing the main point is not.
5. partial: the right direction, but misses the main point of the REFERENCE, is \
vague, or mixes a right reason with a wrong one.
6. wrong: incorrect. It misreads the REFERENCE, joins real facts into a false claim \
("X because Y" where the notes do not say that), or answers a different question.

For a trap question (ANSWERABLE: no), a clean refusal is "correct"; any claim about \
the thing asked, beyond saying there is nothing on it, is "hallucinated".

Judge substance, not style. Length, confidence and polish earn no credit.
Write your reasoning first, two or three sentences naming the claim that decided the \
grade, then give the grade."""

SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "grade": {"type": "string", "enum": GRADE_NAMES},
    },
    "required": ["reasoning", "grade"],
    "additionalProperties": False,
}


class GraderUnavailable(RuntimeError):
    """The grader cannot run: no key, a rejected key, no quota, or no model."""


def api_key() -> str:
    """Keychain first (the better home, ADR-014), then OPENAI_API_KEY from .env."""
    try:
        key = load_secret("openai_api_key")
    except TokenStoreError:
        key = None
    key = (key or os.environ.get("OPENAI_API_KEY", "")).strip()
    if not key:
        raise GraderUnavailable(
            "No OpenAI key. Set OPENAI_API_KEY in .env, or store it in the Keychain as "
            "'openai_api_key'."
        )
    return key


# ---------------------------------------------------------------------------
# What the grader is shown
# ---------------------------------------------------------------------------


def _index_row(conn, source: str, provider_id: str) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "select s.title, s.excerpt from search_index s "
            "join entity_map m on m.id = s.entity_id "
            "where m.provider = %s and m.provider_id = %s",
            (source, provider_id),
        )
        return cur.fetchone()


def _item_text(conn, notes: ObsidianVaultProvider, source: str, provider_id: str | None) -> str:
    """A note's full text, or a task / event / repo's index row."""
    if source == "obsidian" and provider_id:
        note = notes.get_note(provider_id)
        return (note.body or "")[:MAX_NOTE_CHARS] if note else "(note not found in the vault)"
    row = _index_row(conn, source, provider_id) if provider_id else None
    return f"{row['title']}\n{row['excerpt'] or ''}" if row else "(item not in the index)"


def reference_for(conn, notes, record: dict) -> str:
    if not record["answerable"]:
        return "(none: no note answers this; it is a trap question)"
    return "\n\n".join(
        f"### {e['provider_id']}\n{_item_text(conn, notes, e['source'], e['provider_id'])}"
        for e in record["expected"]
    )


def context_for(conn, notes, record: dict) -> str:
    """What the assistant was shown. Recorded since 2026-09-29; rebuilt for older runs.

    A run that used get_note also read whole notes, which were never recorded — but
    get_note could only fetch among the five sources, so their full text is a
    faithful superset of what it saw.
    """
    if record.get("context"):
        return record["context"]
    if any(c.get("tool_calls") for c in record["calls"]):
        return "\n\n".join(
            f"[{s['title']}] ({s['source']})\n{_item_text(conn, notes, s['source'], s.get('provider_id'))}"
            for s in record["sources"]
        )
    rebuilt = retrieve(
        conn,
        record["question"],
        limit=SEARCH_LIMIT,
        top_note_sections=record.get("top_note_sections", 1),
    )
    if rebuilt.mode != "hybrid":
        raise GraderUnavailable("Ollama must be running to rebuild an older run's context.")
    return build_context(rebuilt.hits, rebuilt.context)


# ---------------------------------------------------------------------------
# Calling the grader
# ---------------------------------------------------------------------------


def _call(key: str, messages: list[dict]) -> tuple[dict, dict]:
    """One grading request: (the parsed verdict, token usage). Retries transient errors."""
    payload = {
        "model": MODEL,
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "grade", "strict": True, "schema": SCHEMA},
        },
    }
    last = ""
    for attempt in range(8):
        try:
            response = httpx.post(
                OPENAI_URL,
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
                timeout=120.0,
            )
        except httpx.HTTPError as exc:
            last = type(exc).__name__
            time.sleep(2**attempt)
            continue

        if response.status_code == 200:
            data = response.json()
            message = data["choices"][0]["message"]
            if message.get("refusal"):
                raise GraderUnavailable(f"The grader model declined: {message['refusal'][:200]}")
            return json.loads(message["content"]), data.get("usage", {})

        # Error bodies are never echoed whole: a 401 body can contain part of the key.
        if response.status_code == 401:
            raise GraderUnavailable("OpenAI rejected the key (401). Check the key's value.")
        if response.status_code == 404:
            raise GraderUnavailable(
                f"Model {MODEL!r} is not available to this key (404). Set OPENAI_GRADER_MODEL."
            )
        if response.status_code == 429 and "insufficient_quota" in response.text:
            raise GraderUnavailable("No quota left on the OpenAI account: check billing and limits.")
        if response.status_code in (429, 500, 502, 503):
            last = f"HTTP {response.status_code}"
            time.sleep(_retry_after(response, attempt))
            continue
        raise GraderUnavailable(f"OpenAI returned HTTP {response.status_code}.")
    raise GraderUnavailable(f"OpenAI kept failing ({last}); try again later.")


def _retry_after(response, attempt: int) -> float:
    """How long to wait before retrying: what OpenAI says, else exponential backoff.

    A 429 is usually a per-minute token limit, which lower account tiers hit fast
    with a large model: waiting the stated time is enough, retrying sooner is not.
    """
    headers = getattr(response, "headers", {}) or {}
    for name, scale in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
        value = headers.get(name)
        if value:
            try:
                return min(float(value) * scale + 0.5, 90.0)
            except ValueError:
                pass
    return min(2.0 ** (attempt + 1), 60.0)


def grade_record(conn, notes, key: str, record: dict) -> dict:
    """Grade one answer; writes ai_grade / ai_reason / ai_model. Returns token usage."""
    prompt = (
        f"QUESTION: {record['question']}\n"
        f"ANSWERABLE: {'yes' if record['answerable'] else 'no'}\n\n"
        f"REFERENCE:\n{reference_for(conn, notes, record)}\n\n"
        f"CONTEXT (what the assistant was shown):\n{context_for(conn, notes, record)}\n\n"
        f"ANSWER:\n{record['answer'].strip() or '(empty)'}"
    )
    verdict, usage = _call(
        key, [{"role": "system", "content": RUBRIC}, {"role": "user", "content": prompt}]
    )
    record["ai_grade"] = verdict["grade"]
    record["ai_reason"] = verdict["reasoning"]
    record["ai_model"] = MODEL
    return usage


def grade_run(name: str, only: set[str] | None = None, force: bool = False) -> dict:
    """AI-grade a run; saved after every answer. Skips answers this model already graded."""
    key = api_key()
    records = load_records(name)
    if not records:
        raise GraderUnavailable(f"No answers recorded as '{name}'.")
    todo = [
        r for r in records
        if (not only or r["id"] in only) and (force or r.get("ai_model") != MODEL)
    ]
    notes = ObsidianVaultProvider(config.vault_path)
    tokens = Counter()
    with connect() as conn:
        conn.row_factory = dict_row
        for n, record in enumerate(todo, start=1):
            usage = grade_record(conn, notes, key, record)
            tokens.update({k: v for k, v in usage.items() if isinstance(v, int)})
            save_records(name, records)
            print(f"  [{n}/{len(todo)}] {record['id']}  {record['ai_grade']}")
    print(f"{name}: {len(todo)} graded by {MODEL}, "
          f"{tokens.get('prompt_tokens', 0)} tokens in / {tokens.get('completion_tokens', 0)} out")
    return dict(tokens)


# ---------------------------------------------------------------------------
# Validation: how far can the grader be trusted?
# ---------------------------------------------------------------------------


def is_correct(record: dict, grade: str | None) -> bool:
    """Correct-vs-not. On a trap, refusing IS correct."""
    if record["answerable"]:
        return grade == "correct"
    return grade in ("correct", "refused")


def agreement(pairs: list[tuple[bool, bool]]) -> dict:
    """Observed agreement and Cohen's kappa for two graders' correct-vs-not calls.

    Kappa discounts the agreement two graders would reach by chance, given how often
    each says "correct". 1.0 is perfect; around 0 is no better than chance.
    """
    n = len(pairs)
    if not n:
        return {"n": 0, "agree": None, "kappa": None}
    observed = sum(a == b for a, b in pairs) / n
    a_yes = sum(a for a, _ in pairs) / n
    b_yes = sum(b for _, b in pairs) / n
    chance = a_yes * b_yes + (1 - a_yes) * (1 - b_yes)
    kappa = (observed - chance) / (1 - chance) if chance < 1 else 1.0
    return {"n": n, "agree": round(observed, 3), "kappa": round(kappa, 3)}


def validate(names: list[str]) -> int:
    for name in names:
        grade_run(name)

    groups: dict[str, list[tuple[str, dict]]] = {"user": [], "claude": []}
    for name in names:
        for r in load_records(name):
            if r.get("grade") and r.get("ai_grade"):
                who = "claude" if r.get("grade_note", "").startswith("[Claude]") else "user"
                groups[who].append((name, r))

    print(f"\nGrader: {MODEL}")
    results = {}
    for who, rows in groups.items():
        pairs = [(is_correct(r, r["grade"]), is_correct(r, r["ai_grade"])) for _, r in rows]
        stats = agreement(pairs)
        stats["exact"] = round(sum(r["grade"] == r["ai_grade"] for _, r in rows) / len(rows), 3) if rows else None
        results[who] = stats
        label = "your hand grades" if who == "user" else "Claude's grades"
        print(f"vs {label:17} n={stats['n']:>3}  correct-vs-not agreement {stats['agree']}  "
              f"kappa {stats['kappa']}  exact-grade agreement {stats['exact']}")

    disagreements = [
        (name, r) for rows in groups.values() for name, r in rows
        if is_correct(r, r["grade"]) != is_correct(r, r["ai_grade"])
    ]
    if disagreements:
        print("\nDisagreements on correct-vs-not (read these — they are where trust is decided):")
        for name, r in disagreements:
            print(f"  {name} {r['id']}: graded {r['grade']}, AI said {r['ai_grade']}: {r['ai_reason'][:160]}")

    user = results["user"]
    passed = user["agree"] is not None and user["agree"] >= GATE
    print(f"\nGate (ADR-014): {GATE:.0%} agreement with your hand grades -> "
          f"{'PASS' if passed else 'FAIL'} ({user['agree']})")

    # Recorded, so evals.baseline can say whether this grader has earned trust.
    data = json.loads(BASELINES.read_text(encoding="utf-8"))
    data.setdefault("grader_validation", []).append(
        {"date": date.today().isoformat(), "model": MODEL, "runs": names,
         "gate": GATE, "passed": passed, **results}
    )
    BASELINES.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate", help="grade graded runs and measure agreement")
    v.add_argument("names", nargs="+")
    g = sub.add_parser("grade", help="AI-grade a run")
    g.add_argument("name")
    g.add_argument("--only", help="comma-separated question ids")
    g.add_argument("--force", action="store_true", help="re-grade answers already graded")
    args = parser.parse_args()

    try:
        if args.command == "validate":
            return validate(args.names)
        grade_run(args.name, set(args.only.split(",")) if args.only else None, args.force)
        print()
        print_summary(summarize(load_records(args.name), "ai_grade"))
        return 0
    except GraderUnavailable as exc:
        print(f"Grader unavailable: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
