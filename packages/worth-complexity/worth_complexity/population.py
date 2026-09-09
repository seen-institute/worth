"""Population-level views: the per-code card, cross-code comparison, the queue.

Everything here is built from plain per-encounter values rather than from
``Run``/``Adequacy`` objects, so a caller can hand-build a handful of
``EncounterRow``s and get a real ``CodeCard`` back without running the
pipeline. ``pipeline.py`` (W3's job) is the only place that will translate a
``Run`` into these rows.

Two deliberate deviations from the contract's literal type signatures, both
to avoid a hard import dependency on ``method1.py`` while W1a writes it
concurrently (the same instruction applies to ``signature.py``):

* ``EncounterRow.m1_total`` / ``m1_split`` / friction fields are plain
  values, not ``Method1Summary`` or ``PayerFriction`` objects.
* ``CodeCard.method1`` is ``object | None`` and ``QueueItem.flags`` /
  ``QueueRow.flags`` are ``tuple[object, ...]`` — carried through opaquely.
  ``queue()`` takes the plain values it actually needs (``has_missed_flag``,
  ``missed_candidate_code``, ``evidence_strength``) as separate fields on
  ``QueueRow`` rather than introspecting the flag objects itself.

A third, smaller deviation: ``EncounterRow`` gains a ``multiplier`` field
beyond the contract's listed fields. ``ScatterPoint.schedule_scale`` and the
pooled Method 0 slope (decision 7) are both defined as ``realized /
multiplier``, and nothing else in the listed fields carries that number.
"""

from __future__ import annotations

import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from worth_complexity.curve import fit
from worth_complexity.models import (
    SUPPRESSION_THRESHOLD,
    Cohort,
    MethodologyViolation,
    NoReferenceCurveError,
    Ratio,
    Stratum,
    ratio_of,
)
from worth_complexity.money import money_context
from worth_complexity.sampling import Distribution, Interval, bootstrap_ratio, describe, quantile

if TYPE_CHECKING:
    from datetime import date

    from worth_complexity.adequacy import Observation
    from worth_complexity.models import Adequacy
    from worth_complexity.pipeline import ClassRun
    from worth_complexity.signature import Lever, MethodMagnitude, Signature

FOUR = Decimal("0.0001")
CENTS = Decimal("0.01")

Strength = Literal["strong", "moderate", "weak"]
"""Local mirror of ``method1.Strength``. A plain Literal, not an import — see
the module docstring."""

_STRENGTH_RANK: dict[str, int] = {"strong": 3, "moderate": 2, "weak": 1}

_DECILE_P: tuple[str, ...] = (
    "0.0", "0.1", "0.2", "0.3", "0.4", "0.5", "0.6", "0.7", "0.8", "0.9", "1.0",
)  # fmt: skip


def _seed_for(code: str) -> int:
    """A deterministic integer seed derived from a code string.

    Most codes are numeric CPTs and parse directly; a handful of HCPCS codes
    carry a leading letter, so a stable checksum is the fallback rather than
    letting ``int()`` raise.
    """
    try:
        return int(code)
    except ValueError:
        return zlib.crc32(code.encode())


# --------------------------------------------------------------------------
# Dataclasses
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HistogramBin:
    """One 10-point bin of the code's score distribution, ``[low, high)``
    except the last bin, which is inclusive of 100."""

    low: int
    high: int
    count: int


@dataclass(frozen=True, slots=True)
class DecileRatio:
    """The adequacy ratio within one decile of the code's own scores."""

    decile: int
    score_low: int
    score_high: int
    n: int
    ratio: Ratio | None
    interval: Interval | None


@dataclass(frozen=True, slots=True)
class RatioAt:
    """The ratio in the decile containing a named percentile of scores."""

    point: Literal["median", "p75", "p90"]
    score: int
    ratio: Ratio | None
    interval: Interval | None
    n: int


@dataclass(frozen=True, slots=True)
class Shortfall:
    """The Method 3 gap, aggregated over a code's cohort."""

    total: Decimal
    top_quintile: Decimal
    top_quintile_share: Decimal
    center_mispricing: Decimal
    compression: Decimal
    m1_unpriced: Decimal | None
    n: int


@dataclass(frozen=True, slots=True)
class SignatureMix:
    """How much of the aggregate gap each method's magnitude accounts for."""

    m1_share: Decimal
    m2_share: Decimal
    m3_share: Decimal
    dominant_lever: Lever
    pattern_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class PooledSlope:
    """The pooled (cross-payer) Method 0 slope, decision 7."""

    n: int
    score_min: int
    score_max: int
    score_median: int
    slope: Decimal
    interval: Interval
    normalized_slope: Decimal
    """``slope x 10 / median(schedule-scale realized)``, as a percentage."""
    normalized_interval: Interval
    verdict: Literal["flat", "rising", "insufficient spread"]
    r_squared: Decimal


@dataclass(frozen=True, slots=True)
class MarkerHealth:
    """One marker's completeness, by site. ``kappa`` has no data source yet."""

    marker_id: str
    provenance: str
    note_derived: bool
    missingness_by_site: dict[str, Decimal]
    kappa: Decimal | None


@dataclass(frozen=True, slots=True)
class InstrumentHealth:
    markers: tuple[MarkerHealth, ...]
    rulebook_version: str
    weights_version: str
    rule_pack_digest: str


@dataclass(frozen=True, slots=True)
class PayerFrictionRow:
    payer_label: str
    n: int
    denied: int
    downcoded: int
    denial_rate: Decimal
    downcode_rate: Decimal
    carc: dict[str, int]
    rarc: dict[str, int]


@dataclass(frozen=True, slots=True)
class ScatterPoint:
    encounter_id: str
    score: int
    realized: Decimal
    payer_label: str
    schedule_scale: Decimal
    """``realized / multiplier``, Medicare-scale dollars (decision 7)."""


