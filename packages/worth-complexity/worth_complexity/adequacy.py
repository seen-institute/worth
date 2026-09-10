"""The payment adequacy ratio.

    adequacy = realized payment / complexity-matched expected payment

Both sides are dollars, so the ratio is dimensionless: 1.00 means paid
consistently with what this payer already pays for equivalent measured
complexity elsewhere in medicine; 0.70 means seventy cents on that dollar.

The numerator is the 835 allowed amount on the primary procedure's line. The
denominator is built in two steps, and the fee schedule is what turns a
complexity level into dollars:

1. **The schedule curve.** Every comparator encounter is priced through
   ``worth-fees`` at one reference release: the Medicare Physician Fee
   Schedule allowed amount for its primary code, in the institution's
   locality and setting. A line is fitted through (Layer A score, PFS amount)
   across the whole comparator cohort. That line is the fee schedule's own
   implicit price of complexity in specialties whose valuation is not in
   dispute, on the Medicare scale, with no payer in it.

2. **The payer multiplier.** The 835 is in the payer's contract dollars and
   the curve is in Medicare dollars. Commercial contracts are written as a
   multiple of Medicare, so the multiple is measured rather than assumed: for
   each payer, the median of realized over PFS across its own comparator
   encounters, each priced at the release in force on its service date. The
   expected payment for a study encounter is that multiple times the curve at
   its score.

The multiplier is a payer's contract level. It is an internal step of the
arithmetic and stays with the partner: the encounter-level trace names it so
the institution can check its own number by hand, and no index record ever
carries it. The complexity score never appears in a division: it selects the
point on the curve, and that is all it does.

This replaces the earlier per-payer curve fitted on comparator 835 amounts,
which observed the same relation with the contract level folded in. Factoring
it out makes the schedule's relation a statement about the fee schedule, and
the multiplier a statement about the contract, and lets each be checked.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import lru_cache
from typing import TYPE_CHECKING

from worth_fees import PlaceOfService, WorthFeesError, expected_allowed
from worth_fees.models import VintageError
from worth_fees.sources import PINNED_VINTAGES, load, vintage_for_date

from worth_complexity.curve import Fit, fit
from worth_complexity.models import (
    SUPPRESSION_THRESHOLD,
    Adequacy,
    Cohort,
    ComplexityScore,
    IndexRecord,
    NoReferenceCurveError,
    PayerFriction,
    Ratio,
    ScoredEncounter,
    Stratum,
    WorthComplexityError,
    ratio_of,
)
from worth_complexity.money import money_context
from worth_complexity.sampling import Interval, bootstrap_ratio, describe

if TYPE_CHECKING:
    from collections.abc import Callable

    from worth_fees.models import FeeDerivation
    from worth_fees.sources import FeeSchedule, Vintage

    from worth_complexity.linkage import LinkedEncounter
    from worth_complexity.method1 import Method1Flag
    from worth_complexity.method3 import Method3
    from worth_complexity.population import CodeCard
    from worth_complexity.rulepack import RulePack
    from worth_complexity.signature import Decomposition, DollarSpine, Signature
    from worth_complexity.witness import Witness

type SchedulePool = Callable[[int, int], FeeSchedule]
"""Where the fee schedule for a (rule year, quarter) comes from.

The default reads the fixture ``worth-fees`` ships. A caller with the full
release, read back out of a database say, supplies its own. Either way the
formula is ``worth-fees``' own; only the rows differ.
"""

CENTS = Decimal("0.01")


@lru_cache(maxsize=8)
def fixture_schedules(rule_year: int, quarter: int) -> FeeSchedule:
    """The committed worth-fees fixture, the offline default."""
    return load(rule_year, quarter)


class PricingError(WorthComplexityError):
    """A comparator encounter could not be priced, so no curve can be fitted.

    Raised rather than skipped: a curve fitted on the comparators that happened
    to price would be a curve fitted on a cohort nobody chose.
    """


@dataclass(frozen=True, slots=True)
class Observation:
    """One scored, linked encounter: everything the arithmetic below needs."""

    scored: ScoredEncounter
    linked: LinkedEncounter

    @property
    def payer_id(self) -> str:
        return self.linked.payer_id

    @property
    def payer_name(self) -> str:
        return self.linked.payer_name

    @property
    def score(self) -> ComplexityScore:
        return self.scored.score

    @property
    def cohort(self) -> Cohort:
        return self.scored.encounter.cohort

    @property
    def realized(self) -> Decimal:
        """The primary line's allowed amount. See :attr:`LinkedEncounter.realized`."""
        return self.linked.realized


