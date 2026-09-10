"""Public value types and errors for worth-complexity.

Three of these types exist to make a methodology rule unbreakable rather than
merely documented.

``ComplexityScore`` is *ordinal*. It ranks encounters against each other on a
0 to 100 instrument. It does not measure a magnitude and it is never a
denominator. Dividing money by it would produce a plausible-looking
dollars-per-complexity-point that means nothing, and that error is invisible
once it is three layers deep in a report. So the type raises instead.

``Ratio`` is the adequacy index and is always Money over Money: realized
payment over complexity-matched expected payment, both in dollars. The result
is dimensionless and comparable across codes, payers and institutions.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from worth_complexity.money import money_context

if TYPE_CHECKING:
    from worth_complexity.method1 import Method1Flag
    from worth_complexity.method3 import Method3
    from worth_complexity.population import CodeCard
    from worth_complexity.sampling import Distribution, Interval
    from worth_complexity.signature import Decomposition, DollarSpine, Signature
    from worth_complexity.witness import Witness

_CPT = re.compile(r"\d{4}[\dA-Z]")

Provenance = Literal["structured", "rule", "ml", "generative"]
"""Where a marker's value came from.

Layer A, the open, published, auditable standard, is defined by the
methodology as having no trained or generative model in the scoring path. That
promise is kept here by construction rather than by discipline: every marker
carries its provenance, and the scorer takes a provenance filter. Markers
tagged ``ml`` or ``generative`` are excluded from a Layer A score because the
filter never admits them, not because a caller remembered to leave them out.
"""

LAYER_A: frozenset[str] = frozenset({"structured", "rule"})
"""The provenance filter that defines Layer A.

Both lanes, because the methodology defines the standard as "a transparent
formula over facts extracted from the record. Facts come from structured fields
plus rule-based note reading, regex, ontologies, negation detection, section
parsing." A score computed from structured fields alone is not Layer A; it is
the subset of Layer A that needs no note dataset.
"""


PROVENANCE_RANK: dict[str, int] = {"structured": 0, "rule": 1, "ml": 2, "generative": 3}
"""Precedence when one marker is available from more than one lane.

Structured fields outrank narrative wherever both exist. A note saying the case
ran five hours is evidence; OpTime's incision-to-close timestamps are the
record. Preferring the narrative would make the score a measure of how
thoroughly a surgeon writes, which is the documentation-intensity risk the
methodology warns about, the busiest services document least, and they would
score lowest for it.

The same ordering keeps the enrichment lanes below the rule lane, so admitting
them can add markers rules cannot reach without ever rewriting one Layer A
already has.
"""

LAYER_A_STRUCTURED_ONLY: frozenset[str] = frozenset({"structured"})
"""Layer A restricted further, to structured fields alone.

The first published index is computed on this filter. It excludes rule-based
note reading, which is inside the methodology's Layer A but requires a note
dataset, a versioned rule pack and a partner AI-governance review. Nothing in
Method 0 needs any of them.
"""


SUPPRESSION_THRESHOLD = 11
"""Smallest cohort that may carry a published value.

