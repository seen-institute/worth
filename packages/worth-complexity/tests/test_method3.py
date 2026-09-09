"""Method 3: band widening, the median, the multiplier, the seeded interval."""

from __future__ import annotations

from decimal import Decimal

import pytest
from worth_complexity.method3 import ComparatorInput, band_expected, bootstrap_median
from worth_complexity.models import ComplexityScore, NoReferenceCurveError


def _comparators(scores: range) -> tuple[ComparatorInput, ...]:
    """One comparator per score in ``scores``, amount = 1000 + score, dollars,
    specialty alternating ortho (even score) / gyn (odd score)."""
    return tuple(
        (
            f"E{score}",
            "58660",
            "ortho" if score % 2 == 0 else "gyn",
            score,
            Decimal(1000 + score),
        )
        for score in scores
    )


COMPARATORS = _comparators(range(40, 70))  # scores 40..69, 30 encounters
MULTIPLIER = Decimal("1.5")


def test_band_widens_until_the_floor_is_reached() -> None:
    """Score 50, half_width 3 -> [47,53] holds 7; widens to half_width 5 ->
    [45,55] holds exactly 11, the default floor."""
    m3 = band_expected("STUDY1", ComplexityScore(50), COMPARATORS, MULTIPLIER, seed=1)
    assert m3.band.low == 45
    assert m3.band.high == 55
    assert m3.band.n == 11
    assert m3.band.widened is True
    assert m3.band.half_width == 5


def test_schedule_median_is_the_band_medians_amount() -> None:
    """Scores 45..55, amounts 1045..1055: the median of 11 sorted values is
    the middle one, 1050."""
    m3 = band_expected("STUDY1", ComplexityScore(50), COMPARATORS, MULTIPLIER, seed=1)
    assert m3.schedule_median == Decimal("1050")


def test_expected_is_multiplier_times_schedule_median() -> None:
    m3 = band_expected("STUDY1", ComplexityScore(50), COMPARATORS, MULTIPLIER, seed=1)
    assert m3.expected == Decimal("1575.00")


def test_specialty_medians_are_reported_in_payer_dollars() -> None:
    m3 = band_expected("STUDY1", ComplexityScore(50), COMPARATORS, MULTIPLIER, seed=1)
    assert m3.specialty_medians == {"gyn": Decimal("1575.00"), "ortho": Decimal("1575.00")}
    assert set(m3.specialty_medians) == {"gyn", "ortho"}


def test_interval_is_deterministic_across_runs() -> None:
    first = band_expected("STUDY1", ComplexityScore(50), COMPARATORS, MULTIPLIER, seed=7)
    second = band_expected("STUDY1", ComplexityScore(50), COMPARATORS, MULTIPLIER, seed=7)
    assert first.interval == second.interval
    assert first.interval.low <= first.expected <= first.interval.high


def test_different_seeds_can_move_the_interval() -> None:
    a = band_expected("STUDY1", ComplexityScore(50), COMPARATORS, MULTIPLIER, seed=1)
    b = band_expected("STUDY1", ComplexityScore(50), COMPARATORS, MULTIPLIER, seed=2)
    # Both are valid 95% intervals around the same point estimate; they need
    # not be identical, but both must bracket the point estimate.
    assert a.interval.low <= a.expected <= a.interval.high
    assert b.interval.low <= b.expected <= b.interval.high


def test_a_band_that_never_reaches_the_floor_raises() -> None:
    """Only 30 comparators exist in total; a floor of 50 can never be met
    even at the widest possible band, [0, 100]."""
    with pytest.raises(NoReferenceCurveError, match="No Method 3 reference"):
        band_expected("STUDY1", ComplexityScore(50), COMPARATORS, MULTIPLIER, floor=50, seed=1)


def test_a_band_at_the_edge_of_the_scale_still_widens_correctly() -> None:
    """Score 0: the band cannot go below 0, so it only ever widens upward."""
    comparators = _comparators(range(0, 30))
    m3 = band_expected("STUDY0", ComplexityScore(0), comparators, MULTIPLIER, seed=1)
    assert m3.band.low == 0
    assert m3.band.n >= 11


def test_bootstrap_median_is_seeded_and_deterministic() -> None:
    amounts = tuple(Decimal(100 + i) for i in range(20))
    first = bootstrap_median(amounts, Decimal("2"), seed=42)
    second = bootstrap_median(amounts, Decimal("2"), seed=42)
    assert first == second
    assert first.method == "bootstrap"


def test_bootstrap_median_needs_at_least_one_observation() -> None:
    with pytest.raises(ValueError, match="at least one"):
        bootstrap_median((), Decimal("1"), seed=1)


def test_trace_shows_the_working() -> None:
    m3 = band_expected("STUDY1", ComplexityScore(50), COMPARATORS, MULTIPLIER, seed=1)
    body = "\n".join(m3.trace)
    assert "band" in body
    assert "schedule median" in body
    assert "multiplier" in body