@dataclass(frozen=True, slots=True)
class PricedEncounter:
    """A comparator encounter's fee-schedule amounts, with their derivations."""

    encounter_id: str
    cpt: str
    at_date: FeeDerivation
    """Priced at the release in force on the service date. Feeds the multiplier."""
    at_reference: FeeDerivation
    """Priced at the run's reference release. Feeds the schedule curve."""
    beyond_pinned: bool = False
    """True when the service date falls after the newest pinned CMS release
    and ``at_date`` was priced at that release instead (see
    :func:`vintage_in_force`). Reported, never hidden."""


def vintage_in_force(service_date: date) -> tuple[Vintage, bool]:
    """The pinned release governing a service date, carried forward past the
    newest one.

    A date inside a pinned range gets that release. A date after the newest
    pinned release gets the newest release and ``True``: the schedule a
    biller applies the day CMS's next release is late, and the only honest
    choice for a service window that runs past what is pinned. A date before
    the oldest pinned release is still an error, because pricing it with a
    later schedule would be a guess in the direction that hides nothing.
    """
    try:
        return vintage_for_date(service_date), False
    except VintageError:
        newest = max(PINNED_VINTAGES.values(), key=lambda v: v.effective)
        if service_date >= newest.effective[1]:
            return newest, True
        raise


@dataclass(frozen=True, slots=True)
class Multiplier:
    """One payer's contract level as a multiple of the fee schedule.

    Measured on the payer's own comparator encounters. Partner-only: it is a
    rate, and rates never leave.
    """

    payer_id: str
    payer_label: str
    n: int
    value: Decimal
    """The median of realized / PFS."""
    q1: Decimal
    q3: Decimal
    trace: tuple[str, ...]

    def render(self) -> str:
        return (
            f"{self.payer_label:9} n={self.n:<4} x{self.value}  "
            f"IQR {self.q1}-{self.q3}  (realized / PFS over comparators)"
        )


def price_comparators(
    observations: tuple[Observation, ...],
    *,
    locality: str,
    setting: PlaceOfService,
    schedules: SchedulePool,
    reference: Vintage,
) -> dict[str, PricedEncounter]:
    """Price every comparator encounter's primary code, twice.

    Once at the release in force on its service date, for the multiplier, and
    once at the reference release, for the curve. Modifier 22 is stripped
    first; any other modifier travels through to ``worth-fees``, which refuses
    the ones that scale the amount, and that refusal becomes a
    :class:`PricingError` naming the encounter.
    """
    out: dict[str, PricedEncounter] = {}
    for obs in observations:
        if obs.cohort is not Cohort.COMPARATOR:
            continue
        enc = obs.scored.encounter
        try:
            in_force, beyond = vintage_in_force(enc.service_date)
            at_date = expected_allowed(
                enc.primary_cpt,
                enc.pricing_modifiers,
                locality,
                setting,
                in_force.rule_year,
                in_force.quarter,
                schedule=schedules(in_force.rule_year, in_force.quarter),
            )
            at_reference = expected_allowed(
                enc.primary_cpt,
                enc.pricing_modifiers,
                locality,
                setting,
                reference.rule_year,
                reference.quarter,
                schedule=schedules(reference.rule_year, reference.quarter),
            )
        except WorthFeesError as exc:
            raise PricingError(
                f"comparator encounter {enc.encounter_id} ({enc.primary_cpt}, "
                f"{enc.service_date.isoformat()}) cannot be priced: {exc}"
            ) from exc
        out[enc.encounter_id] = PricedEncounter(
            enc.encounter_id, enc.primary_cpt, at_date, at_reference, beyond_pinned=beyond
        )
    return out