From the partner's own governance commitment, "cell suppression below n = 11".
A value computed over fewer encounters is withheld rather than shown, because a
small cell in a single-institution index is re-identifiable in a way no amount of
de-identification upstream fixes.
"""


def blind_payers(payer_ids: Iterable[str]) -> dict[str, str]:
    """Map payer identifiers to stable, non-identifying labels.

    The methodology draws a hard line here: "The payment figure goes into a
    complexity ratio, blinded by payer, and never comes back out as a rate. A
    rate benchmark tells participants what competitors are paid; an adequacy
    index tells one institution only whether its own payment matched its own
    work."

    Stratification by payer is still required, payment levels differ by
    contract and an unstratified slope would report contract mix as complexity
    sensitivity, so the labels persist while the identities do not. The mapping
    lives only where the data lives; nothing downstream can invert it.
    """
    return {payer: f"Payer {chr(ord('A') + i)}" for i, payer in enumerate(sorted(set(payer_ids)))}


class WorthComplexityError(Exception):
    """Base class for every error this package raises."""


class MethodologyViolation(WorthComplexityError):  # noqa: N818
    """An operation was attempted that the methodology forbids.

    Named for what it is rather than with an ``Error`` suffix: this is the
    exception a reader meets when they have tried to divide money by a
    complexity score, and the name should tell them they broke a rule of the
    standard rather than that something went wrong in the code.
    """


class RulePackError(WorthComplexityError):
    """A rule pack is malformed, or its weights do not sum to one."""


class MissingMarkerError(WorthComplexityError):
    """Every structured marker the rule pack requires was absent or rejected
    for an encounter, so nothing about its complexity could be measured at
    all (decision 6, CONTRACT-SEEDS.md).

    A single missing or implausible marker no longer raises: ``scoring.score``
    scores it at zero and records it as missing instead (see
    ``ScoredEncounter.missing``), because a small documentation gap is common
    and refusing to score the whole encounter over it would drop exactly the
    encounters whose paperwork is thinnest, the same bias the old blanket
    refusal was meant to avoid but produced instead. This error is reserved
    for the one case where nothing structured survived to be scored.
    """


class UnlinkedEncounterError(WorthComplexityError):
    """No remittance could be matched to an encounter."""


class NoReferenceCurveError(WorthComplexityError):
    """No comparator curve exists for the payer, or it could not be fitted."""


class ExtrapolationError(WorthComplexityError):
    """An encounter scored outside the range the reference curve was fitted on.

    The curve is an observation about what a payer already pays for measured
    complexity. Outside the observed range it is a guess, and a guess presented
    as an expected payment is the kind of thing a hostile referee is paid to
    find.
    """


@dataclass(frozen=True, slots=True, order=True)
class ComplexityScore:
    """An ordinal Layer A complexity score, 0 to 100.

    Ranks; does not measure magnitude. Locates an encounter against its code's
    reference distribution. Never a denominator, see ``__rtruediv__``.
    """

    value: int

    def __post_init__(self) -> None:
        if not 0 <= self.value <= 100:
            msg = f"complexity score out of range: {self.value}"
            raise MethodologyViolation(msg)

    def __rtruediv__(self, other: object) -> None:
        msg = (
            "The complexity score is ordinal and is never a denominator. The "
            "adequacy index is realized payment / complexity-matched expected "
            "payment, both in dollars. See WORTH Methodology Notes, "
            "'the payment adequacy ratio'."
        )
        raise MethodologyViolation(msg)

    def __str__(self) -> str:
        return f"{self.value}/100"


@dataclass(frozen=True, slots=True)
class Ratio:
    """The payment adequacy ratio. Money over Money, dimensionless.

    ``1.0`` means paid consistently with what the same payer already pays for
    equivalent measured complexity elsewhere in medicine. ``0.70`` means
    seventy cents on that dollar.
    """

    value: Decimal

    def __str__(self) -> str:
        return f"{self.value:.2f}"


class Cohort(StrEnum):
    """Which side of the comparison an encounter sits on."""

    STUDY = "study"
    """The codes under examination."""
    COMPARATOR = "comparator"
    """Specialties whose valuation is uncontested, from which the curve is fit."""


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Where a marker value was read from, precisely enough to re-check by hand."""

    file: str
    """Filename as delivered by the partner."""
    sha256: str
    """Hash of that file as received."""
    row: int
    """1-based physical line number, header included."""
    column: str
    """Column name, or X12 segment and element position, e.g. ``AMT02``."""

    def __str__(self) -> str:
        return f"{self.file}:{self.row}:{self.column}"


@dataclass(frozen=True, slots=True)
class Marker:
    """One extracted complexity fact, with its provenance.

    The provenance field is what lets one pipeline output be scored three ways. Layer A, Layer A
    plus the ML lane, Layer A plus the generative lane, from identical inputs, differing only in the
    filter. It also means the
    question of where the ML classifier belongs can be settled by measuring the
    delta between those scores rather than by argument.
    """

    encounter_id: str
    marker_id: str
    value: Decimal
    provenance: Provenance
    source_ref: SourceRef
    extractor_id: str
    extractor_version: str
    rule_pack_digest: str | None = None
    model_id: str | None = None
    confidence: Decimal | None = None
    evidence: str | None = None
    """The text a rule matched on, verbatim.

    An auditable rule has to be checkable by a clinician, and a rule pack digest
    alone does not let anyone confirm the rule fired on the right sentence. The
    span is in ``source_ref``; this is what is inside it.
    """

    def __post_init__(self) -> None:
        model_lane = self.provenance in {"ml", "generative"}
        if model_lane and self.model_id is None:
            msg = f"{self.marker_id}: provenance {self.provenance} requires a model_id"
            raise MethodologyViolation(msg)
        if not model_lane and self.model_id is not None:
            msg = f"{self.marker_id}: provenance {self.provenance} must not name a model"
            raise MethodologyViolation(msg)