@dataclass(frozen=True, slots=True)
class PeriodRow:
    """One study encounter's plain values for :func:`periods` (decision 7,
    CONTRACT-SEEDS.md) -- the over-time twin of :class:`EncounterRow`, with
    just what a time series needs and nothing ``code_card`` needs beyond
    that: which code, which calendar date, and the score/realized/expected/
    signature/payer a ratio-over-time point and a pre/post summary are built
    from."""

    code: str
    service_date: date
    score: int
    realized: Decimal
    expected: Decimal
    """Method 3 expected payment."""
    signature: Signature
    payer_label: str
    service_line: str = ""
    """The domain (service line); lets :func:`periods` key a series by
    domain so a line whose billing vehicle changes across a policy date
    (maternity: antepartum bundle before, 99214 after) reads as one series."""


@dataclass(frozen=True, slots=True)
class PeriodPoint:
    """One code's ratio in one calendar period."""

    period: str
    """``"YYYY-MM"`` for month granularity, ``"YYYY-QN"`` for quarter."""
    n: int
    ratio: Ratio | None
    interval: Interval | None
    suppressed: bool
    """``True`` when ``n`` is below the suppression floor (decision 7): the
    point is still listed, with its ``n``, but ``ratio``/``interval`` are
    withheld rather than published on a cell too small to de-identify."""


@dataclass(frozen=True, slots=True)
class PeriodSummary:
    """A code's cohort on one side of a policy date: distribution, ratio and
    signature mix, all computed the same way :func:`code_card` computes them
    for a whole cohort, just restricted to one side of the line."""

    n: int
    distribution: Distribution
    ratio: Ratio | None
    interval: Interval | None
    signature_mix: SignatureMix | None


@dataclass(frozen=True, slots=True)
class PayerSeries:
    """One code's ratio-over-time points, restricted to one payer."""

    payer_label: str
    points: tuple[PeriodPoint, ...]


Granularity = Literal["month", "quarter", "half"]
"""How a period series buckets calendar time. ``periods(granularity="auto")``
picks the finest of these at which at least half of a code's points clear the
suppression floor, because a monthly series in which every month is withheld
draws nothing (60 cases over a year is five a month)."""

GRANULARITIES: tuple[Granularity, ...] = ("month", "quarter", "half")


@dataclass(frozen=True, slots=True)
class PeriodSeries:
    """One code's over-time output (decision 7, CONTRACT-SEEDS.md): the
    monthly (or quarterly) ratio with its interval, a pre/post split at a
    named policy date when one applies, and the same series again per payer.
    """

    code: str
    domain: str | None
    """The service line when the series is keyed by domain (``periods(by="domain")``);
    ``None`` for a per-code series."""
    granularity: Granularity
    points: tuple[PeriodPoint, ...]
    pre: PeriodSummary | None
    """The cohort strictly before ``policy_date``. ``None`` when
    ``policy_date`` was not given, or when nothing fell on this side."""
    post: PeriodSummary | None
    """The cohort on or after ``policy_date``. Same ``None`` rules as :attr:`pre`."""
    by_payer: tuple[PayerSeries, ...]
    policy_date: date | None
    rulebook_version: str
    weights_version: str


@dataclass(frozen=True, slots=True)
class EncounterRow:
    """One study encounter's plain values — enough to build a ``CodeCard``
    cell with no pipeline in the loop. See the module docstring for the
    ``multiplier`` deviation."""

    encounter_id: str
    score: int
    realized: Decimal
    expected: Decimal
    """Method 3 expected payment."""
    m2_expected: Decimal
    m2_adjustment: Decimal
    center_mispricing: Decimal
    m3_shortfall: Decimal
    m1_total: Decimal | None
    m1_split: tuple[Decimal | None, Decimal | None, Decimal | None]
    """``(missed, mismatched, no_code)``."""
    multiplier: Decimal
    payer_label: str
    site: str
    pattern_id: str
    dominant_lever: Lever
    signature: Signature
    friction: tuple[bool, bool, tuple[str, ...], tuple[str, ...]]
    """``(denied, downcoded, carc, rarc)``, decision 11."""
    present_markers: tuple[str, ...]
    missing_markers: tuple[str, ...] = ()
    """Marker ids the rule pack lists that were absent or rejected on this
    encounter and so scored at zero (``ScoredEncounter.missing``, decision
    6, CONTRACT-SEEDS.md). What :func:`instrument_health` now computes
    missingness from, in place of inferring it from :attr:`present_markers`."""


@dataclass(frozen=True, slots=True)
class CodeCard:
    code: str
    encounter_class: str
    service_line: str
    """The modal ``service_line`` of this code's study encounters (decision
    6, CONTRACT-PACKS.md): the domain ``compare_rows(..., by="domains")``
    groups codes by within a class, e.g. MENOPAUSE/MATERNITY for the visit
    class's study codes, ENDOCRINOLOGY/CARDIOLOGY/NEPHROLOGY for its
    comparators."""
    n: int
    scored: int
    method0: PooledSlope | None
    strata: tuple[Stratum, ...]
    comparator_codes: tuple[str, ...]
    comparator_slope: PooledSlope | None
    points: tuple[ScatterPoint, ...]
    comparator_points: tuple[ScatterPoint, ...]
    distribution: Distribution
    histogram: tuple[HistogramBin, ...]
    top_quintile_cutoff: int
    ratio_by_decile: tuple[DecileRatio, ...]
    ratio_at: tuple[RatioAt, ...]
    adequacy: Ratio | None
    adequacy_interval: Interval | None
    shortfall: Shortfall | None
    signature_mix: SignatureMix | None
    method1: object | None
    """``Method1Summary`` once W3 wires the pipeline; carried opaquely — see
    the module docstring."""
    payer_friction: tuple[PayerFrictionRow, ...]
    instrument_health: InstrumentHealth
    headline: str
    rulebook_version: str
    weights_version: str
    suppressed: bool
    suppression_reason: str | None


@dataclass(frozen=True, slots=True)
class CompareRow:
    code: str
    encounter_class: str
    domain: str | None
    """This code's ``service_line`` (decision 6). Populated whichever way
    ``compare_rows`` is called; only the ordering changes with ``by``."""
    n: int
    normalized_slope: Decimal | None
    normalized_interval: Interval | None
    ratio_median: Ratio | None
    ratio_p90: Ratio | None
    shortfall: Decimal | None
    top_quintile_share: Decimal | None
    dominant_lever: Lever | None
    m1_share: Decimal | None
    m2_share: Decimal | None
    m3_share: Decimal | None
    missingness: Decimal
    rulebook_version: str
    weights_version: str
    verdict: str | None


