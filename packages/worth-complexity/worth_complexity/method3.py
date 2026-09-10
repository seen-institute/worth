"""Method 3: the band-expected payment.

    expected = multiplier x median(comparator PFS amount, score in a band around
    the study encounter's own score)

Method 2 (see ``adequacy.py``) fits one line through the whole comparator
cohort and reads a point off it; every encounter's expected payment moves with
the line's slope alone. Method 3 asks a narrower question instead: what did
comparator encounters *scored near this one* actually get paid, at the fee
schedule? It needs no functional form, only enough neighbours.

"Enough" is the suppression floor, the same n=11 the registry already refuses
to publish below. The band starts at the study score +/- three points and
widens by a point on each side, symmetrically, until it holds that many
comparators or has nowhere left to widen to. A code whose local neighbourhood
never reaches the floor gets no Method 3 value at all, rather than one drawn
from a band so wide it has stopped being "near this score".

The adequacy ratio's denominator is this expected payment (decision 2 in
PLAN.md's contract): ``adequacy = realized / Method 3 expected``. Method 2
stays as a separate, reported figure — see ``signature.py``, where the gap
between the two is read as a fee-schedule-shape signal rather than folded
silently into one number.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from worth_complexity.models import (
    SUPPRESSION_THRESHOLD,
    ComplexityScore,
    NoReferenceCurveError,
)
from worth_complexity.money import money_context
from worth_complexity.sampling import BOOTSTRAP_DRAWS, Interval, quantile

CENTS = Decimal("0.01")

type ComparatorInput = tuple[str, str, str, int, Decimal]
"""One comparator encounter: ``(encounter_id, cpt, specialty, score, schedule_amount)``.

``schedule_amount`` is the comparator's primary code priced at the run's
reference CMS release, in Medicare PFS dollars — ``Run.priced[...].at_reference.amount``
in the caller's terms. Plain tuples, not a dataclass: this keeps ``method3.py``
free of any dependency on how the caller assembled its comparator pool.
"""


@dataclass(frozen=True, slots=True)
class Band:
    """The score window a Method 3 expectation was computed over."""

    low: int
    high: int
    n: int
    """Comparators inside ``[low, high]``."""
    widened: bool
    """True once the band grew past its starting half-width."""
    half_width: int
    """The half-width the band settled at, e.g. ``3`` if never widened."""


@dataclass(frozen=True, slots=True)
class BandPoint:
    """One comparator encounter inside the band, in both dollar scales."""

    encounter_id: str
    cpt: str
    specialty: str
    score: int
    schedule_amount: Decimal
    """PFS amount at the reference release, Medicare dollars."""
    amount: Decimal
    """``schedule_amount x multiplier``, in the study encounter's payer's dollars."""


@dataclass(frozen=True, slots=True)
class Method3:
    """One encounter's Method 3 expected payment, with everything behind it."""

    encounter_id: str
    score: ComplexityScore
    band: Band
    schedule_median: Decimal
    """Median PFS amount in the band, Medicare dollars."""
    multiplier: Decimal
    expected: Decimal
    """``multiplier x schedule_median``, quantized to cents."""
    interval: Interval
    """Seeded 95% bootstrap percentile interval of the band median x multiplier."""
    comparators: tuple[BandPoint, ...]
    specialty_medians: dict[str, Decimal]
    """Median ``amount`` (payer dollars) per specialty represented in the band."""
    trace: tuple[str, ...]

    def render(self) -> str:
        lines = [
            f"encounter {self.encounter_id}   Method 3 band expected",
            "",
            "Derivation",
            "----------",
        ]
        lines.extend(f"  {step}".rstrip() for step in self.trace)
        lines += ["", f"Method 3 expected: {self.expected:.2f}  95% CI {self.interval.render(2)}"]
        return "\n".join(lines)


