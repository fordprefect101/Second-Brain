"""Grader v2's combining rules and cache, with a fake model answering the checks.

These are the answer key's rules (evals/dataset.jsonl, corrections of 2026-09-29):
every required point for "correct", some for "partial", the right conclusion without
the reasons is "partial" (rule A), any unsupported sentence is "hallucinated" (rule C).
"""

from __future__ import annotations

import pytest

from evals.grader_v2 import CheckCache, Checker, grade_answer, sentences
from evals.judge import GraderUnavailable

KEY_POINTS = {"must": ["Point one", "Point two"], "also": ["A bonus"]}


class FakeModel:
    """Answers each kind of check from a script; counts calls."""

    name = "fake"

    def __init__(self, supported="yes", refusal="no", points=("yes", "yes"), conclusion="no", different="no"):
        self.supported, self.refusal, self.conclusion = supported, refusal, conclusion
        self.different = different
        self.points = dict(zip(KEY_POINTS["must"], points))
        self.calls = 0

    def ask(self, prompt, options):
        self.calls += 1
        if "SENTENCE:" in prompt:
            answer = self.supported
        elif "is not available" in prompt:
            answer = self.refusal
        elif "mainly describe different things" in prompt:
            answer = self.different
        elif "POINT:" in prompt:
            answer = next(v for k, v in self.points.items() if f"POINT: {k}" in prompt)
        else:
            answer = self.conclusion
        return answer, None, 10


def grade(model, answer="The answer is point one and point two.", answerable=True, tmp_path=None, cache="auto"):
    # A fresh cache per call unless a test shares one on purpose: identical checks
    # in one cache are answered from it, which is the point of the cache.
    name = f"{id(model)}-{answer}-{answerable}.jsonl" if cache == "auto" else cache
    checker = Checker(model, CheckCache(tmp_path / name.replace("/", "_")))
    record = {"question": "why?", "answer": answer, "answerable": answerable}
    return grade_answer(checker, record, KEY_POINTS if answerable else None, "source").grade


def test_malformed_is_caught_by_code_without_asking_the_model(tmp_path):
    model = FakeModel()
    assert grade(model, answer='{"name": "search", "parameters": {}}', tmp_path=tmp_path) == "malformed"
    assert model.calls == 0


def test_one_unsupported_sentence_makes_it_hallucinated(tmp_path):
    assert grade(FakeModel(supported="no"), tmp_path=tmp_path) == "hallucinated"


def test_every_required_point_makes_it_correct(tmp_path):
    assert grade(FakeModel(points=("yes", "yes")), tmp_path=tmp_path) == "correct"


def test_some_required_points_make_it_partial(tmp_path):
    """Rule B: all but one required point is still partial."""
    assert grade(FakeModel(points=("yes", "no")), tmp_path=tmp_path) == "partial"
    assert grade(FakeModel(points=("partly", "no")), tmp_path=tmp_path) == "partial"


def test_right_conclusion_without_the_reasons_is_partial(tmp_path):
    """Rule A: no required point, nothing false, but the right conclusion."""
    assert grade(FakeModel(points=("no", "no"), conclusion="yes"), tmp_path=tmp_path) == "partial"
    assert grade(FakeModel(points=("no", "no"), conclusion="no"), tmp_path=tmp_path) == "wrong"


def test_an_answer_about_different_things_is_wrong_despite_a_shared_point(tmp_path):
    """q005: a different list of protections that happens to include a confirmation."""
    assert grade(FakeModel(points=("yes", "no"), different="yes"), tmp_path=tmp_path) == "wrong"


def test_refusals_depend_on_whether_the_notes_answer_it(tmp_path):
    refusal = "I don't have anything on that."
    assert grade(FakeModel(refusal="yes"), answer=refusal, tmp_path=tmp_path) == "refused"
    assert grade(FakeModel(refusal="yes"), answer=refusal, answerable=False, tmp_path=tmp_path) == "correct"
    assert grade(FakeModel(refusal="no"), answer="It was RabbitMQ.", answerable=False, tmp_path=tmp_path) == "wrong"


def test_an_invented_claim_beside_a_refusal_is_hallucinated(tmp_path):
    """q010: hedging does not excuse the invented decision."""
    answer = "It is not mentioned. However, RabbitMQ was chosen for the queue."
    assert grade(FakeModel(supported="no", refusal="yes"), answer=answer, answerable=False, tmp_path=tmp_path) == "hallucinated"


def test_an_unchanged_answer_is_never_checked_twice(tmp_path):
    model = FakeModel()
    grade(model, tmp_path=tmp_path, cache="shared.jsonl")
    first = model.calls
    grade(model, tmp_path=tmp_path, cache="shared.jsonl")  # same cache, same answer

    assert first > 0 and model.calls == first


def test_the_token_budget_stops_before_overspending(tmp_path):
    checker = Checker(FakeModel(), CheckCache(tmp_path / "cache.jsonl"), budget_tokens=15)
    record = {"question": "why?", "answer": "One sentence here. Another sentence here. A third one here.", "answerable": True}

    with pytest.raises(GraderUnavailable, match="budget"):
        grade_answer(checker, record, KEY_POINTS, "source")


def test_sentences_are_split_and_bullets_stripped():
    text = "According to ADR-008, no.\n1. Adopt when it saves weeks.\n- Build when short. Ok."
    assert sentences(text) == ["According to ADR-008, no.", "Adopt when it saves weeks.", "Build when short."]
    assert sentences("Demucs or UVR.") == ["Demucs or UVR."]  # a short answer is kept