@dataclass(frozen=True, slots=True)
class QueueRow:
    """Plain per-encounter input to ``queue()``. See the module docstring
    for why ``flags`` is opaque here."""

    encounter_id: str
    code: str
    score: int
    code_percentile: Decimal
    flags: tuple[object, ...]
    has_missed_flag: bool
    missed_candidate_code: str | None
    evidence_strength: Strength | None
    outcome_835: Literal["paid", "denied", "downcoded", "pending"]
    payer_label: str
    site: str
    surgeon: str | None
    top_quintile_cutoff: int


@dataclass(frozen=True, slots=True)
class QueueItem:
    encounter_id: str
    code: str
    score: int
    code_percentile: Decimal
    flags: tuple[object, ...]
    evidence_strength: Strength | None
    suggested_vehicle: str | None
    outcome_835: Literal["paid", "denied", "downcoded", "pending"]
    payer_label: str
    site: str
    surgeon: str | None


# --------------------------------------------------------------------------
# Histogram, deciles, shortfall, mix, slope
# --------------------------------------------------------------------------


def histogram(scores: tuple[int, ...]) -> tuple[HistogramBin, ...]:
    """10 bins of 10 points; the last bin is inclusive of 100."""
    bins: list[HistogramBin] = []
    for i in range(10):
        low, high = i * 10, i * 10 + 10
        if i == 9:
            count = sum(1 for s in scores if low <= s <= high)
        else:
            count = sum(1 for s in scores if low <= s < high)
        bins.append(HistogramBin(low, high, count))
    return tuple(bins)


def _decile_boundaries(scores_sorted: list[Decimal]) -> list[Decimal]:
    return [quantile(scores_sorted, p) for p in _DECILE_P]


def _decile_index(point: Decimal, boundaries: list[Decimal]) -> int:
    for d in range(1, 11):
        low, high = boundaries[d - 1], boundaries[d]
        if (d < 10 and low <= point < high) or (d == 10 and low <= point <= high):
            return d - 1
    return 9


def ratio_by_decile(rows: tuple[EncounterRow, ...]) -> tuple[DecileRatio, ...]:
    """Adequacy ratio (sum realized / sum M3 expected) within each decile of
    the code's own score distribution. Deciles are cut with ``quantile`` from
    ``sampling.py``, the same quantile definition the rest of the package
    uses. CI via a bootstrap seeded on the decile number (1-10) when the
    decile holds at least two encounters."""
    if not rows:
        return ()
    scores_sorted = sorted(Decimal(r.score) for r in rows)
    boundaries = _decile_boundaries(scores_sorted)
    out: list[DecileRatio] = []
    for d in range(1, 11):
        low, high = boundaries[d - 1], boundaries[d]
        members = [
            r
            for r in rows
            if (d < 10 and low <= Decimal(r.score) < high)
            or (d == 10 and low <= Decimal(r.score) <= high)
        ]
        ratio: Ratio | None = None
        interval: Interval | None = None
        if members:
            with money_context():
                realized = sum((r.realized for r in members), start=Decimal(0))
                expected = sum((r.expected for r in members), start=Decimal(0))
            if expected > 0:
                ratio = ratio_of(realized, expected)
                if len(members) >= 2:
                    pairs = tuple((r.realized, r.expected) for r in members)
                    interval = bootstrap_ratio(pairs, seed=d)
        out.append(DecileRatio(d, int(low), int(high), len(members), ratio, interval))
    return tuple(out)


def ratio_at(rows: tuple[EncounterRow, ...]) -> tuple[RatioAt, ...]:
    """The ratio in the decile containing the median, p75 and p90 score."""
    if not rows:
        return ()
    deciles = ratio_by_decile(rows)
    scores_sorted = sorted(Decimal(r.score) for r in rows)
    boundaries = _decile_boundaries(scores_sorted)

    def at(point_label: Literal["median", "p75", "p90"], p: str) -> RatioAt:
        point = quantile(scores_sorted, p)
        d = deciles[_decile_index(point, boundaries)]
        return RatioAt(point_label, int(point), d.ratio, d.interval, d.n)

    return (at("median", "0.5"), at("p75", "0.75"), at("p90", "0.9"))


def _top_quintile_cutoff(scores: tuple[int, ...]) -> int:
    if not scores:
        return 0
    return int(quantile(sorted(Decimal(s) for s in scores), "0.8"))


def shortfall(rows: tuple[EncounterRow, ...]) -> Shortfall:
    """The Method 3 gap, aggregated, with the share sitting in the top
    quintile of the code's own score distribution (score >= 80th percentile)."""
    if not rows:
        return Shortfall(Decimal(0), Decimal(0), Decimal(0), Decimal(0), Decimal(0), None, 0)
    cutoff = _top_quintile_cutoff(tuple(r.score for r in rows))
    with money_context():
        total = sum((r.m3_shortfall for r in rows), start=Decimal(0))
        top = sum((r.m3_shortfall for r in rows if r.score >= cutoff), start=Decimal(0))
        center = sum((r.center_mispricing for r in rows), start=Decimal(0))
        compression = sum((r.m2_adjustment for r in rows), start=Decimal(0))
        m1_values = [r.m1_total for r in rows if r.m1_total is not None]
        m1_unpriced = sum(m1_values, start=Decimal(0)) if m1_values else None
        share = (top / total).quantize(FOUR) if total != 0 else Decimal(0)
    return Shortfall(total, top, share, center, compression, m1_unpriced, len(rows))


def _magnitude(m: MethodMagnitude) -> Decimal:
    return abs(m.dollars) if m.dollars is not None else Decimal(0)


def _dominant_lever(sig: Signature) -> Lever:
    """The lever behind whichever of M1/M2/M3 has the largest |magnitude|
    on one encounter. Mirrors ``pipeline._dominant_lever``; kept here too so
    :func:`encounter_row` needs no import from ``pipeline`` (which imports
    this module, not the other way round)."""
    best = max((sig.m1, sig.m2, sig.m3), key=_magnitude)
    return best.lever or "fee-schedule"


