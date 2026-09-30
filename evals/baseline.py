"""Re-baseline in one command: index -> retrieval eval -> answers -> AI grades.

    .venv/bin/python -m evals.baseline NAME                   # everything
    .venv/bin/python -m evals.baseline NAME --retrieval-only  # ~1 minute: search changes only
    .venv/bin/python -m evals.baseline NAME --no-grade        # answers, graded later

Run on demand only — after a notes refresh, a search change, a model change. It
runs with the assistant's current defaults, so the numbers describe the system as
it is. Results are saved under NAME in evals/baselines.json, answers in
evals/answers/NAME.jsonl. It stops at the first step that fails, rather than
measuring something other than what it claims to.

The grades come from grader v2 on local qwen3 (ADR-014): free, nothing leaves the
machine, and trusted because `python -m evals.grader_v2 validate` passed its gate. A
different grader can be picked with --grader; if it has not passed, the output says so.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date

from psycopg.rows import dict_row

from api.assistant import SEARCH_LIMIT
from api.database import connect
from api.search_routes import run_reindex
from evals import grader_v2, run_answers, run_retrieval
from evals.judge import GraderUnavailable
from evals.run_retrieval import BASELINES


def step(title: str) -> None:
    print(f"\n{'═' * 74}\n{title}\n{'═' * 74}")


def keep_awake() -> None:
    """macOS: stay awake until this process exits. A sleeping laptop once turned a
    79-second answer into 97 minutes. Harmless elsewhere."""
    try:
        subprocess.Popen(
            ["caffeinate", "-i", "-w", str(os.getpid())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass


def reindex() -> bool:
    step("1. Rebuild the index")
    with connect() as conn:
        conn.row_factory = dict_row
        stats = run_reindex(conn)
    for source in ("notes", "captures", "events", "tasks", "repositories"):
        s = getattr(stats, source)
        print(f"  {source:13} {s['indexed'] + s['skipped']:>4} items ({s['indexed']} changed)")
    print(f"  embeddings    {stats.embeddings}")
    if stats.errors:
        print(f"  errors: {stats.errors}")
    if "embeddings" in stats.errors or stats.embeddings.get("pending"):
        # Without vectors, search silently becomes keyword-only — measuring that and
        # calling it the baseline would be a lie about the system.
        print("  STOPPED: vectors are missing. Start Ollama (`ollama serve`) and re-run.")
        return False
    if stats.errors:
        print("  (continuing: a missing source makes its questions fail, and the scores will show it)")
    return True


def retrieval(name: str) -> None:
    step("2. Retrieval eval")
    rows = run_retrieval.load_dataset()
    with connect() as conn:
        conn.row_factory = dict_row
        results = run_retrieval.evaluate(conn, rows, SEARCH_LIMIT, "hybrid")
    summary = run_retrieval.summarize(results)
    run_retrieval.report(results, summary, SEARCH_LIMIT)
    run_retrieval.save(summary, name, "baseline", SEARCH_LIMIT, len(rows), "hybrid")


def answers(name: str) -> bool:
    step("3. Answer eval (the assistant's current defaults)")
    return run_answers.run(name, None) == 0


def grade(name: str, backend: str) -> None:
    checker = grader_v2.Checker(grader_v2.make_backend(backend), grader_v2.CheckCache())
    grader_id = f"{checker.backend.name}|{grader_v2.VERSION}"
    step(f"4. AI grades ({grader_id})")
    grader_v2.grade_run(checker, name)

    # The summary reads one grade field; the grader's grades live under its id, so
    # they are copied across for counting only (never saved as "ai_grade").
    records = run_answers.load_records(name)
    for record in records:
        record["ai_grade"] = record["graders"][grader_id]["grade"]
    summary = run_answers.summarize(records, "ai_grade")
    run_answers.print_summary(summary)

    data = json.loads(BASELINES.read_text(encoding="utf-8"))
    data.setdefault("answers", []).append(
        {"name": name, "date": date.today().isoformat(), "grader_model": grader_id, **summary}
    )
    BASELINES.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    if not grader_v2.is_validated(grader_id):
        print(f"\n  NOTE: {grader_id} has not passed validation (ADR-014). Treat these "
              f"grades as provisional: python -m evals.grader_v2 validate --backend {backend}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name", help="a name for this baseline, e.g. 2026-10-01")
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument("--no-grade", action="store_true")
    parser.add_argument("--grader", default=grader_v2.OFFICIAL_BACKEND,
                        help="ollama:<model> or openai:<model> (default: %(default)s)")
    args = parser.parse_args()

    keep_awake()
    if not reindex():
        return 1
    retrieval(args.name)
    if args.retrieval_only:
        return 0
    if not answers(args.name):
        return 1
    if args.no_grade:
        print(f"\nAnswers recorded. Grade later: python -m evals.grader_v2 grade {args.name}")
        return 0
    try:
        grade(args.name, args.grader)
    except GraderUnavailable as exc:
        print(f"\n  Grading skipped: {exc}\n  Answers are saved; grade later with "
              f"python -m evals.grader_v2 grade {args.name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