@dataclass(frozen=True, slots=True)
class Encounter:
    """One surgical case, as read from the partner's operative log."""

    encounter_id: str
    """The OR log identifier. The unit the complexity score is computed on."""
    csn: str
    """The Epic contact serial number for the visit."""
    patient_id: str
    account_id: str
    """The billing account number. The join key to the remittance."""
    service_date: date
    service_line: str
    specialty: str
    cohort: Cohort
    facility_npi: str
    primary_cpt: str
    procedures: tuple[tuple[str, str], ...]
    """``(cpt, modifier)`` for every code billed on this log, in panel order."""
    inpatient: bool
    surgeon_id: str | None = None
    """The OR log's primary surgeon id, when the extract carries one. Used by
    ``worth-cli code --by surgeon`` and the work queue; ``None`` for an
    extract that does not deliver ``primary_surgeon_id``."""
    encounter_class: str = "surgical"
    """Which extract shape and rule pack this encounter belongs to:
    ``"surgical"``, ``"visit"`` or ``"episode"`` (decision 3, CONTRACT-PACKS).
    Raw complexity scores are never compared across classes. Defaults to
    ``"surgical"`` so every existing constructor call, all of which predate
    this field, keeps meaning what it always meant."""
    clinician_id: str | None = None
    """The billing or rendering clinician for a visit or episode encounter.
    ``None`` for the surgical extract, which identifies its clinician via
    ``surgeon_id`` instead."""
    window_start: date | None = None
    """The start of the encounter's clinical window: the visit's own service
    date for a visit, or the first day of a monitoring episode. ``None`` for
    a surgical encounter, which has no window beyond ``service_date``."""
    window_end: date | None = None
    """The end of the encounter's clinical window, e.g. a 30-day episode's
    last day. ``None`` for a surgical encounter."""

    def __post_init__(self) -> None:
        if not _CPT.fullmatch(self.primary_cpt):
            msg = f"not a CPT/HCPCS code: {self.primary_cpt!r}"
            raise WorthComplexityError(msg)

    @property
    def primary_modifiers(self) -> tuple[str, ...]:
        """Modifiers billed on the primary procedure, as given."""
        return tuple(m for cpt, m in self.procedures if cpt == self.primary_cpt and m)

    @property
    def increased_service(self) -> bool:
        """Modifier 22 on the primary procedure: the surgeon's own claim that this
        case was substantially harder than typical. Recorded beside the score,
        never fed into it, and stripped before pricing because worth-fees does
        not model payment-scaling modifiers."""
        return "22" in self.primary_modifiers

    @property
    def pricing_modifiers(self) -> tuple[str, ...]:
        """The primary procedure's modifiers with 22 removed, for the fee schedule."""
        return tuple(m for m in self.primary_modifiers if m != "22")