def schedule_curve(
    observations: tuple[Observation, ...],
    priced: dict[str, PricedEncounter],
    reference: Vintage,
) -> Fit:
    """Fit the fee schedule's complexity relation on the comparator cohort.

    One curve, not one per payer, because the fee schedule has no payer. From
    the comparator cohort only, because a curve that included the codes under
    study would define adequacy partly in terms of the thing being tested. At
    one reference release, so the curve is a statement about one fee schedule
    rather than a blend of two years' dollars.
    """
    points = tuple(
        (Decimal(obs.score.value), priced[obs.scored.encounter.encounter_id].at_reference.amount)
        for obs in observations
        if obs.cohort is Cohort.COMPARATOR
    )
    return fit(f"schedule/{reference.label}", points)


def payer_multipliers(
    observations: tuple[Observation, ...],
    priced: dict[str, PricedEncounter],
    labels: dict[str, str],
) -> tuple[dict[str, Multiplier], tuple[str, ...]]:
    """Measure each payer's multiple of the fee schedule on its comparator encounters.

    The median rather than a fit: a contract multiple is a scalar, and the
    median is robust to the one encounter a clearinghouse repriced. A payer
    with fewer comparator encounters than the suppression floor gets no
    multiplier, and its study encounters get no ratio; the reason is returned
    rather than logged.
    """
    grouped: dict[str, list[Decimal]] = defaultdict(list)
    for obs in observations:
        if obs.cohort is not Cohort.COMPARATOR:
            continue
        pfs = priced[obs.scored.encounter.encounter_id].at_date.amount
        with money_context():
            grouped[obs.payer_id].append((obs.realized / pfs).quantize(Decimal("0.0001")))

    out: dict[str, Multiplier] = {}
    thin: list[str] = []
    for payer in sorted(grouped, key=lambda p: labels[p]):
        ratios = tuple(sorted(grouped[payer]))
        if len(ratios) < SUPPRESSION_THRESHOLD:
            thin.append(
                f"{labels[payer]}: {len(ratios)} comparator encounters, "
                f"below the floor of {SUPPRESSION_THRESHOLD}; no multiplier, no ratio"
            )
            continue
        d = describe(ratios)
        four = Decimal("0.0001")
        with money_context():
            value, q1, q3 = (x.quantize(four) for x in (d.median, d.q1, d.q3))
        out[payer] = Multiplier(
            payer_id=payer,
            payer_label=labels[payer],
            n=len(ratios),
            value=value,
            q1=q1,
            q3=q3,
            trace=(
                f"comparator encounters        = {len(ratios)}",
                "realized / PFS, each at the release in force on its service date",
                f"min / q1 / median / q3 / max = {d.minimum} / {q1} / {value} / {q3} / {d.maximum}",
                f"multiplier = median          = {value}",
            ),
        )
    return out, tuple(thin)