def encounter_row(a: Adequacy, o: Observation, *, payer_label: str = "") -> EncounterRow:
    """One study encounter's plain row, straight from its scored
    :class:`~worth_complexity.models.Adequacy` and
    :class:`~worth_complexity.adequacy.Observation`.

    The construction :func:`~worth_complexity.pipeline.run`'s population
    stage uses to build every :func:`code_card`'s rows, factored out here so
    :func:`domain_rows` can build the same rows grouped a different way
    without duplicating it. ``payer_label`` is the *blinded* label
    (``Run.payer_labels`` lives with the blinding, not this module, so a
    caller with no payer context at all may simply leave it blank); nothing
    :func:`domain_rows` computes from the returned row reads it.
    """
    return EncounterRow(
        encounter_id=a.encounter_id,
        score=a.score.value,
        realized=a.realized,
        expected=a.expected,
        m2_expected=a.spine.m2_expected,
        m2_adjustment=a.spine.m2_adjustment,
        center_mispricing=a.decomposition.center_mispricing,
        m3_shortfall=a.decomposition.m3_shortfall,
        m1_total=a.spine.m1_unpriced,
        m1_split=(a.spine.m1_missed, a.spine.m1_mismatched, a.spine.m1_no_code),
        multiplier=a.multiplier,
        payer_label=payer_label,
        site=o.scored.encounter.facility_npi,
        pattern_id=a.signature.pattern_id,
        dominant_lever=_dominant_lever(a.signature),
        signature=a.signature,
        friction=(
            a.payer_friction.denied,
            a.payer_friction.downcoded,
            a.payer_friction.carc,
            a.payer_friction.rarc,
        ),
        present_markers=tuple(m.marker_id for m in o.scored.markers),
        missing_markers=o.scored.missing,
    )


def signature_mix(signatures: tuple[Signature, ...]) -> SignatureMix:
    """Each method's share of the aggregate |magnitude|, and the lever that
    dominates the most encounters (the method with the largest magnitude on
    each encounter casts one vote for its lever)."""
    if not signatures:
        return SignatureMix(Decimal(0), Decimal(0), Decimal(0), "fee-schedule", {})
    with money_context():
        m1_total = sum((_magnitude(s.m1) for s in signatures), start=Decimal(0))
        m2_total = sum((_magnitude(s.m2) for s in signatures), start=Decimal(0))
        m3_total = sum((_magnitude(s.m3) for s in signatures), start=Decimal(0))
        grand = m1_total + m2_total + m3_total
        if grand > 0:
            m1_share = (m1_total / grand).quantize(FOUR)
            m2_share = (m2_total / grand).quantize(FOUR)
            m3_share = (m3_total / grand).quantize(FOUR)
        else:
            m1_share = m2_share = m3_share = Decimal(0)

    lever_counts: Counter[Lever] = Counter()
    pattern_counts: dict[str, int] = defaultdict(int)
    for s in signatures:
        pattern_counts[s.pattern_id] += 1
        dominant = max((s.m1, s.m2, s.m3), key=_magnitude)
        if dominant.lever is not None:
            lever_counts[dominant.lever] += 1
    dominant_lever: Lever = lever_counts.most_common(1)[0][0] if lever_counts else "fee-schedule"
    return SignatureMix(m1_share, m2_share, m3_share, dominant_lever, dict(pattern_counts))


def pooled_slope(points: tuple[tuple[int, Decimal], ...]) -> PooledSlope:
    """Method 0, pooled across payers, fit on ``(score, realized / multiplier)``
    (decision 7). ``insufficient spread`` when n < 8 or the score range spans
    fewer than 20 points — below either, ``fit()`` itself would refuse or the
    finding would not be trustworthy anyway."""
    if not points:
        msg = "no points to fit a pooled slope on"
        raise ValueError(msg)
    n = len(points)
    scores = [p[0] for p in points]
    score_min, score_max = min(scores), max(scores)
    scores_sorted = sorted(Decimal(s) for s in scores)
    score_median = int(quantile(scores_sorted, "0.5"))

    def insufficient() -> PooledSlope:
        zero = Decimal(0)
        zero_interval = Interval(zero, zero, "none")
        return PooledSlope(
            n, score_min, score_max, score_median, zero, zero_interval, zero,
            zero_interval, "insufficient spread", zero,
        )  # fmt: skip

    if n < 8 or (score_max - score_min) < 20:
        return insufficient()

    try:
        f = fit("method0-pooled", tuple((Decimal(s), y) for s, y in points))
    except NoReferenceCurveError:
        return insufficient()

    ys_sorted = sorted(y for _, y in points)
    with money_context():
        median_realized = quantile(ys_sorted, "0.5")
        if median_realized == 0:
            normalized_slope = Decimal(0)
            normalized_interval = Interval(Decimal(0), Decimal(0), f.slope_interval.method)
        else:
            factor = (Decimal(10) / median_realized) * Decimal(100)
            normalized_slope = (f.slope * factor).quantize(FOUR)
            normalized_interval = Interval(
                (f.slope_interval.low * factor).quantize(FOUR),
                (f.slope_interval.high * factor).quantize(FOUR),
                f.slope_interval.method,
            )
    verdict: Literal["flat", "rising"] = "rising" if f.differentiates else "flat"
    return PooledSlope(
        n, score_min, score_max, score_median, f.slope, f.slope_interval,
        normalized_slope, normalized_interval, verdict, f.r_squared,
    )  # fmt: skip


def comparator_codes(study: Distribution, comparators: dict[str, Distribution]) -> tuple[str, ...]:
    """Comparator codes whose score IQR overlaps the study code's (decision 8)."""
    return tuple(
        sorted(code for code, d in comparators.items() if d.q1 <= study.q3 and study.q1 <= d.q3)
    )