@dataclass(frozen=True, slots=True)
class RemitLine:
    """One service line of an 835 remittance advice."""

    account_id: str
    """CLP01, the patient control number the provider submitted."""
    payer_id: str
    payer_name: str
    claim_status: str
    """CLP02. ``1`` processed as primary, ``22`` reversal, and so on."""
    cpt: str
    modifiers: tuple[str, ...]
    billed: Decimal
    """SVC02."""
    allowed: Decimal
    """AMT*B6. The realized payment for adequacy purposes."""
    paid: Decimal
    """SVC03, net of patient responsibility."""
    service_date: date
    adjustments: tuple[tuple[str, str, Decimal], ...]
    """``(group, reason, amount)`` from the CAS segments, e.g. ``("CO", "45", ...)``."""
    source_ref: SourceRef
    rarc: tuple[str, ...] = ()
    """Remark codes from ``LQ*HE*<code>`` segments, if the 835 carries them
    (decision 11). Empty when the transaction carries none."""
    claim_seq: int = 0
    """Which CLP occurrence, across the whole remittance directory, this line
    came from. Assigned in file-then-segment order (decision 6,
    CONTRACT-SEEDS.md: ``x12.read_directory`` renumbers each file's local
    count by a running offset so two CLPs for the same account, whether in
    one file or two, compare in the order they were actually received).
    Lines from the same CLP occurrence share this value; ``linkage.link``
    uses it to net a reversal (``CLP02 == 22``) against the claim occurrence
    it reverses."""

    @property
    def is_reversal(self) -> bool:
        return self.claim_status == "22"

    @property
    def denial_codes(self) -> tuple[str, ...]:
        """CARC codes on contractual-obligation adjustments that zeroed the line."""
        if self.allowed != 0:
            return ()
        return tuple(reason for group, reason, _ in self.adjustments if group == "CO")


@dataclass(frozen=True, slots=True)
class MarkerRow:
    """One rule pack marker's status on one scored encounter: present and
    scored, or missing and defaulted to zero (decision 6).

    What ``explain``/JSON renders per marker, one row per marker the rule
    pack lists under the provenance filter the encounter was scored with —
    unlike ``ScoredEncounter.markers``, which carries only the ``Marker``
    objects that were actually admitted and contributed a nonzero term.
    """

    marker_id: str
    provenance: Provenance
    weight: Decimal
    """This marker's share of the rule pack's weight total, renormalised
    over the admitted rules the same way ``scoring.score`` renormalises it."""
    value: Decimal | None
    """The raw marker value, or ``None`` when ``missing``."""
    contribution: Decimal
    """This marker's points on the 0-100 scale; ``0`` when ``missing``."""
    missing: bool
    reason: str | None
    """``None`` when present; ``"absent"`` or ``"rejected: <why>"`` when
    ``missing``."""


@dataclass(frozen=True, slots=True)
class ScoredEncounter:
    """An encounter, its Layer A score, and the arithmetic that produced it."""

    encounter: Encounter
    score: ComplexityScore
    markers: tuple[Marker, ...]
    superseded: tuple[Marker, ...]
    """Markers a higher-precedence lane displaced. Reported, not discarded."""
    trace: tuple[str, ...]
    rule_pack_id: str
    rule_pack_version: str
    rule_pack_digest: str
    provenance_filter: tuple[str, ...]
    missing: tuple[str, ...] = ()
    """Marker ids the rule pack lists (under this provenance filter) that
    were absent from the extract, or present but rejected by a plausibility
    guard, and so scored at zero (decision 6, CONTRACT-SEEDS.md). The reason
    for each lives on the matching row of :attr:`marker_rows` and in
    :attr:`trace`, not encoded into this tuple, so it stays a plain id list a
    caller can test set-membership against (e.g. ``population.EncounterRow.
    missing_markers``)."""
    marker_rows: tuple[MarkerRow, ...] = ()
    """One :class:`MarkerRow` per rule the pack admits under this provenance
    filter, present or missing, in rule-pack order — what ``explain``/JSON
    exposes per marker (decision 6)."""

    def render(self) -> str:
        e = self.encounter
        lines = [
            f"encounter {e.encounter_id}   {e.primary_cpt}   {e.service_date.isoformat()}"
            f"   {e.specialty}",
            f"rule pack {self.rule_pack_id} v{self.rule_pack_version} "
            f"[{self.rule_pack_digest[:12]}]   filter {'+'.join(self.provenance_filter)}",
            "",
            "Derivation",
            "----------",
        ]
        lines.extend(f"  {step}".rstrip() for step in self.trace)
        lines += ["", f"Layer A complexity score: {self.score}"]
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class PayerFriction:
    """One encounter's 835 friction, decision 11.

    Whether the payer's own processing, not the structural fee-schedule
    question the ratio answers, cost this encounter money. Never merged into
    the adequacy ratio: a denial and a genuinely inadequate rate are different
    problems with different remedies, and folding one into the other would
    make the published ratio unreadable as either.
    """

    denied: bool
    """The primary line was allowed ``0`` with a CO adjustment attached."""
    downcoded: bool
    """The 835's paid CPT on the account differs from the 837's submitted
    line, or a CO reason in ``{"59", "97", "234", "236"}`` fired with
    ``allowed > 0``."""
    carc: tuple[str, ...]
    """Every CO-group adjustment reason code on this encounter's remittance."""
    rarc: tuple[str, ...]
    """Every ``LQ*HE`` remark code, if the 835 carried any."""
    vehicle_existed: bool
    """Any Method 1 flag on this encounter names a candidate code."""


