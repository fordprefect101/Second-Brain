"""Search scores, kept apart from the eval runner so they are small and testable.

A question's rank is the position of the first acceptable result in the top k,
counted from 1, or None when none appeared.
"""

from __future__ import annotations


def first_hit_rank(ranked: list, expected: set) -> int | None:
    """Position (starting at 1) of the first item in `ranked` that is in `expected`,
    or None if none is. Only the first counts: a second acceptable note lower down
    does not change the question's rank."""
    for position, item in enumerate(ranked, start=1):
        if item in expected:
            return position
    return None


def recall_at_k(ranks: list[int | None]) -> float:
    """Share of questions that were found at all. 0.0 if there are none."""
    if not ranks:
        return 0.0
    return sum(rank is not None for rank in ranks) / len(ranks)


def mrr(ranks: list[int | None]) -> float:
    """Mean reciprocal rank: the average of 1/rank, a miss counting 0. Rewards #1
    over #5, which recall cannot see. 0.0 if there are none."""
    if not ranks:
        return 0.0
    return sum(1 / rank for rank in ranks if rank is not None) / len(ranks)