def instrument_health(
    site_missing_markers: dict[str, tuple[tuple[str, ...], ...]],
    markers: tuple[tuple[str, str], ...],
    *,
    rulebook_version: str,
    weights_version: str,
    rule_pack_digest: str,
) -> InstrumentHealth:
    """Missingness per marker per site, read straight from each scored
    encounter's own missing-marker flags (decision 6, CONTRACT-SEEDS.md),
    not inferred from which markers happened to be present.
    ``site_missing_markers`` maps a site to one tuple of *missing* marker ids
    (``ScoredEncounter.missing`` / ``EncounterRow.missing_markers``) per
    encounter at that site; ``markers`` is the rule pack's full marker list
    as ``(marker_id, provenance)``. Kappa has no data source yet (decision
    10) and is always ``None``."""
    out: list[MarkerHealth] = []
    for marker_id, provenance in markers:
        missingness: dict[str, Decimal] = {}
        for site, encounters in sorted(site_missing_markers.items()):
            total = len(encounters)
            if total == 0:
                missingness[site] = Decimal(0)
                continue
            missing_count = sum(1 for row in encounters if marker_id in row)
            with money_context():
                missingness[site] = (Decimal(missing_count) / Decimal(total)).quantize(FOUR)
        out.append(MarkerHealth(marker_id, provenance, provenance == "rule", missingness, None))
    return InstrumentHealth(tuple(out), rulebook_version, weights_version, rule_pack_digest)


def payer_friction(
    rows: tuple[tuple[str, bool, bool, tuple[str, ...], tuple[str, ...]], ...],
) -> tuple[PayerFrictionRow, ...]:
    """Denial/downcode rates and reason-code tallies per payer (decision 11).
    ``rows`` are ``(payer_label, denied, downcoded, carc, rarc)`` per line."""
    grouped: dict[str, list[tuple[bool, bool, tuple[str, ...], tuple[str, ...]]]]
    grouped = defaultdict(list)
    for payer_label, denied, downcoded, carc, rarc in rows:
        grouped[payer_label].append((denied, downcoded, carc, rarc))

    out: list[PayerFrictionRow] = []
    for payer_label in sorted(grouped):
        group = grouped[payer_label]
        n = len(group)
        denied_n = sum(1 for d, _, _, _ in group if d)
        downcoded_n = sum(1 for _, dc, _, _ in group if dc)
        carc_counts: Counter[str] = Counter()
        rarc_counts: Counter[str] = Counter()
        for _, _, carc, rarc in group:
            carc_counts.update(carc)
            rarc_counts.update(rarc)
        with money_context():
            denial_rate = (Decimal(denied_n) / Decimal(n)).quantize(FOUR) if n else Decimal(0)
            downcode_rate = (Decimal(downcoded_n) / Decimal(n)).quantize(FOUR) if n else Decimal(0)
        out.append(
            PayerFrictionRow(
                payer_label,
                n,
                denied_n,
                downcoded_n,
                denial_rate,
                downcode_rate,
                dict(carc_counts),
                dict(rarc_counts),
            )
        )
    return tuple(out)


def headline(card: CodeCard) -> str:
    """``"{code}: {verdict} slope; {ratio at median}, {ratio at p90}; ..."``,
    with an em dash standing in for anything not computed."""
    verdict = card.method0.verdict if card.method0 else "—"
    median = next((r for r in card.ratio_at if r.point == "median"), None)
    p90 = next((r for r in card.ratio_at if r.point == "p90"), None)
    median_s = str(median.ratio) if median and median.ratio else "—"
    p90_s = str(p90.ratio) if p90 and p90.ratio else "—"
    shortfall_s = f"${card.shortfall.total:,.2f}" if card.shortfall else "—"
    share_s = f"{card.shortfall.top_quintile_share * 100:.0f}" if card.shortfall else "—"
    dominant_s: str = card.signature_mix.dominant_lever if card.signature_mix else "—"
    return (
        f"{card.code}: {verdict} slope; {median_s} at median, {p90_s} at p90; "
        f"{shortfall_s} shortfall across {card.n} cases, {share_s}% in the top quintile; "
        f"{dominant_s} dominant."
    )


def assert_single_class(cards: tuple[CodeCard, ...]) -> None:
    """Refuse to pool ``CodeCard``s from more than one encounter class.

    Decision 4 (CONTRACT-PACKS-MC.md): "never pool across classes." Every
    function in this module that takes a tuple of cards rather than one
    calls this first, so mixing classes fails loudly, as a
    :class:`~worth_complexity.models.MethodologyViolation`, at the point
    they were pooled rather than as a confusing number three views later.
    """
    classes = {c.encounter_class for c in cards}
    if len(classes) > 1:
        msg = (
            f"cards from more than one encounter class were pooled: {sorted(classes)}. "
            "Scores and dollars are comparable only within a class (CONTRACT-PACKS.md "
            "decision 7) -- flatten and label them by class instead of pooling."
        )
        raise MethodologyViolation(msg)


def compare_rows(
    cards: tuple[CodeCard, ...], *, by: Literal["codes", "domains"] = "codes"
) -> tuple[CompareRow, ...]:
    """One row per code for the cross-code compare view.

    Decision 9: never compare raw scores across encounter classes; this row
    carries ``encounter_class`` so the caller can enforce that, and
    :func:`assert_single_class` refuses a ``cards`` tuple that already mixes
    them. Decision 6 (CONTRACT-PACKS.md) adds ``by="domains"``: cards are
    ordered by ``(service_line, code)`` instead of the caller's own order,
    so codes in the same domain sit together, e.g. every MENOPAUSE code
    before every MATERNITY one. Every row's ``domain`` field is populated
    from the card's ``service_line`` either way; ``by`` only changes the
    ordering. See also :func:`domain_rows`, which collapses a class's cards
    to one row per domain instead of reordering them.
    """
    assert_single_class(cards)
    ordered = cards
    if by == "domains":
        ordered = tuple(sorted(cards, key=lambda c: (c.service_line, c.code)))
    out: list[CompareRow] = []
    for card in ordered:
        median = next((r for r in card.ratio_at if r.point == "median"), None)
        p90 = next((r for r in card.ratio_at if r.point == "p90"), None)
        values = [v for m in card.instrument_health.markers for v in m.missingness_by_site.values()]
        with money_context():
            missingness = (
                (sum(values, start=Decimal(0)) / Decimal(len(values))).quantize(FOUR)
                if values
                else Decimal(0)
            )
        out.append(
            CompareRow(
                code=card.code,
                encounter_class=card.encounter_class,
                domain=card.service_line,
                n=card.n,
                normalized_slope=card.method0.normalized_slope if card.method0 else None,
                normalized_interval=card.method0.normalized_interval if card.method0 else None,
                ratio_median=median.ratio if median else None,
                ratio_p90=p90.ratio if p90 else None,
                shortfall=card.shortfall.total if card.shortfall else None,
                top_quintile_share=card.shortfall.top_quintile_share if card.shortfall else None,
                dominant_lever=card.signature_mix.dominant_lever if card.signature_mix else None,
                m1_share=card.signature_mix.m1_share if card.signature_mix else None,
                m2_share=card.signature_mix.m2_share if card.signature_mix else None,
                m3_share=card.signature_mix.m3_share if card.signature_mix else None,
                missingness=missingness,
                rulebook_version=card.rulebook_version,
                weights_version=card.weights_version,
                verdict=card.method0.verdict if card.method0 else None,
            )
        )
    return tuple(out)


