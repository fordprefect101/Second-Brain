"""The search scores, on the worked example: found at #1, #2, #5, and one miss."""

from __future__ import annotations

import pytest

from evals.metrics import first_hit_rank, mrr, recall_at_k

RANKS = [1, 2, 5, None]


def test_recall_counts_every_question_found_anywhere_in_the_top_k():
    assert recall_at_k(RANKS) == 0.75


def test_mrr_rewards_a_higher_position():
    assert mrr(RANKS) == pytest.approx((1 + 1 / 2 + 1 / 5 + 0) / 4)  # 0.425


def test_ranks_count_from_one():
    assert first_hit_rank(["a", "b", "c"], {"a"}) == 1


def test_only_the_first_acceptable_result_counts():
    """q022 accepts four notes; two in the top 5 still means one rank, the higher."""
    assert first_hit_rank(["a", "b", "c"], {"c", "b"}) == 2


def test_a_miss_has_no_rank():
    assert first_hit_rank(["a", "b"], {"z"}) is None


def test_no_questions_scores_zero_rather_than_crashing():
    assert recall_at_k([]) == 0.0
    assert mrr([]) == 0.0