def adequacy(
    obs: Observation,
    m3: Method3,
    curve: Fit,
    multiplier: Multiplier,
    *,
    spine: DollarSpine,
    decomposition: Decomposition,
    signature: Signature,
    method1_flags: tuple[Method1Flag, ...],
    payer_friction: PayerFriction,
    witness: Witness,
    rulebook_version: str,
    weights_version: str,
    encounter_class: str,
) -> Adequacy:
    """Compute one encounter's payment adequacy ratio, with its derivation.

    Decision 2: the ratio's denominator is the Method 3 band expected payment,
    not the Method 2 schedule curve read at this encounter's own score.
    ``schedule_expected`` (Method 2) is still computed and carried, always
    extrapolated (the compression component in ``spine`` needs it at every
    score, including the code's own median, which can sit outside the curve's
    fitted range) — Method 3 is the one that can refuse: see
    ``method3.band_expected``, which raises rather than widening past the
    whole 0-100 scale.
    """
    schedule_expected = curve.predict(Decimal(obs.score.value), allow_extrapolation=True)
    ratio = ratio_of(obs.realized, m3.expected)
    ratio_interval = Interval(
        ratio_of(obs.realized, m3.interval.high).value,
        ratio_of(obs.realized, m3.interval.low).value,
        "m3-band-bootstrap",
    )
    enc = obs.scored.encounter
    trace = (
        f"Layer A complexity score               = {obs.score.value}",
        *m3.trace,
        f"realized (835 allowed, primary line)   = {obs.realized:.2f}",
        f"adequacy = realized / m3 expected      = {obs.realized:.2f} / {m3.expected:.2f}"
        f" = {ratio}",
        f"ratio interval = realized / m3 CI, low expected -> high ratio "
        f"= [{obs.realized:.2f}/{m3.interval.high:.2f}, {obs.realized:.2f}/{m3.interval.low:.2f}]"
        f" = {ratio_interval.render(4)}",
        f"payer multiplier ({multiplier.payer_label}, n={multiplier.n})   "
        f"= median realized / PFS over comparators = {multiplier.value}",
    )
    return Adequacy(
        encounter_id=enc.encounter_id,
        cpt=enc.primary_cpt,
        payer_id=obs.payer_id,
        payer_name=obs.payer_name,
        score=obs.score,
        realized=obs.realized,
        schedule_expected=schedule_expected,
        multiplier=multiplier.value,
        expected=m3.expected,
        ratio=ratio,
        curve_id=curve.curve_id,
        trace=trace,
        m3=m3,
        spine=spine,
        decomposition=decomposition,
        signature=signature,
        method1_flags=method1_flags,
        payer_friction=payer_friction,
        ratio_interval=ratio_interval,
        witness=witness,
        rulebook_version=rulebook_version,
        weights_version=weights_version,
        encounter_class=encounter_class,
    )


def method_zero_slopes(
    observations: tuple[Observation, ...], labels: dict[str, str]
) -> tuple[dict[tuple[str, str], Fit], tuple[str, ...]]:
    """Fit the Method 0 slope for every (code, payer) stratum with enough cases.

    Within one code and one payer, does realized payment move as documented
    complexity rises? Stratified by payer because payment levels differ by
    contract, and an unstratified fit would report contract mix as if it were
    complexity sensitivity.

    Strata below the minimum are returned as a list of reasons rather than
    silently omitted. A code that produced no slope because the cohort was too
    thin looks identical, in a finished report, to a code that was never
    examined, and the difference matters to whoever reads it.
    """
    grouped: dict[tuple[str, str], list[tuple[Decimal, Decimal]]] = defaultdict(list)
    for obs in observations:
        if obs.cohort is not Cohort.STUDY:
            continue
        key = (obs.scored.encounter.primary_cpt, obs.payer_id)
        grouped[key].append((Decimal(obs.score.value), obs.realized))

    out: dict[tuple[str, str], Fit] = {}
    dropped: list[str] = []
    for (cpt, payer), points in sorted(grouped.items()):
        try:
            out[(cpt, payer)] = fit(f"method0/{cpt}/{labels[payer]}", tuple(points))
        except NoReferenceCurveError as exc:
            dropped.append(f"{cpt} / {labels[payer]}: {exc}")
    return out, tuple(dropped)


def code_strata(
    cohort: list[Observation], code: str, labels: dict[str, str]
) -> tuple[Stratum, ...]:
    """One Method 0 fit per payer, for one code's study cohort.

    Shared between :func:`index_records` and the population stage's per-code
    card (``population.CodeCard.strata``, decision 8's context for the compare
    view): both need the identical per-payer fit, and computing it twice from
    the same points would only risk the two disagreeing.
    """
    strata: list[Stratum] = []
    by_payer: dict[str, list[Observation]] = defaultdict(list)
    for obs in cohort:
        by_payer[obs.payer_id].append(obs)
    for payer in sorted(by_payer, key=lambda p: labels[p]):
        group = by_payer[payer]
        points = tuple((Decimal(o.score.value), o.realized) for o in group)
        thin = len(group) < SUPPRESSION_THRESHOLD
        try:
            f = fit(f"method0/{code}/{labels[payer]}", points)
        except NoReferenceCurveError:
            strata.append(
                Stratum(
                    labels[payer],
                    len(group),
                    Decimal(0),
                    Interval(Decimal(0), Decimal(0), "none"),
                    Decimal(0),
                    False,
                    True,
                )
            )
            continue
        strata.append(
            Stratum(
                payer_label=labels[payer],
                n=len(group),
                slope=f.slope,
                slope_interval=f.slope_interval,
                r_squared=f.r_squared,
                differentiates=f.differentiates,
                suppressed=thin,
            )
        )
    return tuple(strata)