def domain_rows(class_run: ClassRun) -> tuple[CompareRow, ...]:
    """One row per ``service_line`` within one class, not one row per code
    (decision 5, CONTRACT-PACKS-MC.md).

    ``compare_rows(..., by="domains")`` only reorders a class's existing
    per-code cards; this collapses them. Every study encounter in
    ``class_run`` is grouped by its own encounter's ``service_line`` — the
    domain a code's card already carries as ``service_line`` — and the same
    computations :func:`code_card` runs per code (pooled Method 0 slope,
    ratio at median/p90, shortfall, signature shares, dominant lever,
    missingness) run once over the whole domain's cohort instead, spanning
    however many codes that domain actually bills. ``code`` on the returned
    row is the domain's modal code, standing in for a domain that reduces to
    no single real code; ``n`` is the domain's whole study cohort, not one
    code's slice of it.

    On the visit fixture, grouping the study cohort by ``service_line``
    yields exactly two rows, MENOPAUSE and MATERNITY (CONTRACT-PACKS-MC.md
    decision 5's worked check) — the study lines' own domains, not the
    visit class's comparator specialties, since only study encounters carry
    an :class:`~worth_complexity.models.Adequacy` for :func:`code_card`'s
    inputs to reuse.
    """
    adequacy_by_id = {a.encounter_id: a for a in class_run.adequacies}
    by_domain: dict[str, list[tuple[Adequacy, Observation]]] = defaultdict(list)
    for o in class_run.observations:
        if o.cohort is not Cohort.STUDY:
            continue
        a = adequacy_by_id.get(o.scored.encounter.encounter_id)
        if a is None:
            continue
        by_domain[o.scored.encounter.service_line].append((a, o))

    pack_markers = tuple((m.marker_id, m.provenance) for m in class_run.pack.markers)
    out: list[CompareRow] = []
    for domain in sorted(by_domain):
        pairs = by_domain[domain]
        rows = tuple(encounter_row(a, o) for a, o in pairs)
        code_counts = Counter(o.scored.encounter.primary_cpt for _, o in pairs)
        modal_code = code_counts.most_common(1)[0][0]

        points = tuple(
            ScatterPoint(
                r.encounter_id, r.score, r.realized, r.payer_label,
                _schedule_scale(r.realized, r.multiplier),
            )
            for r in rows
        )  # fmt: skip
        code_of = {
            o.scored.encounter.encounter_id: o.scored.encounter.primary_cpt for _, o in pairs
        }
        slope, slope_interval, verdict = _domain_slope(code_of, points)
        at = ratio_at(rows)
        median = next((r for r in at if r.point == "median"), None)
        p90 = next((r for r in at if r.point == "p90"), None)
        sf = shortfall(rows)
        mix = signature_mix(tuple(r.signature for r in rows))

        site_markers: dict[str, list[tuple[str, ...]]] = defaultdict(list)
        for r in rows:
            site_markers[r.site].append(r.missing_markers)
        health = instrument_health(
            {site: tuple(v) for site, v in site_markers.items()},
            pack_markers,
            rulebook_version=class_run.rulebook_version,
            weights_version=class_run.weights_version,
            rule_pack_digest=class_run.pack.digest,
        )
        values = [v for m in health.markers for v in m.missingness_by_site.values()]
        with money_context():
            missingness = (
                (sum(values, start=Decimal(0)) / Decimal(len(values))).quantize(FOUR)
                if values
                else Decimal(0)
            )

        out.append(
            CompareRow(
                code=modal_code,
                encounter_class=class_run.encounter_class,
                domain=domain,
                n=len(rows),
                normalized_slope=slope,
                normalized_interval=slope_interval,
                ratio_median=median.ratio if median else None,
                ratio_p90=p90.ratio if p90 else None,
                shortfall=sf.total,
                top_quintile_share=sf.top_quintile_share,
                dominant_lever=mix.dominant_lever,
                m1_share=mix.m1_share,
                m2_share=mix.m2_share,
                m3_share=mix.m3_share,
                missingness=missingness,
                rulebook_version=class_run.rulebook_version,
                weights_version=class_run.weights_version,
                verdict=verdict,
            )
        )
    return tuple(out)


def _domain_slope(
    code_of: dict[str, str], points: tuple[ScatterPoint, ...]
) -> tuple[Decimal | None, Interval | None, str | None]:
    """A domain's Method 0 slope: within each of its codes, never across them.

    A domain such as a surgical service line spans codes that pay at
    different levels, and a line fitted through all of them measures how the
    fee schedule prices those codes against each other, not whether any one
    code pays more for a harder case. So each code is fitted on its own (the
    same fit the code card carries) and the domain reports the n-weighted
    mean of the codes' normalized slopes and interval bounds. The verdict is
    ``rising`` if any member code rises, else ``flat`` if any is flat, else
    ``insufficient spread``; codes too thin to fit contribute nothing.
    """
    by_code: dict[str, list[tuple[int, Decimal]]] = defaultdict(list)
    for p in points:
        by_code[code_of[p.encounter_id]].append((p.score, p.schedule_scale))
    fits: list[tuple[int, PooledSlope]] = []
    for code in sorted(by_code):
        pts = tuple(by_code[code])
        try:
            fits.append((len(pts), pooled_slope(pts)))
        except (ValueError, NoReferenceCurveError):
            continue
    if not fits:
        return None, None, None
    total = Decimal(sum(n for n, _ in fits))
    with money_context():
        slope = (sum((n * f.normalized_slope for n, f in fits), start=Decimal(0)) / total).quantize(
            FOUR
        )
        low = (
            sum((n * f.normalized_interval.low for n, f in fits), start=Decimal(0)) / total
        ).quantize(FOUR)
        high = (
            sum((n * f.normalized_interval.high for n, f in fits), start=Decimal(0)) / total
        ).quantize(FOUR)
    verdicts = {f.verdict for _, f in fits}
    verdict = (
        "rising"
        if "rising" in verdicts
        else ("flat" if "flat" in verdicts else "insufficient spread")
    )
    return slope, Interval(low, high, "n-weighted mean of per-code t intervals"), verdict


