"""The dollar spine, its decomposition, and the signature read off it.

Three methods price the same encounter differently, and the gap between them
is diagnostic rather than noise (doc pp. 26-27):

* **Method 1** — what documented-but-uncoded or miscoded work would be worth
  if it had a billing vehicle. Built from ``method1.py``'s flags, priced at
  PFS x the payer multiplier. When no vehicle exists at all, Method 1 still
  reports a count of unpriced flags, so the gap is visible even at $0.
* **Method 2** — the fee schedule's own complexity relation, read at this
  encounter's score. ``m2_adjustment`` isolates the part of that figure that
  comes specifically from sitting away from the code's own median position on
  the curve — the "compression" component, which can run negative (a code
  whose curve pays *less* at its own median than a below-median case would
  suggest a compressed scale, not a fee-schedule gap).
* **Method 3** — the band-expected payment from ``method3.py``, the
  denominator of the published adequacy ratio.

The **dollar spine** lays these on one axis, in the encounter's realized
dollars. The **decomposition** turns the spine into a waterfall:
``m3_shortfall`` (what Method 3 says is missing) splits into ``compression``
(Method 2's own adjustment) and what is left over, ``center_mispricing``.
Method 1 is reported beside this waterfall, never inside it — a documented
but unbilled step is a different kind of problem from a rate that pays wrong
at the center, and adding them would make one dollar figure answer two
questions.

The **signature** is the pattern read off the three magnitudes: which lever —
coding/documentation, a gap in the code set itself, the code's structure, or
the fee schedule's rate — best explains why this encounter's dollars look the
way they do.

Deviation from the contract's literal ``signature_of(spine, *, method1_summary,
code_kind)``: the task instructions for this track ask every W1b function to
take plain values for Method 1 rather than import W1a's ``Method1Summary``
(to avoid a hard import dependency while W1a is still being written). This
module's ``signature_of`` therefore takes ``any_vehicle: bool`` — whether any
Method 1 flag on this encounter names a candidate code — in its place.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from worth_complexity.money import money_context

if TYPE_CHECKING:
    from worth_complexity.curve import Fit
    from worth_complexity.models import ComplexityScore

CENTS = Decimal("0.01")
TEN_PERCENT = Decimal("0.10")
QUARTER = Decimal("0.25")
DOUBLE = Decimal("2")

PATTERN_IDS: tuple[str, ...] = (
    "m1-dominant-vehicle",
    "m1-dominant-no-vehicle",
    "m1-m3-dominant",
    "structural-converging",
    "center-mispriced",
    "compression",
    "mixed",
)
"""Every pattern ``signature_of`` can name, in evaluation order.

A limitation worth knowing before reading the two no-vehicle patterns: the
Method 1 magnitude is priced dollars, and only ``missed`` and ``mismatched``
flags are priced (CONTRACT.md decision 1). ``no_code`` work is counted
(``DollarSpine.m1_unpriced_count``) but carries no dollars, so a case whose
documented work has no billing vehicle at all reads, by dollars, as a Method 3
finding. The doc's example D reaches "M1 and M3 dominant" by pricing no-code
work at what equivalent work earns elsewhere (complex care management); until
that reference exists here, ``m1-dominant-no-vehicle`` and ``m1-m3-dominant``
are reachable only through priced flags.
"""

Lever = Literal["coding-documentation", "code-set-gap", "code-structure", "fee-schedule"]

CodeKind = Literal["procedure", "level"]


@dataclass(frozen=True, slots=True)
class MethodMagnitude:
    """One method's dollar figure in the spine, and the lever it implicates.

    ``dollars`` is the method's *gap*, not its raw price: for M1 the priced
    total of missed/mismatched flags; for M2 ``m2_adjustment`` (the
    compression component); for M3 ``m3_shortfall``. Comparisons between
    methods (see the pattern rules below) compare these magnitudes by
    absolute value, since a compression or shortfall can run negative.
    """

    method: Literal["M1", "M2", "M3"]
    dollars: Decimal | None
    lever: Lever | None
    built: bool
    """False only for M1 when no Method 1 data was available at all (as
    opposed to available and zero)."""
    label: str


@dataclass(frozen=True, slots=True)
class DollarSpine:
    """One encounter's dollars, laid out for the waterfall in ``decompose``."""

    realized: Decimal
    m1_unpriced: Decimal | None
    """The Method 1 total (missed + mismatched dollars), reported beside the
    M3-based waterfall — never priced into it. Named for what it is *not*
    used for: it is never priced into the M3 decomposition below."""
    m1_missed: Decimal | None
    m1_mismatched: Decimal | None
    m1_no_code: Decimal | None
    m1_unpriced_count: int
    """Flags with ``priced is None`` — so unpriced work stays visible even at $0."""
    m2_expected: Decimal
    m2_adjustment: Decimal
    """``multiplier x (curve.predict(score) - curve.predict(code_median))``. May be negative."""
    m3_expected: Decimal


