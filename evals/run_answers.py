"""Answer eval, step 1: ask the assistant every question, record, auto-check.

    .venv/bin/python -m evals.run_answers NAME                  # all questions
    .venv/bin/python -m evals.run_answers NAME --only q006,q030  # a few

Then grade the answers by hand:

    .venv/bin/python -m evals.grade_answers NAME

Retrieval eval asks "did the right note reach the model?". This asks "what did the
model do with it?". Every answer is recorded with where it came from, so a wrong
one can be blamed on the right half:

    retrieval found the note + answer wrong   ->  generation failed (the model)
    retrieval missed the note + answer wrong  ->  retrieval failed

A full run is one local-model call per question, which can take tens of minutes.
Each answer is written to evals/answers/NAME.jsonl the moment it lands, and
re-running the same NAME skips questions already answered — an interruption loses
at most the question in flight.

The automatic checks are hints, not verdicts: whether an answer is RIGHT is the
hand grade's job. They catch what needs no judgement — an answer that is not an
answer at all, and whether the model said "I don't have anything on that".
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

from psycopg.rows import dict_row

from api.assistant import MODEL, AssistantUnavailable, ask
from api.config import config
from api.database import connect
from api.providers.obsidian import ObsidianVaultProvider
from evals.run_retrieval import _refs_for, load_dataset

ANSWERS = Path(__file__).resolve().parent / "answers"

# The hand grades. For a trap question (nothing to find), "refused" is correct.
GRADES = {
    "c": ("correct", "answers it, and everything it says is in the notes"),
    "p": ("partial", "right direction, but incomplete or vague"),
    "w": ("wrong", "incorrect, or answers a different question"),
    "h": ("hallucinated", "states something that is not in the notes it was given"),
    "r": ("refused", "says it has nothing on it — right for traps, wrong otherwise"),
    "m": ("malformed", "not an answer: a JSON blob, cut off, empty"),
}

# Phrasings of "I don't have anything on that". Deliberately loose: a false hit is
# corrected by the hand grade, a miss just shows up there instead.
REFUSAL = re.compile(
    r"\b(don't|do not|doesn't|does not|can't|cannot|couldn't|could not)\s+"
    r"(have|find|see|contain|mention|say|know)"
    r"|\bno (information|results|mention|record)"
    r"|\bnot (mentioned|in the (search )?results)"
    r"|\bnothing (on|about)",
    re.IGNORECASE,
)


def check(text: str) -> dict:
    """What can be told about an answer without judging it."""
    stripped = text.strip()
    malformed = None
    if not stripped:
        malformed = "empty"
    elif stripped.startswith("I could not settle on an answer"):
        malformed = "gave up: ran out of tool-call turns"
    elif stripped.startswith("{") and '"name"' in stripped:
        # The 8B writing a tool call out as text instead of making it — the failure
        # assistant.py's docstring records. Parsed when possible; flagged either way.
        malformed = "tool call written as text"
    return {
        "malformed": malformed,
        "refused": malformed is None and bool(REFUSAL.search(stripped)),
    }


def load_records(name: str) -> list[dict]:
    path = ANSWERS / f"{name}.jsonl"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def save_records(name: str, records: list[dict]) -> None:
    ANSWERS.mkdir(exist_ok=True)
    path = ANSWERS / f"{name}.jsonl"
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    tmp.replace(path)  # never leave a half-written file behind


def run(name: str, only: set[str] | None, offer_tools: bool = False, model: str = MODEL) -> int:
    questions = [q for q in load_dataset() if not only or q["id"] in only]
    records = load_records(name)
    done = {r["id"] for r in records}
    todo = [q for q in questions if q["id"] not in done]
    print(f"{len(done)} already answered, {len(todo)} to go -> {ANSWERS.name}/{name}.jsonl\n")

    notes = ObsidianVaultProvider(config.vault_path)
    with connect() as conn:
        conn.row_factory = dict_row
        for n, q in enumerate(todo, start=1):
            started = time.time()
            try:
                answer = ask(conn, q["question"], notes, offer_tools=offer_tools, model=model)
            except AssistantUnavailable as exc:
                print(f"\nStopped: {exc}\nFinished answers are saved; re-run to resume.")
                return 1

            refs = _refs_for(conn, [s.id for s in answer.sources])
            expected = {(e["source"], e["provider_id"]) for e in q["expected"]}
            record = {
                "id": q["id"],
                "question": q["question"],
                "answerable": q["answerable"],
                "tags": q.get("tags", []),
                "expected": q["expected"],
                "answer": answer.text,
                "sources": [
                    {"title": s.title, "source": s.source, "provider_id": refs.get(s.id, (None, None))[1]}
                    for s in answer.sources
                ],
                "retrieval_mode": answer.retrieval_mode,
                "tools_offered": offer_tools,
                "model": model,
                # None for traps: there was nothing to find.
                "retrieval_found": any(refs.get(s.id) in expected for s in answer.sources)
                if q["answerable"]
                else None,
                "calls": answer.calls,
                "seconds": round(time.time() - started, 1),
                "checks": check(answer.text),
                "grade": None,
                "grade_note": "",
            }
            records.append(record)
            save_records(name, records)

            flag = record["checks"]["malformed"] or ("refused" if record["checks"]["refused"] else "")
            print(f"[{n}/{len(todo)}] {q['id']}  {record['seconds']:>5}s  {flag}")

    print()
    print_summary(summarize(records))
    return 0


def summarize(records: list[dict]) -> dict:
    """Auto-check counts always; hand-grade counts once there are grades."""
    answerable = [r for r in records if r["answerable"]]
    traps = [r for r in records if not r["answerable"]]
    # The model's own reported working time, not wall-clock: a laptop that sleeps
    # mid-run turned one 79-second answer into 97 minutes of "seconds".
    seconds = [round(sum(c.get("seconds", 0) for c in r["calls"]), 1) for r in records]
    prompt_tokens = [c["prompt_tokens"] for r in records for c in r["calls"][:1] if c.get("prompt_tokens")]
    load = [c["load_seconds"] for r in records for c in r["calls"] if c.get("load_seconds")]

    summary: dict = {
        "answered": len(records),
        "malformed": sum(bool(r["checks"]["malformed"]) for r in records),
        "traps_refused": sum(r["checks"]["refused"] for r in traps),
        "traps": len(traps),
        # Refused although the right note WAS in its context: generation's fault.
        "refused_despite_retrieval": sum(
            r["checks"]["refused"] and bool(r["retrieval_found"]) for r in answerable
        ),
        # The get_note escape hatch is meant to be rare. Each use is another full
        # model call, and it can bring back a whole note — the prompt size problem
        # sections were meant to fix.
        "used_a_tool": sum(any(c.get("tool_calls") for c in r["calls"]) for r in records),
        "median_seconds": statistics.median(seconds) if seconds else None,
        "median_prompt_tokens": statistics.median(prompt_tokens) if prompt_tokens else None,
        "total_load_seconds": round(sum(load), 1),
    }

    graded = [r for r in records if r["grade"]]
    if graded:
        graded_answerable = [r for r in graded if r["answerable"]]
        summary["graded"] = len(graded)
        summary["grades"] = dict(Counter(r["grade"] for r in graded))
        summary["correct"] = sum(r["grade"] == "correct" for r in graded_answerable)
        summary["graded_answerable"] = len(graded_answerable)
        # On a trap, the right behaviour IS refusing — so a grader may reasonably
        # mark a clean refusal either "refused" or "correct". Both count.
        summary["traps_correct"] = sum(
            r["grade"] in ("refused", "correct") for r in graded if not r["answerable"]
        )
        # The split that says which half to fix.
        not_correct = [r for r in graded_answerable if r["grade"] != "correct"]
        summary["generation_failures"] = sum(bool(r["retrieval_found"]) for r in not_correct)
        summary["retrieval_failures"] = sum(not r["retrieval_found"] for r in not_correct)
        per_tag: dict[str, list[int]] = {}
        for r in graded_answerable:
            for tag in r["tags"]:
                bucket = per_tag.setdefault(tag, [0, 0])
                bucket[0] += r["grade"] == "correct"
                bucket[1] += 1
        summary["correct_by_tag"] = {t: f"{c}/{n}" for t, (c, n) in sorted(per_tag.items())}
    return summary


def print_summary(s: dict) -> None:
    print(f"answered {s['answered']}   malformed {s['malformed']}   "
          f"traps refused {s['traps_refused']}/{s['traps']}   "
          f"refused despite finding the note {s['refused_despite_retrieval']}")
    print(f"median {s['median_seconds']}s of model time per answer, median {s['median_prompt_tokens']} prompt tokens, "
          f"{s['total_load_seconds']}s total spent loading the model")
    print(f"called a tool (get_note) on {s['used_a_tool']} of {s['answered']} answers")
    if "graded" in s:
        print(f"\ngraded {s['graded']}: {s['grades']}")
        print(f"correct {s['correct']}/{s['graded_answerable']} answerable   "
              f"traps handled {s['traps_correct']}/{s['traps']}")
        print(f"not correct -> generation failures {s['generation_failures']}   "
              f"retrieval failures {s['retrieval_failures']}")
        print("correct by tag: " + "   ".join(f"{t} {v}" for t, v in s["correct_by_tag"].items()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name", help="a name for this run, e.g. hybrid-2026-09-28")
    parser.add_argument("--only", help="comma-separated question ids, e.g. q006,q030")
    parser.add_argument(
        "--tools",
        action="store_true",
        help="offer get_note (off by default, as in the assistant)",
    )
    # Tools are off by default now; kept so the commands in earlier runs still work.
    parser.add_argument("--no-tools", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--model", default=MODEL, help=f"Ollama model (default {MODEL})")
    args = parser.parse_args()
    only = set(args.only.split(",")) if args.only else None
    return run(args.name, only, offer_tools=args.tools, model=args.model)


if __name__ == "__main__":
    sys.exit(main())
