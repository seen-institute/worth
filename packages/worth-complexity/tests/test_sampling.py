"""Distributions and intervals."""

from __future__ import annotations

from decimal import Decimal

import pytest
from worth_complexity.sampling import (
    bootstrap_ratio,
    describe,
    quantile,
    t_critical,
    t_interval,
)


def decimals(*values: int | str) -> tuple[Decimal, ...]:
    return tuple(Decimal(str(v)) for v in values)


# ------------------------------------------------------------------ quantiles


def test_the_median_of_an_odd_sample_is_the_middle_value() -> None:
    assert quantile(list(decimals(1, 5, 9)), "0.5") == Decimal(5)


def test_the_median_of_an_even_sample_interpolates() -> None:
    assert quantile(list(decimals(1, 5, 9, 13)), "0.5") == Decimal(7)


def test_the_extremes_are_the_extremes() -> None:
    values = list(decimals(3, 7, 11))
    assert quantile(values, "0") == Decimal(3)
    assert quantile(values, "1") == Decimal(11)


def test_a_quantile_of_nothing_is_refused() -> None:
    with pytest.raises(ValueError, match="no values"):
        quantile([], "0.5")


def test_a_distribution_reports_what_a_published_record_needs() -> None:
    d = describe(decimals(1, 2, 3, 4, 5, 6, 7, 8, 9))
    assert (d.n, d.minimum, d.median, d.maximum) == (9, Decimal(1), Decimal(5), Decimal(9))
    assert d.q1 < d.median < d.q3


# ------------------------------------------------------------------ intervals


def test_the_t_table_is_read_conservatively() -> None:
    """An untabulated df takes the next value down, which widens the interval."""
    assert t_critical(45) == t_critical(40)
    assert t_critical(1000) == Decimal("1.980")


def test_the_critical_value_shrinks_as_the_sample_grows() -> None:
    assert t_critical(2) > t_critical(10) > t_critical(60)


def test_no_interval_exists_without_a_degree_of_freedom() -> None:
    with pytest.raises(ValueError, match="degree of freedom"):
        t_critical(0)


def test_a_t_interval_is_symmetric_about_its_estimate() -> None:
    i = t_interval(Decimal(10), Decimal(2), 30)
    assert i.low < Decimal(10) < i.high
    assert (Decimal(10) - i.low) == (i.high - Decimal(10))


def test_a_zero_standard_error_gives_a_point_interval() -> None:
    i = t_interval(Decimal("0.7"), Decimal(0), 30)
    assert i.low == i.high == Decimal("0.7")


# ----------------------------------------------------------------- bootstrap


def test_a_constant_ratio_bootstraps_to_itself() -> None:
    pairs = tuple((Decimal(7), Decimal(10)) for _ in range(40))
    i = bootstrap_ratio(pairs, seed=1)
    assert i.low == i.high == Decimal("0.7000")


def test_the_bootstrap_is_reproducible() -> None:
    """A published interval has to be re-derivable, not merely repeatable."""
    pairs = tuple((Decimal(r), Decimal(10)) for r in range(1, 41))
    assert bootstrap_ratio(pairs, seed=58662) == bootstrap_ratio(pairs, seed=58662)


def test_a_different_stratum_gets_a_different_resample() -> None:
    pairs = tuple((Decimal(r), Decimal(10)) for r in range(1, 41))
    assert bootstrap_ratio(pairs, seed=1) != bootstrap_ratio(pairs, seed=2)


def test_the_interval_brackets_the_point_estimate() -> None:
    pairs = tuple((Decimal(r), Decimal(10)) for r in range(1, 41))
    total = sum(r for r, _ in pairs) / sum(e for _, e in pairs)
    i = bootstrap_ratio(pairs, seed=7)
    assert i.low <= total <= i.high


def test_one_observation_cannot_be_bootstrapped() -> None:
    with pytest.raises(ValueError, match="at least two"):
        bootstrap_ratio(((Decimal(1), Decimal(2)),), seed=1)