def bootstrap_median(
    amounts: tuple[Decimal, ...],
    multiplier: Decimal,
    *,
    seed: int,
    draws: int = BOOTSTRAP_DRAWS,
) -> Interval:
    """Seeded percentile bootstrap of ``median(amounts) x multiplier``.

    Resamples band members with replacement, takes the median of each
    resample, and multiplies by the payer multiplier — the same quantity as
    ``expected``, just recomputed 2000 times so its sampling spread is
    visible. The seed is a caller-supplied integer, not the clock, so a
    published interval is exactly reproducible.
    """
    if not amounts:
        msg = "a bootstrap needs at least one observation"
        raise ValueError(msg)
    if len(amounts) == 1:
        with money_context():
            v = (amounts[0] * multiplier).quantize(CENTS)
        return Interval(v, v, "bootstrap")

    rng = random.Random(seed)
    size = len(amounts)
    medians: list[Decimal] = []
    with money_context():
        for _ in range(draws):
            sample = sorted(amounts[rng.randrange(size)] for _ in range(size))
            medians.append(quantile(sample, "0.5") * multiplier)
        medians.sort()
        return Interval(
            quantile(medians, "0.025").quantize(CENTS),
            quantile(medians, "0.975").quantize(CENTS),
            "bootstrap",
        )


def band_expected(
    encounter_id: str,
    score: ComplexityScore,
    comparators: tuple[ComparatorInput, ...],
    multiplier: Decimal,
    *,
    half_width: int = 3,
    floor: int = SUPPRESSION_THRESHOLD,
    seed: int,
) -> Method3:
    """Compute the Method 3 expected payment for one study encounter.

    ``encounter_id`` is not in the contract's abbreviated signature but is
    required to populate :attr:`Method3.encounter_id`; every other keyword
    matches the contract exactly.
    """
    s = score.value

    def band_at(width: int) -> tuple[int, int, list[ComparatorInput]]:
        low = max(0, s - width)
        high = min(100, s + width)
        members = [c for c in comparators if low <= c[3] <= high]
        return low, high, members

    hw = half_width
    low, high, members = band_at(hw)
    widened = False
    while len(members) < floor and not (low == 0 and high == 100):
        hw += 1
        widened = True
        low, high, members = band_at(hw)

    if len(members) < floor:
        msg = (
            f"encounter {encounter_id}: only {len(members)} comparators even in the "
            f"widest possible band (score {low}-{high} of 0-100); need at least {floor}. "
            "No Method 3 reference exists for this encounter."
        )
        raise NoReferenceCurveError(msg)

    band = Band(low=low, high=high, n=len(members), widened=widened, half_width=hw)

    amounts = sorted(c[4] for c in members)
    schedule_median = quantile(amounts, "0.5")
    with money_context():
        expected = (multiplier * schedule_median).quantize(CENTS)

    interval = bootstrap_median(tuple(amounts), multiplier, seed=seed)

    band_points: list[BandPoint] = []
    with money_context():
        for eid, cpt, specialty, sc, amt in sorted(members, key=lambda m: (m[3], m[0])):
            band_points.append(
                BandPoint(
                    encounter_id=eid,
                    cpt=cpt,
                    specialty=specialty,
                    score=sc,
                    schedule_amount=amt,
                    amount=(multiplier * amt).quantize(CENTS),
                )
            )
    comparators_out = tuple(band_points)

    by_specialty: dict[str, list[Decimal]] = defaultdict(list)
    for point in comparators_out:
        by_specialty[point.specialty].append(point.amount)
    specialty_medians = {
        specialty: quantile(sorted(values), "0.5")
        for specialty, values in sorted(by_specialty.items())
    }

    trace = (
        f"study score                              = {s}",
        f"band                                      = [{low}, {high}]"
        f"{' (widened)' if widened else ' (unwidened)'}, half-width={hw}, n={band.n}",
        f"comparator PFS amounts in band, sorted     = [{', '.join(f'{a:.2f}' for a in amounts)}]",
        f"schedule median                            = {schedule_median:.2f}"
        "   (Medicare PFS dollars)",
        f"payer multiplier                           = {multiplier}",
        f"expected = multiplier x schedule median    = {multiplier} x {schedule_median:.2f}"
        f" = {expected:.2f}",
        f"95% CI, seeded bootstrap (seed={seed}, draws={BOOTSTRAP_DRAWS}) = {interval.render(2)}",
        f"specialty medians (band, payer dollars)    = "
        f"{{{', '.join(f'{k}: {v:.2f}' for k, v in specialty_medians.items())}}}",
    )

    return Method3(
        encounter_id=encounter_id,
        score=score,
        band=band,
        schedule_median=schedule_median,
        multiplier=multiplier,
        expected=expected,
        interval=interval,
        comparators=comparators_out,
        specialty_medians=specialty_medians,
        trace=trace,
    )
