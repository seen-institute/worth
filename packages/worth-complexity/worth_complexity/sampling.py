"""Distributions and intervals, in Decimal, standard library only.

Two things the methodology asks for and the engine could not previously
produce.

**The empirical reference distribution.** "The empirical reference distribution
is a measurement of what the work actually looks like across thousands of
encounters", one of the two outputs the notes call defensible from the first
validated extract. It is where the worked example's "the reference distribution
for 58662 across the registry population places the median at 32/100" comes
from, and Method 2 has nothing to compare a case against without it.

**Confidence intervals.** Every published index value "carries what is needed to
read it: the code, the institution, the time window, the number of encounters
behind it, the complexity distribution it was drawn from, and a confidence
interval." A point estimate with no interval reads as more certain than the
cohort behind it can support, which is the error a referee is quickest to find.

No scipy. The Student's t critical values are a published table, read
conservatively; the ratio interval is a seeded percentile bootstrap, so it is
reproducible rather than merely repeatable.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import Decimal

from worth_complexity.money import money_context

# Two-sided 95% Student's t. Standard published values; the lookup takes the
# largest tabulated df not exceeding the real one, which widens the interval
# rather than narrowing it.
_T_95: tuple[tuple[int, str], ...] = (
    (1, "12.706"),
    (2, "4.303"),
    (3, "3.182"),
    (4, "2.776"),
    (5, "2.571"),
    (6, "2.447"),
    (7, "2.365"),
    (8, "2.306"),
    (9, "2.262"),
    (10, "2.228"),
    (11, "2.201"),
    (12, "2.179"),
    (13, "2.160"),
    (14, "2.145"),
    (15, "2.131"),
    (16, "2.120"),
    (17, "2.110"),
    (18, "2.101"),
    (19, "2.093"),
    (20, "2.086"),
    (21, "2.080"),
    (22, "2.074"),
    (23, "2.069"),
    (24, "2.064"),
    (25, "2.060"),
    (26, "2.056"),
    (27, "2.052"),
    (28, "2.048"),
    (29, "2.045"),
    (30, "2.042"),
    (40, "2.021"),
    (50, "2.009"),
    (60, "2.000"),
    (80, "1.990"),
    (100, "1.984"),
    (120, "1.980"),
)
_T_INFINITY = Decimal("1.960")

BOOTSTRAP_DRAWS = 2000
"""Resamples behind a ratio interval. Fixed, so two runs agree exactly."""


def t_critical(df: int) -> Decimal:
    """Two-sided 95% critical value, rounded conservatively."""
    if df < 1:
        msg = "no interval is defined below one degree of freedom"
        raise ValueError(msg)
    chosen = _T_INFINITY
    for tabulated, value in _T_95:
        if df >= tabulated:
            chosen = Decimal(value)
    return chosen


def quantile(values: list[Decimal], p: str) -> Decimal:
    """Linear-interpolation quantile over a sorted list.

    One published definition, applied everywhere, because two quantile
    conventions disagreeing by a quartile is exactly the kind of difference
    nobody notices until someone else fails to reproduce the number.
    """
    if not values:
        msg = "no values to take a quantile of"
        raise ValueError(msg)
    with money_context():
        position = Decimal(p) * (Decimal(len(values)) - 1)
        low = int(position)
        high = min(low + 1, len(values) - 1)
        weight = position - low
        return values[low] + (values[high] - values[low]) * weight


@dataclass(frozen=True, slots=True)
class Distribution:
    """What the work looks like, for one code, across a cohort."""

    n: int
    minimum: Decimal
    q1: Decimal
    median: Decimal
    q3: Decimal
    maximum: Decimal

    def render(self) -> str:
        return (
            f"n={self.n}  median {self.median:.0f}  "
            f"IQR {self.q1:.0f}-{self.q3:.0f}  range {self.minimum:.0f}-{self.maximum:.0f}"
        )


def describe(values: tuple[Decimal, ...]) -> Distribution:
    """The empirical reference distribution of a set of complexity scores."""
    ordered = sorted(values)
    return Distribution(
        n=len(ordered),
        minimum=ordered[0],
        q1=quantile(ordered, "0.25"),
        median=quantile(ordered, "0.5"),
        q3=quantile(ordered, "0.75"),
        maximum=ordered[-1],
    )


@dataclass(frozen=True, slots=True)
class Interval:
    """A two-sided 95% interval."""

    low: Decimal
    high: Decimal
    method: str
    """``t`` or ``bootstrap``. Part of the answer, not a footnote."""

    def render(self, places: int = 4) -> str:
        return f"[{self.low:.{places}f}, {self.high:.{places}f}]"


def t_interval(estimate: Decimal, standard_error: Decimal, df: int) -> Interval:
    """A normal-theory interval around a least-squares estimate."""
    with money_context():
        half = t_critical(df) * standard_error
        return Interval(estimate - half, estimate + half, "t")


def bootstrap_ratio(
    pairs: tuple[tuple[Decimal, Decimal], ...],
    *,
    seed: int,
    draws: int = BOOTSTRAP_DRAWS,
) -> Interval:
    """Percentile bootstrap for a ratio of sums.

    The index is a ratio of two totals, not a mean, so its sampling distribution
    is not the textbook one and a normal-theory interval would be wrong in a way
    that is hard to see. Resampling encounters with replacement makes no
    distributional assumption at all.

    The seed is derived from the stratum rather than from the clock, so the
    interval published for a code in November is the interval anyone re-deriving
    it gets in 2031.
    """
    if len(pairs) < 2:
        msg = "a bootstrap needs at least two observations"
        raise ValueError(msg)

    rng = random.Random(seed)
    size = len(pairs)
    ratios: list[Decimal] = []
    with money_context():
        for _ in range(draws):
            numerator = Decimal(0)
            denominator = Decimal(0)
            for _ in range(size):
                realized, expected = pairs[rng.randrange(size)]
                numerator += realized
                denominator += expected
            if denominator > 0:
                ratios.append(numerator / denominator)
        ratios.sort()
        return Interval(
            quantile(ratios, "0.025").quantize(Decimal("0.0001")),
            quantile(ratios, "0.975").quantize(Decimal("0.0001")),
            "bootstrap",
        )
