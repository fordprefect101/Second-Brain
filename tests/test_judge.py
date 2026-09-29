"""The AI grader (ADR-014), with OpenAI never called.

Pinned: where the key comes from and that it never appears in an error; that AI
grades never overwrite a person's; what the grader is shown for a trap; and the
agreement maths that decides whether the grader is trusted.
"""

from __future__ import annotations

import pytest

from evals import judge

KEY = "sk-test-DO-NOT-LEAK-1234"


def trap_record(**extra):
    return {
        "id": "q030",
        "question": "what is the wifi password at the office?",
        "answerable": False,
        "expected": [],
        "answer": "I don't have anything on that.",
        "sources": [],
        "calls": [{"tool_calls": []}],
        "context": "[1] NOTE — LEARNING\n    some text",
        "grade": "correct",
        "grade_note": "",
        **extra,
    }


def test_the_key_comes_from_the_keychain_before_the_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    monkeypatch.setattr(judge, "load_secret", lambda name: "from-keychain")
    assert judge.api_key() == "from-keychain"

    monkeypatch.setattr(judge, "load_secret", lambda name: None)
    assert judge.api_key() == "from-env"

    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(judge.GraderUnavailable):
        judge.api_key()


def test_ai_grades_never_overwrite_a_persons(monkeypatch):
    seen = {}

    def fake_call(key, messages):
        seen["prompt"] = messages[1]["content"]
        return {"reasoning": "Clean refusal on a trap.", "grade": "correct"}, {"prompt_tokens": 10}

    monkeypatch.setattr(judge, "_call", fake_call)
    record = trap_record(grade="refused")

    judge.grade_record(conn=None, notes=None, key=KEY, record=record)

    assert record["grade"] == "refused"  # the person's grade, untouched
    assert record["ai_grade"] == "correct"
    assert record["ai_model"] == judge.MODEL
    # What a trap looks like to the grader: no reference, and the recorded context.
    assert "ANSWERABLE: no" in seen["prompt"]
    assert "it is a trap question" in seen["prompt"]
    assert "NOTE — LEARNING" in seen["prompt"]


class FakeResponse:
    def __init__(self, status: int, text: str):
        self.status_code, self.text = status, text


def test_a_rejected_key_is_never_echoed(monkeypatch):
    """OpenAI's 401 body can include part of the key; the error must not."""
    monkeypatch.setattr(
        judge.httpx, "post",
        lambda *a, **k: FakeResponse(401, f"Incorrect API key provided: {KEY}"),
    )

    with pytest.raises(judge.GraderUnavailable) as err:
        judge._call(KEY, [])

    assert KEY not in str(err.value) and "1234" not in str(err.value)


def test_no_quota_fails_at_once_instead_of_retrying(monkeypatch):
    calls = []
    monkeypatch.setattr(
        judge.httpx, "post",
        lambda *a, **k: calls.append(1) or FakeResponse(429, '{"code": "insufficient_quota"}'),
    )

    with pytest.raises(judge.GraderUnavailable, match="quota"):
        judge._call(KEY, [])
    assert len(calls) == 1


def test_a_rate_limit_waits_as_long_as_openai_says(monkeypatch):
    """gpt-4.1 on a low account tier hits per-minute limits fast; the stated wait works."""
    replies = [
        FakeResponse(429, "rate limited"),
        FakeResponse(200, ""),
    ]
    replies[0].headers = {"retry-after": "12"}
    replies[1].json = lambda: {
        "choices": [{"message": {"content": '{"reasoning": "r", "grade": "correct"}'}}],
        "usage": {},
    }
    slept = []
    monkeypatch.setattr(judge.httpx, "post", lambda *a, **k: replies.pop(0))
    monkeypatch.setattr(judge.time, "sleep", slept.append)

    verdict, _ = judge._call(KEY, [])

    assert verdict["grade"] == "correct"
    assert slept == [12.5]


def test_on_a_trap_refusing_is_correct():
    answerable, trap = {"answerable": True}, {"answerable": False}
    assert judge.is_correct(answerable, "correct")
    assert not judge.is_correct(answerable, "refused")
    assert judge.is_correct(trap, "refused") and judge.is_correct(trap, "correct")
    assert not judge.is_correct(trap, "hallucinated")


def test_agreement_and_kappa():
    # 10 answers: agree on 8. Kappa discounts the agreement expected by chance.
    pairs = [(True, True)] * 5 + [(False, False)] * 3 + [(True, False), (False, True)]

    stats = judge.agreement(pairs)

    assert stats == {"n": 10, "agree": 0.8, "kappa": 0.583}
    assert judge.agreement([]) == {"n": 0, "agree": None, "kappa": None}