@dataclass(frozen=True, slots=True)
class Adequacy:
    """One encounter's payment adequacy, with everything needed to re-check it."""

    encounter_id: str
    cpt: str
    payer_id: str
    payer_name: str
    score: ComplexityScore
    realized: Decimal
    """The 835 allowed amount on the primary procedure's line."""
    schedule_expected: Decimal
    """What the fee schedule's complexity relation pays at this score, in
    Medicare PFS dollars, before the payer's multiple. Method 2's figure; kept
    for the spine and no longer the adequacy ratio's denominator."""
    multiplier: Decimal
    """The payer's contract level as a multiple of the schedule, from its own
    comparator encounters. Stays with the partner; never in an index record."""
    expected: Decimal
    """The Method 3 band-expected payment (``m3.expected``): the complexity-
    matched expected payment in this payer's dollars, and the ratio's
    denominator (decision 2)."""
    ratio: Ratio
    curve_id: str
    trace: tuple[str, ...]
    m3: Method3
    """The band-expected derivation behind ``expected``."""
    spine: DollarSpine
    decomposition: Decomposition
    signature: Signature
    method1_flags: tuple[Method1Flag, ...]
    payer_friction: PayerFriction
    ratio_interval: Interval
    """Realized over the Method 3 bootstrap CI, low-expected paired with
    high-ratio (decision 2's band arithmetic, method ``m3-band-bootstrap``)."""
    witness: Witness
    rulebook_version: str
    weights_version: str
    encounter_class: str

    def render(self) -> str:
        lines = [
            f"encounter {self.encounter_id}   {self.cpt}   {self.payer_name}",
            "",
            "Derivation",
            "----------",
        ]
        lines.extend(f"  {step}".rstrip() for step in self.trace)
        lines += [
            "",
            f"Payment adequacy ratio: {self.ratio}  95% CI {self.ratio_interval.render(4)}",
            f"Method 3 band expected: {self.m3.expected:.2f}  "
            f"(band [{self.m3.band.low}, {self.m3.band.high}], n={self.m3.band.n})",
        ]
        return "\n".join(lines)


def ratio_of(realized: Decimal, expected: Decimal) -> Ratio:
    """Divide realized payment by expected payment, both Money, under the pinned context."""
    if expected <= 0:
        msg = f"expected payment must be positive, got {expected}"
        raise NoReferenceCurveError(msg)
    with money_context():
        return Ratio((realized / expected).quantize(Decimal("0.0001")))


@dataclass(frozen=True, slots=True)
class Stratum:
    """One Method 0 fit: a single code within a single blinded payer."""

    payer_label: str
    n: int
    slope: Decimal
    slope_interval: Interval
    r_squared: Decimal
    differentiates: bool
    """False when the interval contains zero: the code has not been shown to
    pay differently for harder work."""
    suppressed: bool

    def render(self) -> str:
        if self.suppressed:
            return f"{self.payer_label:9} n={self.n:<4} withheld (n < {SUPPRESSION_THRESHOLD})"
        verdict = "differentiates" if self.differentiates else "flat"
        return (
            f"{self.payer_label:9} n={self.n:<4} {self.slope:>9.4f} $/point  "
            f"95% CI {self.slope_interval.render(4):<22} {verdict}"
        )


