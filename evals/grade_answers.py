"""Answer eval, step 2: grade each answer by hand.

    .venv/bin/python -m evals.grade_answers NAME              # ungraded, one at a time
    .venv/bin/python -m evals.grade_answers NAME --regrade q006
    .venv/bin/python -m evals.grade_answers NAME --summary --save

Hand grades come before any AI grader, deliberately (ADR-008): an AI grader is only
worth trusting once its grades are shown to agree with a person's, and these are
the grades it will be measured against.

For each answer this shows the question, the section of the note that actually
answers it — so grading needs no digging — what the assistant said, and the
automatic checks. One key per grade; saved after every grade, so stop any time.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from datetime import date
from pathlib import Path

from psycopg.rows import dict_row

from api.database import connect
from api.embeddings import EmbeddingsUnavailable, embed_query
from api.vectors import best_chunks
from evals.run_answers import GRADES, load_records, print_summary, save_records, summarize

BASELINES = Path(__file__).resolve().parent / "baselines.json"


LIST_ITEM = re.compile(r"^\s*([-*]|\d+\.)\s")


def wrap(text: str, indent: str = "    ", width: int = 88) -> str:
    """Re-flow text for the terminal.

    Notes are hard-wrapped at ~88 characters, so wrapping each line on its own
    leaves ragged half-lines. Lines are joined back into paragraphs first — except
    where a new line starts a list item, which is a real break.
    """
    paragraphs: list[str] = []
    for line in text.strip().splitlines():
        if not line.strip():
            paragraphs.append("")
        elif paragraphs and paragraphs[-1] and not LIST_ITEM.match(line):
            paragraphs[-1] += " " + line.strip()
        else:
            paragraphs.append(line.strip())
    lines = []
    for paragraph in paragraphs:
        lines.extend(textwrap.wrap(paragraph, width, initial_indent=indent, subsequent_indent=indent) or [""])
    return "\n".join(lines)


def where_it_answers(conn, record: dict) -> str:
    """The best-matching section of each expected item, as the grader's reference."""
    if not record["answerable"]:
        return "    No note answers this — it is a trap. The right behaviour is to say so."
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "select id, provider_id from entity_map where (provider, provider_id) in "
            "(select * from unnest(%s::text[], %s::text[]))",
            ([e["source"] for e in record["expected"]], [e["provider_id"] for e in record["expected"]]),
        )
        ids = {r["id"]: r["provider_id"] for r in cur.fetchall()}
    try:
        chunks = best_chunks(conn, embed_query(record["question"]), list(ids))
    except EmbeddingsUnavailable:
        return "    (start Ollama to see the section that answers it)"
    parts = []
    for entity_id, chunk in chunks.items():
        where = " › ".join(chunk.chunk_path) or "(whole note)"
        parts.append(f"    {ids[entity_id]}  [{where}]\n{wrap(chunk.chunk_text[:700])}")
    return "\n\n".join(parts) or "    (the expected item is not in the index — check its label)"


def show(conn, record: dict, left: int) -> None:
    print("\n" + "─" * 88)
    print(f"{record['id']}  [{', '.join(record['tags'])}]   ({left} left)")
    print(f"\nQ: {record['question']}")
    found = record["retrieval_found"]
    status = "trap" if found is None else ("FOUND the right note" if found else "MISSED the right note")
    print(f"\nRetrieval {status}. Sources given to the model: "
          + ", ".join(s["title"] for s in record["sources"]))
    print(f"\nWhere the answer is:\n{where_it_answers(conn, record)}")
    print(f"\nThe assistant said:\n{wrap(record['answer'])}")
    checks = record["checks"]
    flags = [f"malformed: {checks['malformed']}"] if checks["malformed"] else []
    flags += ["refused"] if checks["refused"] else []
    print(f"\nauto-checks: {', '.join(flags) or 'nothing flagged'}")


def ask_grade() -> str | None:
    """A grade name, "skip", or None to quit."""
    menu = "  ".join(f"{k}){name[1:]}" for k, (name, _) in GRADES.items())
    while True:
        key = input(f"\nGrade  {menu}   s)kip  ?)help  q)uit: ").strip().lower()
        if key in GRADES:
            return GRADES[key][0]
        if key == "s":
            return "skip"
        if key == "q":
            return None
        if key == "?":
            for k, (name, meaning) in GRADES.items():
                print(f"   {k}  {name:13} {meaning}")


def grade(name: str, regrade: set[str] | None) -> int:
    records = load_records(name)
    if not records:
        print(f"No answers recorded as '{name}'. Run: .venv/bin/python -m evals.run_answers {name}")
        return 1
    queue = [r for r in records if (r["id"] in regrade if regrade else not r["grade"])]
    with connect() as conn:
        for n, record in enumerate(queue):
            show(conn, record, len(queue) - n)
            try:
                result = ask_grade()
                if result is None:
                    break
                if result == "skip":
                    continue
                note = input("Note (optional, Enter to skip): ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            record["grade"], record["grade_note"] = result, note
            save_records(name, records)

    graded = sum(bool(r["grade"]) for r in records)
    print(f"\n{graded}/{len(records)} graded.\n")
    print_summary(summarize(records))
    return 0


def save_summary(name: str) -> None:
    data = json.loads(BASELINES.read_text(encoding="utf-8"))
    data.setdefault("answers", []).append(
        {"name": name, "date": date.today().isoformat(), **summarize(load_records(name))}
    )
    BASELINES.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"\nsaved as '{name}' under 'answers' in {BASELINES.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name")
    parser.add_argument("--regrade", help="comma-separated ids to grade again")
    parser.add_argument("--summary", action="store_true", help="print the summary only")
    parser.add_argument("--save", action="store_true", help="with --summary: append to baselines.json")
    args = parser.parse_args()

    if args.summary:
        print_summary(summarize(load_records(args.name)))
        if args.save:
            save_summary(args.name)
        return 0
    return grade(args.name, set(args.regrade.split(",")) if args.regrade else None)


if __name__ == "__main__":
    sys.exit(main())