def queue(rows: tuple[QueueRow, ...]) -> tuple[QueueItem, ...]:
    """Rank the work queue by evidence strength, then by code percentile,
    both descending. ``suggested_vehicle`` is "Modifier 22" for an
    above-top-quintile case with no missed-code flag, else the missed
    flag's candidate code, else ``None``."""

    def vehicle(r: QueueRow) -> str | None:
        if r.score >= r.top_quintile_cutoff and not r.has_missed_flag:
            return "Modifier 22"
        if r.has_missed_flag:
            return r.missed_candidate_code
        return None

    ranked = sorted(
        rows,
        key=lambda r: (-_STRENGTH_RANK.get(r.evidence_strength or "", 0), -r.code_percentile),
    )
    return tuple(
        QueueItem(
            r.encounter_id, r.code, r.score, r.code_percentile, r.flags,
            r.evidence_strength, vehicle(r), r.outcome_835, r.payer_label, r.site, r.surgeon,
        )
        for r in ranked
    )  # fmt: skip


def _schedule_scale(realized: Decimal, multiplier: Decimal) -> Decimal:
    with money_context():
        return (realized / multiplier).quantize(CENTS)


def code_card(
    *,
    code: str,
    encounter_class: str,
    service_line: str,
    n: int,
    rows: tuple[EncounterRow, ...],
    strata: tuple[Stratum, ...],
    comparator_codes: tuple[str, ...],
    comparator_points: tuple[ScatterPoint, ...],
    comparator_slope: PooledSlope | None,
    method1: object | None,
    markers: tuple[tuple[str, str], ...],
    rulebook_version: str,
    weights_version: str,
    rule_pack_digest: str,
    suppressed: bool,
    suppression_reason: str | None,
) -> CodeCard:
    """Assemble one code's card from plain per-encounter rows.

    ``strata`` (per-payer Method 0, from ``adequacy.py``), ``comparator_codes``
    /``comparator_points``/``comparator_slope`` (cross-code context) and
    ``method1`` (opaque, see the module docstring) come from the caller,
    since they need data ``rows`` alone does not carry. Everything else —
    the histogram, deciles, shortfall, signature mix, this code's own pooled
    slope, instrument health and payer friction — is computed here from
    ``rows``, which is what makes this function unit-testable without a
    pipeline run.
    """
    if not rows:
        msg = "code_card requires at least one encounter row"
        raise ValueError(msg)

    scores = tuple(r.score for r in rows)
    hist = histogram(scores)
    top_cutoff = _top_quintile_cutoff(scores)
    distribution = describe(tuple(Decimal(s) for s in scores))
    deciles = ratio_by_decile(rows)
    at = ratio_at(rows)
    sf = shortfall(rows)
    mix = signature_mix(tuple(r.signature for r in rows))

    points = tuple(
        ScatterPoint(
            r.encounter_id, r.score, r.realized, r.payer_label,
            _schedule_scale(r.realized, r.multiplier),
        )
        for r in rows
    )  # fmt: skip
    method0 = pooled_slope(tuple((p.score, p.schedule_scale) for p in points))

    adequacy_ratio: Ratio | None = None
    adequacy_interval: Interval | None = None
    with money_context():
        realized_sum = sum((r.realized for r in rows), start=Decimal(0))
        expected_sum = sum((r.expected for r in rows), start=Decimal(0))
    if expected_sum > 0:
        adequacy_ratio = ratio_of(realized_sum, expected_sum)
        if len(rows) >= 2:
            pairs = tuple((r.realized, r.expected) for r in rows)
            adequacy_interval = bootstrap_ratio(pairs, seed=_seed_for(code))

    site_markers: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    for r in rows:
        site_markers[r.site].append(r.missing_markers)
    health = instrument_health(
        {site: tuple(v) for site, v in site_markers.items()},
        markers,
        rulebook_version=rulebook_version,
        weights_version=weights_version,
        rule_pack_digest=rule_pack_digest,
    )

    friction_rows = tuple(
        (r.payer_label, r.friction[0], r.friction[1], r.friction[2], r.friction[3]) for r in rows
    )
    friction = payer_friction(friction_rows)

    card = CodeCard(
        code=code,
        encounter_class=encounter_class,
        service_line=service_line,
        n=n,
        scored=len(rows),
        method0=method0,
        strata=strata,
        comparator_codes=comparator_codes,
        comparator_slope=comparator_slope,
        points=points,
        comparator_points=comparator_points,
        distribution=distribution,
        histogram=hist,
        top_quintile_cutoff=top_cutoff,
        ratio_by_decile=deciles,
        ratio_at=at,
        adequacy=adequacy_ratio,
        adequacy_interval=adequacy_interval,
        shortfall=sf,
        signature_mix=mix,
        method1=method1,
        payer_friction=friction,
        instrument_health=health,
        headline="",
        rulebook_version=rulebook_version,
        weights_version=weights_version,
        suppressed=suppressed,
        suppression_reason=suppression_reason,
    )
    return replace(card, headline=headline(card))


# --------------------------------------------------------------------------
# Over time (decision 7, CONTRACT-SEEDS.md)
# --------------------------------------------------------------------------


