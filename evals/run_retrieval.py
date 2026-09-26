"""Retrieval eval: does search() put the right item in the top k?

Run from the repo root, against the real index — rebuild it first (Settings →
Rebuild), or the eval measures yesterday's index:

    .venv/bin/python -m evals.run_retrieval
    .venv/bin/python -m evals.run_retrieval --save NAME --config "what changed"
    .venv/bin/python -m evals.run_retrieval --check

It calls search() exactly as the assistant does — same match mode, same k — so
the score describes what the assistant actually receives, not the search box.

Scoring:
  recall@k  fraction of answerable questions with an expected item in the top k
  MRR       mean of 1/rank of the first expected item (0 if missed); rewards #1
            over #5, which recall cannot see

`expected` lists ACCEPTABLE answers: finding any one of them counts. Unanswerable
questions (`answerable: false`) are not scored — there is nothing to find. They are
printed so you can see what the assistant would be handed instead.

Matching is on (source, provider_id), never on titles or entity ids. Titles are
not unique across sources, and entity ids are assigned rather than computed, so a
rebuild would silently repoint every label (ADR-004).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from api.assistant import SEARCH_LIMIT
from api.database import connect
from api.search import search

HERE = Path(__file__).resolve().parent
DATASET = HERE / "dataset.jsonl"
BASELINES = HERE / "baselines.json"

Ref = tuple[str, str]  # (source, provider_id)


@dataclass
class Result:
    id: str
    question: str
    tags: list[str]
    answerable: bool
    rank: int | None = None  # 1-based rank of the first expected hit; None = missed
    top: list[str] = field(default_factory=list)  # titles, for reading the output
    unknown_labels: list[Ref] = field(default_factory=list)


def load_dataset(path: Path = DATASET) -> list[dict]:
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _indexed_refs(conn: psycopg.Connection) -> set[Ref]:
    """Everything currently in the index, as (source, provider_id).

    Used to tell a bad LABEL from a search MISS. An expected item that is not in
    the index at all cannot be found by any search — the label has a typo, or the
    note was renamed — and scoring it as a miss would blame search for it.
    """
    rows = conn.execute(
        "select m.provider, m.provider_id from search_index s "
        "join entity_map m on m.id = s.entity_id"
    ).fetchall()
    return {(r["provider"], r["provider_id"]) for r in rows}


def _refs_for(conn: psycopg.Connection, ids: list) -> dict:
    """Entity id -> (source, provider_id), in one query rather than one per hit."""
    rows = conn.execute(
        "select id, provider, provider_id from entity_map where id = any(%s)", (ids,)
    ).fetchall()
    return {r["id"]: (r["provider"], r["provider_id"]) for r in rows}


def evaluate(conn: psycopg.Connection, rows: list[dict], k: int) -> list[Result]:
    indexed = _indexed_refs(conn)
    results = []

    for row in rows:
        result = Result(
            id=row["id"],
            question=row["question"],
            tags=row.get("tags", []),
            answerable=row["answerable"],
        )
        expected = {(e["source"], e["provider_id"]) for e in row["expected"]}
        result.unknown_labels = sorted(expected - indexed)

        # match="any" and this k are what the assistant uses (assistant.py).
        hits = search(conn, row["question"], limit=k, match="any")
        refs = _refs_for(conn, [h.id for h in hits])
        result.top = [h.title for h in hits]
        result.rank = next(
            (i for i, h in enumerate(hits, start=1) if refs.get(h.id) in expected),
            None,
        )
        results.append(result)

    return results


def _scored(results: list[Result]) -> list[Result]:
    """Answerable questions whose labels exist — the ones a score can be fair about."""
    return [r for r in results if r.answerable and not r.unknown_labels]


def summarize(results: list[Result]) -> dict:
    scored = _scored(results)
    found = [r for r in scored if r.rank is not None]
    n = len(scored) or 1  # an empty dataset scores 0 rather than dividing by zero

    per_tag: dict[str, dict[str, int]] = {}
    for r in scored:
        for tag in r.tags:
            bucket = per_tag.setdefault(tag, {"found": 0, "total": 0})
            bucket["total"] += 1
            bucket["found"] += r.rank is not None

    return {
        "recall_at_k": round(len(found) / n, 3),
        "mrr": round(sum(1 / r.rank for r in found) / n, 3),
        "found": len(found),
        "answerable": len(scored),
        "per_question": {r.id: r.rank for r in scored},
        "per_tag": dict(sorted(per_tag.items())),
    }


def _latest_run() -> dict | None:
    if not BASELINES.exists():
        return None
    runs = json.loads(BASELINES.read_text(encoding="utf-8")).get("retrieval", [])
    return runs[-1] if runs else None


def _describe(rank: int | None) -> str:
    return f"#{rank}" if rank else "missed"


def compare(summary: dict, previous: dict) -> tuple[list[str], list[str]]:
    """Question-by-question changes against a saved run: (better, worse).

    Only questions present in both are compared, so growing the dataset does not
    read as a regression. Lower rank is better; missed is worse than any rank.
    """
    better, worse = [], []
    worst = float("inf")
    for qid, rank in summary["per_question"].items():
        if qid not in previous["per_question"]:
            continue
        before = previous["per_question"][qid]
        a, b = before or worst, rank or worst
        line = f"{qid}: {_describe(before)} -> {_describe(rank)}"
        if b < a:
            better.append(line)
        elif b > a:
            worse.append(line)
    return better, worse


def report(results: list[Result], summary: dict, k: int) -> None:
    for r in results:
        if r.unknown_labels:
            print(f"{r.id}  BAD LABEL  not in the index: {r.unknown_labels}")
        elif not r.answerable:
            print(f"{r.id}  (trap)     handed {len(r.top)} results: {r.top[:3]}")
        else:
            status = f"FOUND #{r.rank}" if r.rank else "MISSED"
            print(f"{r.id}  {status:<10} {r.top[:3]}")

    print(
        f"\nrecall@{k}: {summary['found']}/{summary['answerable']} = "
        f"{summary['recall_at_k']:.0%}   MRR: {summary['mrr']:.3f}"
    )
    print("by tag:  " + "   ".join(
        f"{tag} {v['found']}/{v['total']}" for tag, v in summary["per_tag"].items()
    ))
    skipped = [r.id for r in results if r.answerable and r.unknown_labels]
    if skipped:
        print(f"NOT SCORED (fix the labels): {', '.join(skipped)}")


def save(summary: dict, name: str, config: str, k: int, dataset_size: int) -> None:
    data = (
        json.loads(BASELINES.read_text(encoding="utf-8"))
        if BASELINES.exists()
        else {"retrieval": []}
    )
    data["retrieval"].append(
        {
            "name": name,
            "date": date.today().isoformat(),
            "config": config,
            "k": k,
            "dataset_size": dataset_size,
            **summary,
        }
    )
    BASELINES.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--k", type=int, default=SEARCH_LIMIT)
    parser.add_argument("--save", metavar="NAME", help="append this run to baselines.json")
    parser.add_argument("--config", default="", help="what this run measured, for --save")
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if a question the latest saved run found is now missed",
    )
    args = parser.parse_args()

    rows = load_dataset()
    with connect() as conn:
        conn.row_factory = dict_row
        results = evaluate(conn, rows, args.k)

    summary = summarize(results)
    report(results, summary, args.k)

    previous = _latest_run()
    regressions: list[str] = []
    if previous and previous.get("k") == args.k:
        better, worse = compare(summary, previous)
        print(f"\nvs saved run '{previous['name']}':")
        for line in better:
            print(f"  better  {line}")
        for line in worse:
            print(f"  worse   {line}")
        if not better and not worse:
            print("  no question changed")
        # A question that was found and is now missed is the regression that
        # matters. One that slipped from #1 to #3 is visible above but not fatal.
        regressions = [line for line in worse if line.endswith("missed")]

    if args.save:
        save(summary, args.save, args.config, args.k, len(rows))
        print(f"\nsaved as '{args.save}' in {BASELINES.name}")

    if args.check and regressions:
        print(f"\nFAIL: {len(regressions)} question(s) regressed to missed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