@dataclass(frozen=True, slots=True)
class IndexRecord:
    """One published value: a code, at an institution, over a period.

    The unit the registry accumulates. "Nothing at the patient record-level
    accumulates. What accumulates is the computed output, a payment adequacy
    index per CPT code, per institution, per period. Each value carries what is
    needed to read it: the code, the institution, the time window, the number of
    encounters behind it, the complexity distribution it was drawn from, and a
    confidence interval."

    Two of the fields below are the ones the methodology calls defensible from
    the first validated extract: ``distribution`` and ``slopes``. ``adequacy``
    depends on the cross-specialty curve, which the same document describes as
    the part that will take longer, and is reported separately for that reason.
    """

    code: str
    institution: str
    period_start: date
    period_end: date
    n: int

    distribution: Distribution
    """The empirical reference distribution of Layer A scores for this code."""
    slopes: tuple[Stratum, ...]
    """Method 0, one stratum per blinded payer. Needs no reference standard."""

    adequacy: Ratio | None
    adequacy_interval: Interval | None
    scored: int
    """Encounters carrying an adequacy value."""
    excluded: int
    """Encounters without a ratio: outside the schedule curve's fitted range,
    or paid by a payer with too few comparator encounters for a multiplier."""

    linkage_rate: Decimal
    rule_pack_id: str
    rule_pack_digest: str
    rule_pack_status: str
    rule_pack_source: str
    """``"packaged"`` or ``"external"``. A record is only ever publishable when
    both this is ``"packaged"`` and ``rule_pack_status`` is ``"ratified"``:
    published packs are the only source a ratified pack may come from."""
    provenance_filter: tuple[str, ...]
    package_version: str
    locality: str
    """The Medicare locality the expected payment was priced in."""
    setting: str
    """``facility`` or ``non-facility``."""
    reference_release: str
    """The CMS release the schedule curve was fitted at, e.g. ``RVU26D``."""
    rulebook_version: str
    """``"{rule_pack_id}@{version}"`` (decision 5)."""
    weights_version: str
    encounter_class: str
    card: CodeCard
    """The full population-level card for this code: histogram, deciles,
    shortfall, signature mix, instrument health and the rest. ``IndexRecord``
    stays the published unit; ``card`` is what a reader drills into."""
    suppressed: bool
    suppression_reason: str | None = None

    @property
    def publishable(self) -> bool:
        """Whether this record may leave the partner's environment.

        A ``ratified`` status alone is not enough: an external pack is capped
        at ``provisional`` on load (see ``rulepack.loads``), but that cap is
        the loader refusing to trust a file's own claim, not a promise that
        every other path is closed. Requiring ``rule_pack_source ==
        "packaged"`` here as well is the second, independent lock — a
        published pack is the only source a publishable record may ever
        trace to.
        """
        return (
            not self.suppressed
            and self.rule_pack_status == "ratified"
            and self.rule_pack_source == "packaged"
        )

    def render(self) -> str:
        window = f"{self.period_start.isoformat()} to {self.period_end.isoformat()}"
        lines = [
            f"CPT {self.code}   {self.institution}   {window}",
            f"  encounters        {self.n}",
            f"  complexity        {self.distribution.render()}",
            f"  linkage           {self.linkage_rate * 100:.2f}%",
            f"  rule pack         {self.rule_pack_id} [{self.rule_pack_digest[:12]}] "
            f"{self.rule_pack_status}   source: {self.rule_pack_source}",
            f"  rulebook          {self.rulebook_version}   weights {self.weights_version}   "
            f"class {self.encounter_class}",
            f"  filter            {'+'.join(self.provenance_filter)}",
            f"  priced in         {self.locality}, {self.setting}, "
            f"schedule curve at {self.reference_release}",
            "",
            "  Method 0 - payment against complexity, per payer",
        ]
        lines.extend(f"    {s.render()}" for s in self.slopes)
        lines.append("")
        if self.suppressed:
            lines.append(f"  Adequacy          withheld: {self.suppression_reason}")
        elif self.adequacy is None:
            lines.append("  Adequacy          not computed: no reference curve")
        else:
            interval = self.adequacy_interval.render(4) if self.adequacy_interval else "-"
            lines.append(
                f"  Adequacy          {self.adequacy}  95% CI {interval}  "
                f"({self.scored} scored, {self.excluded} outside curve range)"
            )
        lines.append(f"  {self.card.headline}")
        return "\n".join(lines)