def _period_label(d: date, granularity: Granularity) -> str:
    """``YYYY-MM``, ``YYYY-Qn`` or ``YYYY-Hn``."""
    if granularity == "month":
        return f"{d.year:04d}-{d.month:02d}"
    if granularity == "quarter":
        return f"{d.year:04d}-Q{(d.month - 1) // 3 + 1}"
    return f"{d.year:04d}-H{(d.month - 1) // 6 + 1}"


def _period_ratio(rows: list[PeriodRow], *, seed: int) -> tuple[Ratio | None, Interval | None]:
    """Sum-of-realized over sum-of-expected across ``rows``, with a bootstrap
    interval when there are at least two of them. ``None``/``None`` for an
    empty group or one whose expected total is not positive -- the same
    guard :func:`code_card` applies to the whole-cohort ratio."""
    if not rows:
        return None, None
    with money_context():
        realized = sum((r.realized for r in rows), start=Decimal(0))
        expected = sum((r.expected for r in rows), start=Decimal(0))
    if expected <= 0:
        return None, None
    ratio = ratio_of(realized, expected)
    interval: Interval | None = None
    if len(rows) >= 2:
        pairs = tuple((r.realized, r.expected) for r in rows)
        interval = bootstrap_ratio(pairs, seed=seed)
    return ratio, interval


def _period_point(period: str, rows: list[PeriodRow], *, seed: int) -> PeriodPoint:
    """One period's point, suppressed (decision 7) below the floor -- ``n``
    is still reported, ``ratio``/``interval`` are not."""
    n = len(rows)
    if n < SUPPRESSION_THRESHOLD:
        return PeriodPoint(period, n, None, None, True)
    ratio, interval = _period_ratio(rows, seed=seed)
    return PeriodPoint(period, n, ratio, interval, False)


def _points_for(
    rows: list[PeriodRow], *, granularity: Granularity, seed_base: int
) -> tuple[PeriodPoint, ...]:
    by_period: dict[str, list[PeriodRow]] = defaultdict(list)
    for r in rows:
        by_period[_period_label(r.service_date, granularity)].append(r)
    return tuple(
        _period_point(period, by_period[period], seed=seed_base + i)
        for i, period in enumerate(sorted(by_period))
    )


def _choose_granularity(
    rows: list[PeriodRow], requested: Granularity | Literal["auto"], *, seed_base: int
) -> Granularity:
    """The finest bucketing at which at least half of this code's points
    clear the suppression floor; ``half`` when none does. An explicit
    request is honoured as given."""
    if requested != "auto":
        return requested
    for candidate in GRANULARITIES:
        points = _points_for(rows, granularity=candidate, seed_base=seed_base)
        if points and sum(1 for p in points if not p.suppressed) * 2 >= len(points):
            return candidate
    return "half"


def _period_summary(rows: list[PeriodRow], *, seed: int) -> PeriodSummary | None:
    """One side of a policy date. ``None`` when nothing fell on this side --
    ``describe()`` has no empirical distribution to build without at least
    one score, and there is nothing else to report either."""
    if not rows:
        return None
    distribution = describe(tuple(Decimal(r.score) for r in rows))
    ratio, interval = _period_ratio(rows, seed=seed)
    mix = signature_mix(tuple(r.signature for r in rows))
    return PeriodSummary(len(rows), distribution, ratio, interval, mix)


def periods(
    rows: tuple[PeriodRow, ...],
    *,
    policy_date: date | None,
    granularity: Granularity | Literal["auto"] = "auto",
    rulebook_version: str = "",
    weights_version: str = "",
    by: Literal["code", "domain"] = "code",
) -> tuple[PeriodSeries, ...]:
    """One :class:`PeriodSeries` per code: the ratio over calendar time, a
    pre/post split at ``policy_date`` when one is given, and the same series
    again broken out by payer (decision 7, CONTRACT-SEEDS.md).

    Built from plain per-encounter rows, the same discipline :func:`code_card`
    follows, so a caller can hand-build a handful of :class:`PeriodRow`\\ s
    and get a real series back with no pipeline run in the loop.
    Suppression (``n < 11`` -> ratio withheld) applies independently to every
    point, to every payer's own points, and is not applied at all to
    ``pre``/``post`` (a policy-change cohort spans many periods pooled
    together specifically to clear the floor; the *points* are where a
    single thin month needs to hide).
    """
    by_code: dict[str, list[PeriodRow]] = defaultdict(list)
    for r in rows:
        by_code[r.service_line if by == "domain" else r.code].append(r)

    out: list[PeriodSeries] = []
    for key in sorted(by_code):
        code_rows = by_code[key]
        # A domain series is labelled by its modal code; the domain itself
        # travels beside it so a reader sees both.
        code = key if by == "code" else Counter(r.code for r in code_rows).most_common(1)[0][0]
        domain = key if by == "domain" else None
        seed_base = _seed_for(key)
        chosen = _choose_granularity(code_rows, granularity, seed_base=seed_base)
        points = _points_for(code_rows, granularity=chosen, seed_base=seed_base)

        pre: PeriodSummary | None = None
        post: PeriodSummary | None = None
        if policy_date is not None:
            pre_rows = [r for r in code_rows if r.service_date < policy_date]
            post_rows = [r for r in code_rows if r.service_date >= policy_date]
            pre = _period_summary(pre_rows, seed=seed_base + 1_000_003)
            post = _period_summary(post_rows, seed=seed_base + 2_000_003)

        by_payer_rows: dict[str, list[PeriodRow]] = defaultdict(list)
        for r in code_rows:
            by_payer_rows[r.payer_label].append(r)
        by_payer = tuple(
            PayerSeries(
                payer_label,
                _points_for(
                    by_payer_rows[payer_label],
                    granularity=chosen,
                    seed_base=seed_base + 3_000_003 + i,
                ),
            )
            for i, payer_label in enumerate(sorted(by_payer_rows))
        )

        out.append(
            PeriodSeries(
                code=code,
                domain=domain,
                granularity=chosen,
                points=points,
                pre=pre,
                post=post,
                by_payer=by_payer,
                policy_date=policy_date,
                rulebook_version=rulebook_version,
                weights_version=weights_version,
            )
        )
    return tuple(out)
