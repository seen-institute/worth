"""Least squares, and its refusals."""

from __future__ import annotations

from decimal import Decimal

import pytest
from worth_complexity import ExtrapolationError, NoReferenceCurveError, fit


def points(pairs: list[tuple[int, float]]) -> tuple[tuple[Decimal, Decimal], ...]:
    return tuple((Decimal(x), Decimal(y)) for x, y in pairs)


PERFECT = points([(x, 10 + 2 * x) for x in range(10, 90, 8)])


def test_a_perfect_line_is_recovered_exactly() -> None:
    f = fit("t", PERFECT)
    assert f.slope == Decimal(2)
    assert f.intercept == Decimal(10)
    assert f.r_squared == Decimal(1)


def test_a_flat_relationship_yields_a_zero_slope() -> None:
    """Method 0's finding: the code does not differentiate."""
    f = fit("t", points([(x, 3894.20) for x in range(10, 90, 8)]))
    assert f.slope == 0


def test_too_few_points_is_refused() -> None:
    """A line through too few points is not a reference standard."""
    with pytest.raises(NoReferenceCurveError, match="at least"):
        fit("t", PERFECT[:4])


def test_a_cohort_with_no_complexity_variance_is_refused() -> None:
    with pytest.raises(NoReferenceCurveError, match="same complexity score"):
        fit("t", points([(50, y) for y in range(100, 900, 80)]))


def test_prediction_inside_the_fitted_range_is_allowed() -> None:
    assert fit("t", PERFECT).predict(Decimal(50)) == Decimal("110.00")


def test_prediction_outside_the_fitted_range_is_refused_by_default() -> None:
    """Outside the observed range the curve is a guess, and a guess presented as
    an expected payment is what a hostile referee is paid to find."""
    with pytest.raises(ExtrapolationError, match="outside the fitted range"):
        fit("t", PERFECT).predict(Decimal(99))


def test_extrapolation_is_possible_but_must_be_asked_for() -> None:
    assert fit("t", PERFECT).predict(Decimal(99), allow_extrapolation=True) == Decimal("208.00")


def test_the_trace_shows_the_working() -> None:
    body = "\n".join(fit("t", PERFECT).trace)
    assert "Sxy" in body and "slope" in body and "R2" in body