def index_records(
    observations: tuple[Observation, ...],
    adequacies: tuple[Adequacy, ...],
    *,
    institution: str,
    labels: dict[str, str],
    linkage_rate: Decimal,
    pack: RulePack,
    provenance_filter: tuple[str, ...],
    package_version: str,
    locality: str,
    setting: PlaceOfService,
    reference: Vintage,
    cards: dict[str, CodeCard],
) -> tuple[IndexRecord, ...]:
    """Build one published record per study code.

    Per code, because that is the unit the registry accumulates. A figure
    averaged across several codes has no home in the methodology: each code is
    valued separately, fails separately, and is petitioned separately, so
    blending them produces a number that names no reform lever.

    Every record carries the distribution and the Method 0 strata whether or not
    an adequacy ratio could be computed, because those two are available from the
    first validated extract and the ratio is not. No record carries a payer's
    multiplier: that is a rate, and rates stay with the partner.
    """
    study = [o for o in observations if o.cohort is Cohort.STUDY]
    by_code: dict[str, list[Observation]] = defaultdict(list)
    for obs in study:
        by_code[obs.scored.encounter.primary_cpt].append(obs)

    ratios = {a.encounter_id: a for a in adequacies}
    out: list[IndexRecord] = []

    for code in sorted(by_code):
        cohort = by_code[code]
        scores = tuple(Decimal(o.score.value) for o in cohort)
        dates = sorted(o.scored.encounter.service_date for o in cohort)

        strata = code_strata(cohort, code, labels)

        priced = [
            ratios[o.scored.encounter.encounter_id]
            for o in cohort
            if o.scored.encounter.encounter_id in ratios
        ]
        suppressed = len(cohort) < SUPPRESSION_THRESHOLD
        ratio: Ratio | None = None
        interval: Interval | None = None
        if priced and not suppressed:
            pairs = tuple((a.realized, a.expected) for a in priced)
            with money_context():
                realized = sum((a.realized for a in priced), start=Decimal(0))
                expected = sum((a.expected for a in priced), start=Decimal(0))
            ratio = ratio_of(realized, expected)
            if len(pairs) >= 2:
                interval = bootstrap_ratio(pairs, seed=int(code))

        out.append(
            IndexRecord(
                code=code,
                institution=institution,
                period_start=dates[0],
                period_end=dates[-1],
                n=len(cohort),
                distribution=describe(scores),
                slopes=strata,
                adequacy=ratio,
                adequacy_interval=interval,
                scored=len(priced),
                excluded=len(cohort) - len(priced),
                linkage_rate=linkage_rate,
                rule_pack_id=pack.rule_pack_id,
                rule_pack_digest=pack.digest,
                rule_pack_status=pack.status,
                rule_pack_source=pack.source,
                provenance_filter=provenance_filter,
                package_version=package_version,
                locality=locality,
                setting=setting.value,
                reference_release=reference.label,
                rulebook_version=pack.rulebook_version,
                weights_version=pack.weights_version,
                encounter_class=pack.encounter_class,
                card=cards[code],
                suppressed=suppressed,
                suppression_reason=(
                    f"n = {len(cohort)}, below the suppression threshold of {SUPPRESSION_THRESHOLD}"
                    if suppressed
                    else None
                ),
            )
        )
    return tuple(out)
