"""Grader v2: small yes/no checks, combined into a grade by code.

    .venv/bin/python -m evals.grader_v2 validate --backend ollama:qwen3:8b
    .venv/bin/python -m evals.grader_v2 validate --backend openai:gpt-4.1-nano
    .venv/bin/python -m evals.grader_v2 grade NAME --backend ollama:qwen3:8b

Why v2 exists: v1 asked one model for one six-way judgement over whole notes.
gpt-4.1-mini failed that (63% agreement); gpt-4.1 passed it but cost too much.
v2 is built around what SMALL models do well — one narrow question, short input,
a few labels — and hands everything else to code:

    1  code   is it malformed (a JSON blob, empty)?                 -> malformed
    2  model  does each SENTENCE only say what the source supports? -> hallucinated
    3  model  does the answer say it has nothing on this?           -> refused / trap: correct
    4  model  does the answer state each required KEY POINT?        -> correct / partial
    5  model  (no key point found) same overall conclusion?         -> partial / wrong

The combining rules are the answer key's rules (A, B, C — see evals/dataset.jsonl
and the 2026-09-29 corrections): every required point for "correct"; some, or the
right conclusion without the reasons, for "partial"; any invented claim is
"hallucinated", even beside a refusal.

The model behind the checks is swappable: OpenAI or local Ollama. Every check's
answer is cached by (backend, prompt), so an answer that has not changed is never
re-checked — the same text in two runs costs one call, not two.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Protocol

import httpx
from psycopg.rows import dict_row

from api.config import config
from api.database import connect
from api.providers.obsidian import ObsidianVaultProvider
from evals.judge import GraderUnavailable, api_key, context_for, is_correct, reference_for
from evals.run_answers import REFUSAL
from evals.run_answers import check as auto_check
from evals.run_answers import load_dataset, load_records, save_records
from evals.run_retrieval import BASELINES

# v2.1: per-part source caps (the context was being cut off), a "different things"
# check, the question inside the point check, and a more explicit refusal check —
# each from a miss in the first smoke test on the 8 known failures (4 caught).
# v2.2: sentences that state no fact (a bare refusal, a lead-in ending in a colon) are
# skipped by code: qwen3 was calling "I don't have anything on that." unsupported.
VERSION = "v2.2"
HERE = Path(__file__).resolve().parent
CACHE = HERE / "answers" / ".grader_cache.jsonl"
VALIDATION_RUNS = [
    "hybrid-2026-09-28",
    "no-tools-2026-09-29",
    "qwen3-no-tools-2026-09-29",
    "llama-3sections-2026-09-29",
]
# What a sentence is checked against, capped PER PART: one cap over the whole thing
# cut off what the assistant was shown, so claims taken from it looked invented.
MAX_REFERENCE_CHARS = 2500
MAX_CONTEXT_CHARS = 3500
MAX_SENTENCES = 15

# The failures the old graders got wrong, which any grader worth using must catch:
# (run, question, grade the answer key says).
KNOWN_FAILURES = [
    ("hybrid-2026-09-28", "q006", "malformed"),
    ("hybrid-2026-09-28", "q010", "hallucinated"),
    ("hybrid-2026-09-28", "q026", "hallucinated"),
    ("no-tools-2026-09-29", "q005", "wrong"),
    ("qwen3-no-tools-2026-09-29", "q005", "wrong"),
    ("llama-3sections-2026-09-29", "q016", "hallucinated"),
    ("qwen3-no-tools-2026-09-29", "q022", "refused"),
    ("llama-3sections-2026-09-29", "q026", "refused"),
]

SYSTEM = (
    "You check one thing about an answer from a personal assistant. "
    "Answer only the question asked, with exactly one of the allowed words."
)


# ---------------------------------------------------------------------------
# Backends: who answers the small questions
# ---------------------------------------------------------------------------


class Backend(Protocol):
    name: str

    def ask(self, prompt: str, options: list[str]) -> tuple[str, float | None, int]:
        """(chosen option, confidence 0-1 or None, tokens used)."""
        ...


class OllamaBackend:
    """A local model. Structured output keeps it inside the allowed options."""

    def __init__(self, model: str):
        self.model = model
        self.name = f"ollama:{model}"

    def ask(self, prompt: str, options: list[str]) -> tuple[str, float | None, int]:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            "stream": False,
            "format": {
                "type": "object",
                "properties": {"answer": {"type": "string", "enum": options}},
                "required": ["answer"],
            },
            "options": {"temperature": 0, "num_ctx": 4096},
        }
        if self.model.startswith("qwen3"):
            payload["think"] = False
        try:
            response = httpx.post("http://localhost:11434/api/chat", json=payload, timeout=300.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise GraderUnavailable(f"Ollama failed ({type(exc).__name__}); is it running?") from exc
        data = response.json()
        answer = json.loads(data["message"]["content"])["answer"]
        return answer, None, data.get("prompt_eval_count", 0) + data.get("eval_count", 0)


class OpenAIBackend:
    """A hosted model. One-word replies, with the token probability as confidence."""

    def __init__(self, model: str):
        self.model = model
        self.name = f"openai:{model}"
        self.key = api_key()

    def ask(self, prompt: str, options: list[str]) -> tuple[str, float | None, int]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"{prompt}\n\nReply with exactly one word: {', '.join(options)}."},
            ],
            "temperature": 0,
            "max_tokens": 3,
            "logprobs": True,
            "top_logprobs": 5,
        }
        for attempt in range(8):
            response = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.key}"},
                json=payload,
                timeout=120.0,
            )
            if response.status_code == 200:
                break
            if response.status_code == 429 and "insufficient_quota" in response.text:
                raise GraderUnavailable("No quota left on the OpenAI account.")
            if response.status_code in (429, 500, 502, 503):
                wait = response.headers.get("retry-after")
                time.sleep(min(float(wait) + 0.5, 90.0) if wait else min(2.0 ** (attempt + 1), 60.0))
                continue
            raise GraderUnavailable(f"OpenAI returned HTTP {response.status_code}.")
        else:
            raise GraderUnavailable("OpenAI kept failing; try again later.")

        data = response.json()
        choice = data["choices"][0]
        word = choice["message"]["content"].strip().lower().strip(".")
        answer = next((o for o in options if word.startswith(o)), None)
        if answer is None:
            raise GraderUnavailable(f"{self.model} replied outside the options: {word!r}")
        # Confidence: probability of the chosen word among the allowed words.
        top = (choice.get("logprobs") or {}).get("content") or []
        confidence = None
        if top:
            probs = {}
            for alt in top[0].get("top_logprobs", []):
                token = alt["token"].strip().lower()
                for option in options:
                    if option.startswith(token) and token:
                        probs[option] = probs.get(option, 0.0) + math.exp(alt["logprob"])
            if probs.get(answer):
                confidence = round(probs[answer] / sum(probs.values()), 3)
        usage = data.get("usage", {})
        return answer, confidence, usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)


def make_backend(spec: str) -> Backend:
    provider, _, model = spec.partition(":")
    if provider == "ollama":
        return OllamaBackend(model)
    if provider == "openai":
        return OpenAIBackend(model)
    raise SystemExit(f"Unknown backend {spec!r}: use ollama:<model> or openai:<model>")


# ---------------------------------------------------------------------------
# The cache: an unchanged answer is never checked twice
# ---------------------------------------------------------------------------


class CheckCache:
    def __init__(self, path: Path = CACHE):
        self.path = path
        self.entries: dict[str, dict] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entry = json.loads(line)
                    self.entries[entry["key"]] = entry

    @staticmethod
    def key(backend: str, prompt: str, options: list[str]) -> str:
        return hashlib.sha256(f"{VERSION}\n{backend}\n{options}\n{prompt}".encode()).hexdigest()

    def get(self, key: str) -> dict | None:
        return self.entries.get(key)

    def put(self, key: str, answer: str, confidence: float | None) -> None:
        entry = {"key": key, "answer": answer, "confidence": confidence}
        self.entries[key] = entry
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")


@dataclass
class Checker:
    backend: Backend
    cache: CheckCache
    calls: int = 0  # made to the model
    cached: int = 0  # answered from the cache
    tokens: int = 0
    budget_tokens: int | None = None

    def ask(self, prompt: str, options: list[str]) -> tuple[str, float | None]:
        key = CheckCache.key(self.backend.name, prompt, options)
        hit = self.cache.get(key)
        if hit:
            self.cached += 1
            return hit["answer"], hit["confidence"]
        if self.budget_tokens is not None and self.tokens >= self.budget_tokens:
            raise GraderUnavailable(f"Token budget of {self.budget_tokens} reached; stopped.")
        answer, confidence, used = self.backend.ask(prompt, options)
        self.calls += 1
        self.tokens += used
        self.cache.put(key, answer, confidence)
        return answer, confidence


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------

_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")


def sentences(answer: str) -> list[str]:
    """The answer's sentences, each checked on its own. Fragments under four words
    ("Demucs or UVR." is kept — short answers are exactly what matters)."""
    parts = []
    for line in answer.splitlines():
        line = _BULLET.sub("", line).strip()
        parts.extend(p.strip() for p in re.split(r"(?<=[.!?])\s+", line) if p.strip())
    kept = [p for p in parts if len(p.split()) >= 3 or len(parts) == 1]
    return kept[:MAX_SENTENCES]


# A refusal with a claim tacked on ("..., but RabbitMQ was chosen") is still checked.
_CLAIM_AFTER = re.compile(r"\b(but|however|although|though|instead)\b", re.IGNORECASE)


def states_no_fact(sentence: str) -> bool:
    """A lead-in to a list, or only a statement that nothing was found: nothing in it
    could be invented, so it is not sent to the model."""
    if sentence.endswith(":"):
        return True
    return bool(REFUSAL.search(sentence)) and not _CLAIM_AFTER.search(sentence)


def _points(key_points: dict) -> str:
    must = "\n".join(f"- {p}" for p in key_points.get("must", []))
    also = "\n".join(f"- {p}" for p in key_points.get("also", []))
    return must + (f"\n{also}" if also else "")


@dataclass
class Verdict:
    grade: str
    checks: list[dict] = field(default_factory=list)
    min_confidence: float | None = None


def grade_answer(checker: Checker, record: dict, key_points: dict | None, source: str) -> Verdict:
    checks: list[dict] = []
    confidences: list[float] = []

    def ask(kind: str, prompt: str, options: list[str], **detail) -> str:
        answer, confidence = checker.ask(prompt, options)
        checks.append({"check": kind, "answer": answer, "confidence": confidence, **detail})
        if confidence is not None:
            confidences.append(confidence)
        return answer

    def verdict(grade: str) -> Verdict:
        return Verdict(grade, checks, min(confidences) if confidences else None)

    text = record["answer"].strip()

    # 1. Code: not an answer at all.
    if auto_check(text)["malformed"]:
        checks.append({"check": "malformed", "answer": "yes", "by": "code"})
        return verdict("malformed")

    # 2. Every sentence against the source: one invented claim is enough (rule C).
    for sentence in sentences(text):
        if states_no_fact(sentence):
            checks.append({"check": "supported", "answer": "skipped", "by": "code", "sentence": sentence})
            continue
        supported = ask(
            "supported",
            f"SOURCE TEXT:\n{source}\n\nSENTENCE: {sentence}\n\n"
            "Is every factual claim in the SENTENCE supported by the SOURCE TEXT? A sentence "
            "that only says information is missing, or only restates the question, counts as "
            "supported. Answer yes or no.",
            ["yes", "no"],
            sentence=sentence,
        )
        if supported == "no":
            return verdict("hallucinated")

    # 3. A refusal: right on a trap, a failure on a question the notes answer.
    refused = ask(
        "refusal",
        f"QUESTION: {record['question']}\n\nANSWER: {text}\n\n"
        "Does the ANSWER say, in any wording, that the information asked for is not "
        "available: not specified, not mentioned, not in the results or the notes, or that "
        "the thing asked about never happened? Answer yes or no.",
        ["yes", "no"],
    )
    if not record["answerable"]:
        return verdict("correct" if refused == "yes" else "wrong")
    if refused == "yes":
        return verdict("refused")

    # 4a. An answer about something else can still share a word with a key point
    # (q005: a "confirmation gate" from a different list of protections).
    different = ask(
        "different",
        f"QUESTION: {record['question']}\n\nEXPECTED ANSWER:\n{_points(key_points)}\n\n"
        f"ANSWER: {text}\n\n"
        "Does the ANSWER mainly describe different things than the EXPECTED ANSWER, such as "
        "a different list, decision, or feature, rather than the same things (even if only "
        "some of them)? Answer yes or no.",
        ["yes", "no"],
    )
    if different == "yes":
        return verdict("wrong")

    # 4b. Each required point (rule B: all of them for "correct").
    found = [
        ask(
            "point",
            f"QUESTION: {record['question']}\n\nPOINT: {point}\n\nANSWER: {text}\n\n"
            "Does the ANSWER state this POINT as part of its answer to the QUESTION, in any "
            "wording? If the ANSWER says this is unknown or not found, answer no. Answer yes, "
            "partly, or no.",
            ["yes", "partly", "no"],
            point=point,
        )
        for point in key_points["must"]
    ]
    if all(f == "yes" for f in found):
        return verdict("correct")
    if any(f in ("yes", "partly") for f in found):
        return verdict("partial")

    # 5. No required point: right conclusion without the reasons (rule A) is partial.
    same = ask(
        "conclusion",
        f"QUESTION: {record['question']}\n\nEXPECTED ANSWER:\n{_points(key_points)}\n\n"
        f"ANSWER: {text}\n\n"
        "Ignoring its reasons, does the ANSWER reach the same overall conclusion as the "
        "EXPECTED ANSWER? Answer yes or no.",
        ["yes", "no"],
    )
    return verdict("partial" if same == "yes" else "wrong")


def source_for(conn, notes, record: dict, key_points: dict | None) -> str:
    """What sentences are checked against: the answer key's points, the reference
    note, and what the assistant was shown — capped so every check stays small."""
    parts = []
    if key_points:
        parts.append(f"KEY POINTS:\n{_points(key_points)}")
    if record["answerable"]:
        parts.append(f"REFERENCE:\n{reference_for(conn, notes, record)[:MAX_REFERENCE_CHARS]}")
    parts.append(f"WHAT THE ASSISTANT WAS SHOWN:\n{context_for(conn, notes, record)[:MAX_CONTEXT_CHARS]}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------


def grade_run(checker: Checker, name: str, force: bool = False) -> None:
    grader_id = f"{checker.backend.name}|{VERSION}"
    records = load_records(name)
    dataset = {q["id"]: q for q in load_dataset()}
    notes = ObsidianVaultProvider(config.vault_path)
    todo = [r for r in records if force or grader_id not in r.get("graders", {})]
    with connect() as conn:
        conn.row_factory = dict_row
        for n, record in enumerate(todo, start=1):
            key_points = dataset[record["id"]].get("key_points")
            started = time.time()
            verdict = grade_answer(checker, record, key_points, source_for(conn, notes, record, key_points))
            record.setdefault("graders", {})[grader_id] = {
                "grade": verdict.grade,
                "min_confidence": verdict.min_confidence,
                "checks": verdict.checks,
                "date": date.today().isoformat(),
            }
            save_records(name, records)
            print(f"  [{n}/{len(todo)}] {record['id']}  {verdict.grade:12} "
                  f"{time.time() - started:5.1f}s  (key: {record.get('grade')})")


def validate(backend_spec: str, runs: list[str], budget_tokens: int | None) -> int:
    backend = make_backend(backend_spec)
    checker = Checker(backend, CheckCache(), budget_tokens=budget_tokens)
    grader_id = f"{backend.name}|{VERSION}"
    started = time.time()
    for run in runs:
        print(f"\n{run}")
        grade_run(checker, run)
    minutes = (time.time() - started) / 60

    rows = [(run, r) for run in runs for r in load_records(run)
            if r.get("grade") and grader_id in r.get("graders", {})]
    key = [r["grade"] for _, r in rows]
    got = [r["graders"][grader_id]["grade"] for _, r in rows]
    n = len(rows)
    exact = sum(k == g for k, g in zip(key, got)) / n
    binary = [(is_correct(r, r["grade"]), is_correct(r, r["graders"][grader_id]["grade"])) for _, r in rows]
    from evals.judge import agreement

    stats = agreement(binary)
    print(f"\n{'═' * 74}\n{grader_id} against answer key v1 ({n} answers)\n{'═' * 74}")
    print(f"exact grade agreement      {exact:.1%}")
    print(f"correct-vs-not agreement   {stats['agree']:.1%}   kappa {stats['kappa']}")
    print(f"model calls {checker.calls}, answered from cache {checker.cached}, tokens {checker.tokens}, "
          f"{minutes:.1f} min")

    print("\nper run (a model can be lenient with its own family's answers):")
    for run in runs:
        pairs = [(r["grade"], r["graders"][grader_id]["grade"]) for rr, r in rows if rr == run]
        print(f"  {run:28} exact {sum(a == b for a, b in pairs)}/{len(pairs)}")

    print("\nconfusion (answer key -> grader), mismatches only:")
    for (k, g), count in Counter((k, g) for k, g in zip(key, got) if k != g).most_common():
        print(f"  {k:12} -> {g:12} x{count}")

    print("\nknown failures (must all be caught):")
    caught = 0
    for run, qid, expected in KNOWN_FAILURES:
        record = next((r for r in load_records(run) if r["id"] == qid), None)
        grade = record["graders"].get(grader_id, {}).get("grade") if record else None
        ok = grade == expected
        caught += ok
        print(f"  {'OK  ' if ok else 'MISS'} {run} {qid}: expected {expected}, got {grade}")

    result = {
        "date": date.today().isoformat(), "grader": grader_id, "runs": runs, "n": n,
        "exact": round(exact, 3), **stats, "known_failures_caught": f"{caught}/{len(KNOWN_FAILURES)}",
        "model_calls": checker.calls, "cached": checker.cached, "tokens": checker.tokens,
        "minutes": round(minutes, 1),
    }
    data = json.loads(BASELINES.read_text(encoding="utf-8"))
    data.setdefault("grader_v2_validation", []).append(result)
    BASELINES.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate", help="grade the answer-key runs and compare")
    v.add_argument("--backend", required=True, help="ollama:<model> or openai:<model>")
    v.add_argument("--runs", nargs="*", default=VALIDATION_RUNS)
    v.add_argument("--budget-tokens", type=int, help="stop before exceeding this many tokens")
    g = sub.add_parser("grade", help="grade one run")
    g.add_argument("name")
    g.add_argument("--backend", required=True)
    g.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "validate":
            return validate(args.backend, args.runs, args.budget_tokens)
        checker = Checker(make_backend(args.backend), CheckCache())
        grade_run(checker, args.name, args.force)
        return 0
    except GraderUnavailable as exc:
        print(f"Grader unavailable: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
