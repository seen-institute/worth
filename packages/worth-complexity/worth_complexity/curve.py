"""Least squares, in Decimal, over the pinned context.

Two different things are fitted with the same machinery, and they should not be
confused.

**The Method 0 slope** is fitted *within* one code and one payer: does realized
payment move at all as documented complexity rises? It needs no reference
standard, which is why the methodology makes it the lead diagnostic. It is the
first evidence available for any code and the hardest to contest. A slope
indistinguishable from zero is itself the finding: the code does not
differentiate.

**The cross-specialty reference curve** is fitted *across* the comparator
cohort, specialties whose valuation is not in dispute, and answers a
different question: what does this payer already pay for this much measured
complexity, anywhere in medicine? That is what makes the expected payment an
observation rather than an assertion, and it is why the curve is fitted per
payer. Mixing payers would make the curve a measure of contract mix.

Standard library only. No numpy: results have to be bit-identical on ARM and
x86, and a fit anyone can re-do in a spreadsheet is worth more here than a fast
one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from worth_complexity.models import (
    ExtrapolationError,
    NoReferenceCurveError,
)
from worth_complexity.money import money_context
from worth_complexity.sampling import Interval, t_interval

MIN_OBSERVATIONS = 8
"""Below this, a fitted line is an anecdote with a slope.

Three degrees of freedom short of this and the t interval is so wide it carries
no information; the methodology's own framing, a slope reported with its
confidence interval, stops meaning anything before the arithmetic does.
"""


@dataclass(frozen=True, slots=True)
class Fit:
    """An ordinary least squares line, with the range it was fitted on."""

    curve_id: str
    n: int
    slope: Decimal
    slope_standard_error: Decimal
    slope_interval: Interval
    """Two-sided 95%. The methodology reports the slope with its interval, not alone."""
    intercept: Decimal
    r_squared: Decimal
    x_min: Decimal
    x_max: Decimal
    y_mean: Decimal
    trace: tuple[str, ...]

    def predict(self, x: Decimal, *, allow_extrapolation: bool = False) -> Decimal:
        """Expected value at ``x``, refusing to extrapolate by default."""
        if not allow_extrapolation and not (self.x_min <= x <= self.x_max):
            msg = (
                f"{self.curve_id}: complexity {x} lies outside the fitted range "
                f"[{self.x_min}, {self.x_max}]. The curve is an observation about what "
                "this payer already pays; outside the observed range it is a guess."
            )
            raise ExtrapolationError(msg)
        with money_context():
            return (self.intercept + self.slope * x).quantize(Decimal("0.01"))

    @property
    def differentiates(self) -> bool:
        """True when the interval excludes zero.

        A slope whose interval contains zero is a code that has not been shown to
        pay differently for harder work. Reading a small non-zero point estimate
        as evidence of anything is the mistake this property exists to prevent.
        """
        return not (self.slope_interval.low <= 0 <= self.slope_interval.high)

    def render(self) -> str:
        return (
            f"{self.curve_id}: expected = {self.intercept:.2f} + {self.slope:.4f} x score  "
            f"(n={self.n}, R2={self.r_squared:.4f}, 95% CI {self.slope_interval.render()}, "
            f"fitted on scores {self.x_min}-{self.x_max})"
        )


def fit(curve_id: str, points: tuple[tuple[Decimal, Decimal], ...]) -> Fit:
    """Fit ``y = intercept + slope * x`` by ordinary least squares."""
    n = len(points)
    if n < MIN_OBSERVATIONS:
        msg = (
            f"{curve_id}: {n} observations, need at least {MIN_OBSERVATIONS}. "
            "A line through too few points is not a reference standard."
        )
        raise NoReferenceCurveError(msg)

    with money_context():
        count = Decimal(n)
        xs = [x for x, _ in points]
        ys = [y for _, y in points]
        mean_x = sum(xs, start=Decimal(0)) / count
        mean_y = sum(ys, start=Decimal(0)) / count

        sxx = sum(((x - mean_x) * (x - mean_x) for x in xs), start=Decimal(0))
        sxy = sum(((x - mean_x) * (y - mean_y) for x, y in points), start=Decimal(0))
        if sxx == 0:
            msg = (
                f"{curve_id}: every observation has the same complexity score, so no "
                "slope is identifiable. This is a cohort problem, not an arithmetic one."
            )
            raise NoReferenceCurveError(msg)

        slope = sxy / sxx
        intercept = mean_y - slope * mean_x

        ss_res = sum(((y - (intercept + slope * x)) ** 2 for x, y in points), start=Decimal(0))
        ss_tot = sum(((y - mean_y) ** 2 for y in ys), start=Decimal(0))
        r_squared = Decimal(1) if ss_tot == 0 else Decimal(1) - ss_res / ss_tot

        # Residual standard error of the slope, and the interval around it.
        df = n - 2
        standard_error = ((ss_res / Decimal(df)) / sxx).sqrt()
        interval = t_interval(slope, standard_error, df)

        trace = (
            f"n                 = {n}",
            f"mean complexity   = {mean_x:.4f}",
            f"mean payment      = {mean_y:.4f}",
            f"Sxx               = {sxx:.4f}",
            f"Sxy               = {sxy:.4f}",
            f"slope    = Sxy/Sxx        = {slope:.6f}",
            f"intercept = ybar - slope*xbar = {intercept:.6f}",
            f"R2       = 1 - SSres/SStot = {r_squared:.6f}",
            f"se(slope) = sqrt(SSres/(n-2)/Sxx) = {standard_error:.6f}",
            f"95% CI   = slope +/- t({df}) * se = {interval.render(6)}",
        )
        return Fit(
            curve_id=curve_id,
            n=n,
            slope=slope,
            slope_standard_error=standard_error,
            slope_interval=interval,
            intercept=intercept,
            r_squared=r_squared,
            x_min=min(xs),
            x_max=max(xs),
            y_mean=mean_y,
            trace=trace,
        )