@dataclass(frozen=True, slots=True)
class Decomposition:
    """The waterfall: how much of the Method 3 gap is compression vs. center."""

    m3_shortfall: Decimal
    """``m3_expected - realized``."""
    compression: Decimal
    """``= m2_adjustment``."""
    center_mispricing: Decimal
    """``m3_shortfall - compression``. What is left after compression is
    subtracted out: the part of the gap the fee schedule's rate, not its
    shape, explains."""
    m1_unpriced: Decimal | None
    """Passed through from the spine, reported beside — never inside — the
    two figures above."""


@dataclass(frozen=True, slots=True)
class Signature:
    """The pattern read off one encounter's three method magnitudes."""

    m1: MethodMagnitude
    m2: MethodMagnitude
    m3: MethodMagnitude
    pattern_id: str
    dominant: str
    """Human-readable label for the pattern, e.g. "Large Method 1 with a
    vehicle: coding/documentation"."""
    m2_m3_converge: bool
    """``|m2_expected - m3_expected| <= 10% of m3_expected``."""


def spine(
    *,
    realized: Decimal,
    score: ComplexityScore,
    curve: Fit,
    code_median: Decimal,
    multiplier: Decimal,
    m3_expected: Decimal,
    m1_total: Decimal | None,
    m1_split: tuple[Decimal | None, Decimal | None, Decimal | None],
    m1_unpriced_count: int,
) -> DollarSpine:
    """Build one encounter's dollar spine.

    ``m1_split`` is ``(missed, mismatched, no_code)``, matching
    ``Method1Summary.by_bucket``'s keys in that order; ``no_code`` dollars are
    always ``None`` under decision 1 (no reference exists to price it), kept
    here purely for display. ``curve`` and ``code_median`` are the same
    comparator-fitted schedule curve ``adequacy.py`` already fits; the median
    point is predicted with extrapolation allowed only if the code's median
    score itself happens to fall outside the curve's fitted range.
    """
    missed, mismatched, no_code = m1_split
    with money_context():
        at_score = curve.predict(Decimal(score.value), allow_extrapolation=True)
        at_median = curve.predict(code_median, allow_extrapolation=True)
        m2_expected = (multiplier * at_score).quantize(CENTS)
        m2_adjustment = (multiplier * (at_score - at_median)).quantize(CENTS)

    return DollarSpine(
        realized=realized,
        m1_unpriced=m1_total,
        m1_missed=missed,
        m1_mismatched=mismatched,
        m1_no_code=no_code,
        m1_unpriced_count=m1_unpriced_count,
        m2_expected=m2_expected,
        m2_adjustment=m2_adjustment,
        m3_expected=m3_expected,
    )


