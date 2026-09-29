"""The answer eval's automatic checks and its failure split.

The model is never called here. What is pinned: that today's real failure — a tool
call written out as text — is caught, that refusals are recognised, and that a
wrong answer is blamed on the right half (retrieval vs generation).
"""

from __future__ import annotations

from evals.run_answers import check, summarize


def test_a_tool_call_written_as_text_is_malformed():
    """The actual answer to q006 on 2026-09-28."""
    text = '{"name": "search", "parameters": {"query": "why not just use the file name"}}'

    assert check(text) == {"malformed": "tool call written as text", "refused": False}


def test_empty_and_given_up_answers_are_malformed():
    assert check("   ")["malformed"] == "empty"
    assert check("I could not settle on an answer — the model kept asking")["malformed"]


def test_refusals_are_recognised():
    for text in [
        "I don't have anything on that.",
        "The search results do not mention RabbitMQ.",
        "There is no information about a wifi password.",
    ]:
        assert check(text) == {"malformed": None, "refused": True}, text


def test_a_normal_answer_is_neither():
    assert check("You would use Demucs for source separation.") == {
        "malformed": None,
        "refused": False,
    }


def record(qid, *, answerable=True, found=True, grade=None, refused=False, tags=()):
    return {
        "id": qid,
        "answerable": answerable,
        "retrieval_found": found if answerable else None,
        "tags": list(tags),
        "seconds": 10.0,
        "calls": [{"prompt_tokens": 1500, "load_seconds": 0.0}],
        "checks": {"malformed": None, "refused": refused},
        "grade": grade,
    }


def test_wrong_answers_are_blamed_on_the_right_half():
    records = [
        record("a", grade="correct"),
        record("b", found=True, grade="hallucinated"),  # had the note, said wrong things
        record("c", found=False, grade="wrong"),  # never saw the note
        record("t", answerable=False, grade="refused", refused=True),
    ]

    s = summarize(records)

    assert s["correct"] == 1
    assert s["generation_failures"] == 1
    assert s["retrieval_failures"] == 1
    assert s["traps_correct"] == 1


def test_a_trap_refusal_graded_correct_counts_as_handled():
    """Refusing IS the right answer to a trap, so "correct" and "refused" both count."""
    records = [
        record("t1", answerable=False, grade="correct", refused=True),
        record("t2", answerable=False, grade="refused", refused=True),
        record("t3", answerable=False, grade="hallucinated"),
    ]

    assert summarize(records)["traps_correct"] == 2


def test_refusing_when_the_note_was_there_is_counted():
    s = summarize([record("a", found=True, refused=True), record("b", found=False, refused=True)])

    assert s["refused_despite_retrieval"] == 1
