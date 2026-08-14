"""Similarity scores and the threshold compare at 2-decimal precision.

A candidate scoring 0.597 against a 0.6 threshold was dropped for a
0.003 shortfall — a difference far below the meaningful resolution of a
cosine similarity between two short multilingual strings. Both sides of
the comparison are now rounded to 2 decimals (half-up) so the gate is
decided at the precision the numbers actually carry.

Half-up matters here: this is the rule the operator asked for, and
Python's built-in ``round`` uses banker's rounding, which would send an
exact .xx5 the other way.
"""

import pytest

from local_deep_research.images.semantic_matcher import (
    SCORE_PRECISION,
    round_score,
)


def test_precision_is_two_decimals():
    assert SCORE_PRECISION == 2


@pytest.mark.parametrize(
    "raw,expected",
    [
        # The reported case: 0.597 must reach the 0.60 gate.
        (0.597, 0.60),
        (0.594, 0.59),
        # Exact half rounds up, not to even (0.595 -> 0.60, not 0.59).
        (0.595, 0.60),
        (0.585, 0.59),
        # Threshold itself is unchanged by rounding.
        (0.6, 0.6),
        (1.0, 1.0),
        (0.0, 0.0),
        # Values already at precision are untouched.
        (0.27, 0.27),
    ],
)
def test_round_score_half_up(raw, expected):
    assert round_score(raw) == pytest.approx(expected)


def test_round_score_accepts_int_and_str_free_floats():
    assert round_score(1) == 1.0
    assert isinstance(round_score(0.5), float)


def test_reported_case_now_clears_the_gate():
    """The peapix image scored 0.597 against threshold 0.6 and was
    dropped. At 2-decimal precision the comparison flips to keep.
    """
    raw_score = 0.597
    threshold = 0.6

    assert raw_score < threshold, "precondition: raw comparison drops it"
    assert round_score(raw_score) >= round_score(threshold)


def test_genuinely_low_scores_still_dropped():
    """Rounding must not become a blanket relaxation — the median
    rejected score in the observed run was 0.275.
    """
    for raw in (0.275, 0.5, 0.58, 0.594):
        assert round_score(raw) < round_score(0.6)