def decompose(spine: DollarSpine) -> Decomposition:
    """Split the Method 3 gap into compression and center-mispricing."""
    with money_context():
        m3_shortfall = (spine.m3_expected - spine.realized).quantize(CENTS)
        compression = spine.m2_adjustment
        center_mispricing = (m3_shortfall - compression).quantize(CENTS)
    return Decomposition(
        m3_shortfall=m3_shortfall,
        compression=compression,
        center_mispricing=center_mispricing,
        m1_unpriced=spine.m1_unpriced,
    )


def _m1_lever(any_vehicle: bool) -> Lever:
    return "coding-documentation" if any_vehicle else "code-set-gap"


def signature_of(
    spine: DollarSpine,
    *,
    any_vehicle: bool,
    code_kind: CodeKind,
) -> Signature:
    """Read the dominant-lever pattern off one encounter's dollar spine.

    Evaluated in the order the doc gives (pp. 26-27); the first rule that
    matches wins, with ``mixed`` as the fallback. A missing Method 1 total
    (``m1_unpriced is None``) is treated as zero for every comparison but
    ``m1.built`` records the difference between "zero flags" and "no Method 1
    data at all".
    """
    m1_dollars = abs(spine.m1_unpriced) if spine.m1_unpriced is not None else Decimal(0)
    m2_dollars = abs(spine.m2_adjustment)
    m3_dollars = abs(spine.m3_expected - spine.realized)
    built = spine.m1_unpriced is not None

    m1 = MethodMagnitude(
        method="M1",
        dollars=spine.m1_unpriced,
        lever=_m1_lever(any_vehicle),
        built=built,
        label="Method 1: documented, uncoded or miscoded work",
    )
    m2 = MethodMagnitude(
        method="M2",
        dollars=spine.m2_adjustment,
        lever="code-structure",
        built=True,
        label="Method 2: distance from the code's own median on the schedule curve",
    )
    m3 = MethodMagnitude(
        method="M3",
        dollars=(spine.m3_expected - spine.realized),
        lever="fee-schedule",
        built=True,
        label="Method 3: band-expected shortfall",
    )

    with money_context():
        m2_m3_converge = spine.m3_expected == 0 or abs(spine.m2_expected - spine.m3_expected) <= (
            TEN_PERCENT * spine.m3_expected
        )

    small = m1_dollars < QUARTER * m3_dollars if m3_dollars else m1_dollars == 0
    kind_note = f" on a {code_kind} code" if code_kind == "level" else ""

    if m1_dollars >= max(m2_dollars, m3_dollars) and any_vehicle:
        pattern_id = "m1-dominant-vehicle"
        dominant = f"Large Method 1{kind_note}, with a vehicle: coding/documentation"
    elif m1_dollars >= max(m2_dollars, m3_dollars) and not any_vehicle:
        pattern_id = "m1-dominant-no-vehicle"
        dominant = f"Large Method 1{kind_note}, no billing vehicle exists"
    elif m1_dollars >= DOUBLE * m2_dollars and m3_dollars >= DOUBLE * m2_dollars:
        pattern_id = "m1-m3-dominant"
        dominant = (
            "Large Method 1 and Method 3, small Method 2: a code-set gap at a "
            "magnitude the schedule curve's own compression can't explain"
        )
    elif small and m2_m3_converge:
        pattern_id = "structural-converging"
        dominant = "Small Method 1, large Method 2 and Method 3: structural, base rate on the curve"
    elif not m2_m3_converge and m3_dollars > m2_dollars:
        pattern_id = "center-mispriced"
        dominant = "Method 2 and Method 3 diverge: mispriced at its center"
    elif m2_dollars >= m3_dollars and small:
        pattern_id = "compression"
        dominant = "The code cannot carry the complexity"
    else:
        pattern_id = "mixed"
        dominant = "No single method dominates"

    return Signature(
        m1=m1,
        m2=m2,
        m3=m3,
        pattern_id=pattern_id,
        dominant=dominant,
        m2_m3_converge=m2_m3_converge,
    )
