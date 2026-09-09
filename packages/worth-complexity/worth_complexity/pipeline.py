"""One run, end to end: files in, index records out.

The engine reads a directory and writes a result. It does no network calls,
takes no credentials, reads no clock and imports no cloud SDK. That is not
minimalism for its own sake, it is what makes "run our code inside your
environment" a deployment target rather than an eighteen-month rewrite, and it
is what lets a partner's security review conclude that nothing can leave.

Three things a run needs that the extract does not carry: the institution's
Medicare locality, the place-of-service setting, and the fee schedule itself.
The first is a parameter, refused rather than guessed. The setting can be
given per class. The third defaults to the fixture ``worth-fees`` ships and can
be supplied by a caller holding the full release.

Four more modules are wired in: ``claims`` (the 837, so Method 1 can check
against what was actually submitted rather than only the OR log panel),
``method1`` (documented-vs-submitted), ``method3`` (the band-expected payment
that is the adequacy ratio's denominator, decision 2 of CONTRACT.md) and
``signature`` (the dollar spine read off all three methods). ``population``
then folds every scored, priced, signed encounter into the per-code card the
registry publishes, and ``witness`` seals both the per-encounter and the
run-level output.

Multi-class runs (CONTRACT-PACKS-MC.md, "MC" track). A real partner delivery
is one extract with OR cases, clinic visits and monitoring episodes side by
side, sharing one 835/837 feed. ``extracts.read_extract`` now returns a
:class:`~worth_complexity.extracts.CombinedExtract` for a directory naming
more than one anchor table, and :func:`run` links remittance and claims once
over *every* encounter regardless of class, then runs one pass — pack
resolution, pricing, the comparator curve, payer multipliers, Method 0,
Method 1, Method 3, signatures, cards, compare rows, the queue — per class
present, in :data:`CLASS_ORDER`. What one pass produces is a
:class:`ClassRun`; :class:`Run` is the shared linkage/claims/witness context
plus one ``ClassRun`` per class, with convenience properties that flatten
across classes for a caller (or Meridian's serializer) that only ever saw one
class before.

A note on stage order: :data:`STAGES` lists the stage names in dependency
order rather than literally appended at the end. ``Adequacy`` now embeds the
Method 1 flags, the Method 3 band and the signature read off both, so those
three have to exist before an ``Adequacy`` can be built, and ``IndexRecord``
now carries its code's population ``CodeCard``, so the population stage has
to run before records are built. Reporting them in an order that could not
actually execute would make the progress callback a worse map of the run than
no map at all. Multi-class stages (``markers`` through ``records``) each run
once per class, in :data:`CLASS_ORDER`; the callback still names one stage at
a time, just more than once.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from worth_fees import PlaceOfService, WorthFeesError, expected_allowed
from worth_fees.sources import vintage_for

from worth_complexity import (
    claims,
    extracts,
    method1,
    method3,
    population,
    scoring,
    signature,
    x12,
)
from worth_complexity import witness as witness_mod
from worth_complexity.adequacy import (
    Multiplier,
    Observation,
    PricedEncounter,
    SchedulePool,
    adequacy,
    code_strata,
    fixture_schedules,
    index_records,
    method_zero_slopes,
    payer_multipliers,
    price_comparators,
    schedule_curve,
    vintage_in_force,
)
from worth_complexity.linkage import Linkage, link
from worth_complexity.models import (
    LAYER_A,
    SUPPRESSION_THRESHOLD,
    Adequacy,
    Cohort,
    IndexRecord,
    MissingMarkerError,
    NoReferenceCurveError,
    PayerFriction,
    RulePackError,
    ScoredEncounter,
    WorthComplexityError,
    blind_payers,
    ratio_of,
)
from worth_complexity.money import money_context
from worth_complexity.rulepack import RulePack, load, load_path
from worth_complexity.sampling import describe, quantile
from worth_complexity.version import __version__

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from typing import Literal

    from worth_fees.sources import Vintage

    from worth_complexity.claims import Claim
    from worth_complexity.curve import Fit
    from worth_complexity.extracts import ClinicalExtract
    from worth_complexity.linkage import LinkedEncounter
    from worth_complexity.method1 import Method1Flag
    from worth_complexity.method3 import ComparatorInput
    from worth_complexity.models import Encounter, RemitLine
    from worth_complexity.population import CodeCard, CompareRow, QueueItem
    from worth_complexity.witness import Witness

CLASS_ORDER: tuple[str, ...] = ("surgical", "visit", "episode")
"""The fixed order every multi-class run processes classes in (decision 2,
CONTRACT-PACKS-MC.md): "for each class present ... in the fixed order
surgical, visit, episode". Deterministic regardless of the order
``extracts.CombinedExtract.classes`` happens to iterate in, or the order a
caller's ``pack_name``/``pack_path`` sequence names them."""

CLASS_DEFAULT_PACKS: dict[str, str] = {
    "surgical": "surgical-v1",
    "visit": "visit-em-v1",
    "episode": "episode-rpm-v1",
}
"""The packaged pack :func:`run` loads for each encounter class when
``pack_name`` is not given (decision 3, CONTRACT-PACKS.md)."""

CLASS_DEFAULT_SETTING: dict[str, PlaceOfService] = {
    "surgical": PlaceOfService.FACILITY,
    "visit": PlaceOfService.NON_FACILITY,
    "episode": PlaceOfService.NON_FACILITY,
}
"""The place-of-service :func:`run` prices at for each encounter class when
``setting`` does not name that class. An OR case is a facility case; an
office E/M visit and a remote-monitoring episode are both priced in the
non-facility setting, the lower practice-expense RVU that applies when the
physician's own practice carries the overhead rather than a hospital."""

STAGES: tuple[str, ...] = (
    "extract",
    "encounters",
    "remittance",
    "link",
    "claims",
    "markers",
    "score",
    "price",
    "curve",
    "multipliers",
    "slopes",
    "method1",
    "method3",
    "signature",
    "adequacy",
    "trends",
    "population",
    "records",
    "witness",
)
"""The stage names :func:`run` reports to its ``progress`` callback, in the
order they actually execute — see the module docstring.

``extract`` through ``claims`` happen once, over every class together
(decision 1, CONTRACT-PACKS-MC.md: "links remittance and claims once over
every encounter"). ``markers`` through ``records`` then run once per class
present, in :data:`CLASS_ORDER`; ``witness`` seals the whole run's flattened
output once at the end.

The engine reads no clock. A caller that wants to know how long each stage
took passes a callback and times the gaps itself, so the timing lives with
whoever asked for it and the arithmetic stays free of anything a referee
cannot re-run.
"""

CENTS = Decimal("0.01")
_DOWNCODE_REASONS = frozenset({"59", "97", "234", "236"})
_OPERATIVE = frozenset({"operative"})


@dataclass(frozen=True, slots=True)
class ClassRun:
    """Everything one class's own pass over the shared linkage produced.

    Decision 2, CONTRACT-PACKS-MC.md: what a single-class run used to carry
    on ``Run`` directly now lives here, one instance per class present in the
    extract. A class present in the extract but with zero linked encounters
    still gets a ``ClassRun`` here, with empty tuples throughout — it is
    never dropped from :attr:`Run.classes`.
    """

    encounter_class: str
    pack: RulePack
    rulebook_version: str
    weights_version: str
    setting: PlaceOfService
    provenance_filter: tuple[str, ...]
    observations: tuple[Observation, ...]
    priced: dict[str, PricedEncounter]
    """Every comparator encounter's fee-schedule amounts, by encounter id."""
    schedule_curve: Fit | None
    """The fee schedule's complexity relation, fitted on this class's own
    comparator cohort. ``None`` only when the class has no comparator
    observation to fit on at all — a class with zero linked encounters,
    decision 2's "never dropped" case — and unused by anything else in that
    same case, since the study loop that would read it has nothing to
    iterate over either."""
    multipliers: dict[str, Multiplier]
    """Each payer's multiple of the schedule, by payer id. Partner-only."""
    thin_payers: tuple[str, ...]
    """Payers with too few comparator encounters for a multiplier, with the reason."""
    slopes: dict[tuple[str, str], Fit]
    thin_strata: tuple[str, ...]
    """(code, payer) strata with too few encounters to fit a Method 0 slope."""
    adequacies: tuple[Adequacy, ...]
    records: tuple[IndexRecord, ...]
    """One per study code: the unit the registry accumulates."""
    cards: tuple[CodeCard, ...]
    """One population card per study code, sorted."""
    compare: tuple[CompareRow, ...]
    """One row per code, for the cross-code compare view (decision 9)."""
    queue: tuple[QueueItem, ...]
    """The work queue: upper-tail and flagged encounters, ranked."""
    extrapolated: tuple[str, ...]
    """Kept for compatibility; always empty. Method 3 no longer extrapolates
    the way the old Method 2 denominator did (band widening replaces it), and
    the schedule curve (Method 2) is now always read with extrapolation
    allowed, since the spine needs it at the code's own median score too."""
    unmultiplied: tuple[str, ...]
    """Study encounters excluded because their payer has no multiplier."""
    unbanded: tuple[str, ...]
    """Study encounters excluded because no Method 3 band ever reached the
    suppression floor, even widened to the full 0-100 scale."""
    trends: tuple[population.PeriodSeries, ...] = ()
    domain_trends: tuple[population.PeriodSeries, ...] = ()
    """The same over-time output keyed by service line, so a domain whose
    billing vehicle changes across a policy date reads as one series."""
    """One :class:`~worth_complexity.population.PeriodSeries` per study code
    (decision 7, CONTRACT-SEEDS.md): the ratio over calendar time, split
    pre/post when ``run()`` was given a ``policy_date``, and again by payer.
    Empty when the class has no study encounter carrying an ``Adequacy`` at
    all, the same "never dropped" rule the rest of this dataclass follows."""

    @property
    def study(self) -> tuple[Observation, ...]:
        return tuple(o for o in self.observations if o.cohort is Cohort.STUDY)

    @property
    def comparator(self) -> tuple[Observation, ...]:
        return tuple(o for o in self.observations if o.cohort is Cohort.COMPARATOR)


@dataclass(frozen=True, slots=True)
class Run:
    """Everything one pass over a partner extract produced.

    Shared across every class present (decision 2, CONTRACT-PACKS-MC.md):
    remittance and claims are linked once over every encounter regardless of
    class, so the linkage, the claims, the payer blinding, the input hash and
    the run-level witness are one value for the whole run, not one per
    class. :attr:`classes` carries everything that *is* per class.
    """

    linkage: Linkage
    claims: dict[str, Claim]
    """The 837 claim linked to each encounter that has one, by encounter id,
    across every class."""
    payer_labels: dict[str, str]
    """Blinded label per payer id, across every class's observations. The
    identities stay where the data stays."""
    input_hash: str
    """sha256 over the sorted (filename, sha256) pairs of every clinical,
    remittance and claims file this run read (decision 6, CONTRACT.md)."""
    witness: Witness
    """The run-level witness, sealing every published record across every
    class."""
    locality: str
    locality_name: str
    reference: Vintage
    """The CMS release the schedule curve was fitted at."""
    classes: tuple[ClassRun, ...]
    """One :class:`ClassRun` per encounter class present in the extract, in
    :data:`CLASS_ORDER`."""
    unscored: tuple[tuple[str, str], ...]
    """``(encounter_id, reason)`` for every encounter whose class had no
    rule pack resolved for it (decision 2/3, CONTRACT-PACKS-MC.md) — named,
    never silently dropped."""
    unused_packs: tuple[tuple[str, str], ...]
    """``(pack identifier, reason)`` for a pack named by ``pack_name``/
    ``pack_path`` whose ``encounter_class`` is not present in this extract
    at all (decision 3)."""

    # -- flattened views, across every class -------------------------------

    @property
    def observations(self) -> tuple[Observation, ...]:
        return tuple(o for c in self.classes for o in c.observations)

    @property
    def study(self) -> tuple[Observation, ...]:
        return tuple(o for c in self.classes for o in c.study)

    @property
    def comparator(self) -> tuple[Observation, ...]:
        return tuple(o for c in self.classes for o in c.comparator)

    @property
    def adequacies(self) -> tuple[Adequacy, ...]:
        return tuple(a for c in self.classes for a in c.adequacies)

    @property
    def records(self) -> tuple[IndexRecord, ...]:
        return tuple(r for c in self.classes for r in c.records)

    @property
    def cards(self) -> tuple[CodeCard, ...]:
        return tuple(card for c in self.classes for card in c.cards)

    @property
    def compare(self) -> tuple[CompareRow, ...]:
        return tuple(row for c in self.classes for row in c.compare)

    @property
    def queue(self) -> tuple[QueueItem, ...]:
        return tuple(item for c in self.classes for item in c.queue)

    @property
    def trends(self) -> tuple[population.PeriodSeries, ...]:
        """Every class's over-time series, flattened (decision 7)."""
        return tuple(series for c in self.classes for series in c.trends)

    @property
    def domain_trends(self) -> tuple[population.PeriodSeries, ...]:
        """Every class's domain-keyed series, in class order."""
        return tuple(series for c in self.classes for series in c.domain_trends)

    # -- single-class convenience: raises on a genuinely mixed run ---------

    def _only_class(self, attr: str) -> ClassRun:
        if len(self.classes) != 1:
            present = [c.encounter_class for c in self.classes]
            msg = (
                f"run.{attr} has no single answer on a run with {len(self.classes)} classes "
                f"({present}); read run.classes instead"
            )
            raise WorthComplexityError(msg)
        return self.classes[0]

    @property
    def pack(self) -> RulePack:
        return self._only_class("pack").pack

    @property
    def rulebook_version(self) -> str:
        return self._only_class("rulebook_version").rulebook_version

    @property
    def weights_version(self) -> str:
        return self._only_class("weights_version").weights_version

    @property
    def setting(self) -> PlaceOfService:
        return self._only_class("setting").setting

    @property
    def schedule_curve(self) -> Fit | None:
        return self._only_class("schedule_curve").schedule_curve

    @property
    def priced(self) -> dict[str, PricedEncounter]:
        return self._only_class("priced").priced

    @property
    def multipliers(self) -> dict[str, Multiplier]:
        return self._only_class("multipliers").multipliers

    @property
    def thin_payers(self) -> tuple[str, ...]:
        return self._only_class("thin_payers").thin_payers

    @property
    def slopes(self) -> dict[tuple[str, str], Fit]:
        return self._only_class("slopes").slopes

    @property
    def thin_strata(self) -> tuple[str, ...]:
        return self._only_class("thin_strata").thin_strata

    @property
    def extrapolated(self) -> tuple[str, ...]:
        return self._only_class("extrapolated").extrapolated

    @property
    def unmultiplied(self) -> tuple[str, ...]:
        return self._only_class("unmultiplied").unmultiplied

    @property
    def unbanded(self) -> tuple[str, ...]:
        return self._only_class("unbanded").unbanded

    @property
    def provenance_filter(self) -> tuple[str, ...]:
        """The provenance filter every class was scored with. Unlike
        ``pack``/``rulebook_version``/``weights_version``/``setting``, this
        is one argument to :func:`run` applied identically to every class,
        so it never raises on a mixed run — there is nothing ambiguous to
        report."""
        if not self.classes:
            return ()
        return self.classes[0].provenance_filter


def _price_method1_flag(
    flag: Method1Flag,
    multiplier: Multiplier | None,
    *,
    locality: str,
    setting: PlaceOfService,
    reference: Vintage,
    pool: SchedulePool,
) -> Method1Flag:
    """Attach a fee-schedule amount to one missed/mismatched flag.

    The first candidate code that prices wins (decision 1); a ``no_code``
    flag is returned unchanged, and a flag whose payer has no multiplier, or
    whose every candidate fails to price, comes back with ``priced=None`` and
    the reason named in ``pricing_trace``.

    A flag that names ``replaces`` (decision 4, CONTRACT-PACKS.md — a
    work-rule flag whose candidate supersedes an already-billed code, e.g.
    the next E/M level by time) prices the *difference* between the
    candidate and the replaced code instead of the candidate's amount
    outright: the replaced code is already paid for, so only the delta is
    unrecovered revenue. Floored at 0 — a "candidate" that actually prices
    lower than what was billed is not a missed charge. The trace names both
    amounts so a biller can see the whole subtraction, not just its result.
    """
    if not flag.candidate_codes:
        return flag
    if multiplier is None:
        return method1.price_flag(
            flag, amount=None, trace=("no payer multiplier: cannot be priced",)
        )
    schedule = pool(reference.rule_year, reference.quarter)
    reasons: list[str] = []
    for candidate in flag.candidate_codes:
        try:
            derivation = expected_allowed(
                candidate,
                (),
                locality,
                setting,
                reference.rule_year,
                reference.quarter,
                schedule=schedule,
            )
        except WorthFeesError as exc:
            reasons.append(f"{candidate}: cannot be priced ({exc})")
            continue

        if flag.replaces is not None:
            try:
                replaced = expected_allowed(
                    flag.replaces,
                    (),
                    locality,
                    setting,
                    reference.rule_year,
                    reference.quarter,
                    schedule=schedule,
                )
            except WorthFeesError as exc:
                reasons.append(f"replaces {flag.replaces}: cannot be priced ({exc})")
                continue
            with money_context():
                difference = derivation.amount - replaced.amount
                if difference < 0:
                    difference = Decimal(0)
                amount = (difference * multiplier.value).quantize(CENTS)
            trace = (
                f"candidate {candidate} priced at PFS {derivation.amount:.2f}, replaces "
                f"{flag.replaces} priced at PFS {replaced.amount:.2f} (CMS {reference.label}); "
                f"difference {difference:.2f} x multiplier {multiplier.value} = {amount:.2f}",
            )
            return method1.price_flag(flag, amount=amount, trace=trace)

        with money_context():
            amount = (derivation.amount * multiplier.value).quantize(CENTS)
        trace = (
            f"candidate {candidate} priced at PFS {derivation.amount:.2f} (CMS "
            f"{reference.label}) x multiplier {multiplier.value} = {amount:.2f}",
        )
        return method1.price_flag(flag, amount=amount, trace=trace)
    return method1.price_flag(flag, amount=None, trace=tuple(reasons))


def _payer_friction(
    linked: LinkedEncounter, claim: Claim | None, flags: tuple[Method1Flag, ...]
) -> PayerFriction:
    """One encounter's 835 friction (decision 11, CONTRACT.md).

    ``downcoded`` fires either when a CO-adjustment reason in the
    bundling/downcode family paid the primary line at a positive amount, or
    when the claim named a primary code the 835 never paid a line under at
    all on the same service date — the shape a payer's own re-coding leaves
    in the remittance when it does not deny the line outright.
    """
    primary_lines = linked.primary_lines
    denied = any(line.allowed == 0 and line.denial_codes for line in primary_lines)
    reason_hit = any(
        line.allowed > 0
        and any(
            reason in _DOWNCODE_REASONS for group, reason, _ in line.adjustments if group == "CO"
        )
        for line in primary_lines
    )
    remapped = (
        claim is not None
        and not primary_lines
        and any(line.service_date == linked.encounter.service_date for line in linked.lines)
    )
    carc = tuple(
        sorted(
            {
                reason
                for line in linked.lines
                for group, reason, _ in line.adjustments
                if group == "CO"
            }
        )
    )
    rarc = tuple(sorted({code for line in linked.lines for code in line.rarc}))
    vehicle_existed = any(f.vehicle_exists for f in flags)
    return PayerFriction(
        denied=denied,
        downcoded=reason_hit or remapped,
        carc=carc,
        rarc=rarc,
        vehicle_existed=vehicle_existed,
    )


def _schedule_scale(realized: Decimal, multiplier: Decimal) -> Decimal:
    with money_context():
        return (realized / multiplier).quantize(CENTS)


def _strength_rank(s: method1.Strength) -> int:
    return method1.STRENGTH_RANK[s]


def _strongest(strengths: list[method1.Strength]) -> method1.Strength | None:
    """The highest-ranked evidence strength among a set of flags, or ``None``."""
    if not strengths:
        return None
    return max(strengths, key=_strength_rank)


def _collect_input_hash(
    extract: extracts.ClinicalExtract,
    remit_lines: tuple[RemitLine, ...],
    claim_records: tuple[Claim, ...],
) -> str:
    """Every clinical, remittance and claims file this run actually read.

    Table- and note-level hashes come straight off ``extract.file_hashes()``
    (the union across every class for a
    :class:`~worth_complexity.extracts.CombinedExtract`, decision 1); the 835
    and 837 readers do not expose a file manifest of their own, but every
    line they produce already carries a ``source_ref`` naming the file and
    its hash, so the distinct set of those is the same manifest without
    needing a new accessor on either reader.
    """
    files: set[tuple[str, str]] = set(extract.file_hashes())
    for line in remit_lines:
        files.add((line.source_ref.file, line.source_ref.sha256))
    for claim in claim_records:
        files.add((claim.source_ref.file, claim.source_ref.sha256))
        for cl in claim.lines:
            files.add((cl.source_ref.file, cl.source_ref.sha256))
    return witness_mod.input_hash(files)


def _class_extracts(extract: ClinicalExtract) -> dict[str, ClinicalExtract]:
    """One sub-extract per class present, however many anchors ``extract``
    was read from. A single-class extract (the common case, unchanged) maps
    its own one class to itself; a
    :class:`~worth_complexity.extracts.CombinedExtract` exposes its
    ``by_class`` mapping directly."""
    combined = getattr(extract, "by_class", None)
    if combined is not None:
        return dict(combined)
    return {extract.encounter_class: extract}


def _resolve_settings(
    setting: PlaceOfService | str | Mapping[str, PlaceOfService | str] | None,
    classes_present: Sequence[str],
) -> dict[str, PlaceOfService]:
    """Turn ``run()``'s ``setting`` argument into one :class:`PlaceOfService`
    per class present.

    ``None`` (default): :data:`CLASS_DEFAULT_SETTING` for every class. A
    plain value (``"facility"`` or a :class:`PlaceOfService`): that setting
    for every class, overriding the default everywhere — the CLI's bare
    ``--setting VALUE`` form. A mapping: a per-class override, keyed by class
    name (the CLI's ``--setting CLASS=VALUE`` form); a ``"*"`` key sets the
    fallback for any class the mapping does not name, in place of
    :data:`CLASS_DEFAULT_SETTING` for just those classes; classes named
    neither by the mapping nor by ``"*"`` still get their own packaged
    default.
    """
    if setting is None:
        return {c: CLASS_DEFAULT_SETTING.get(c, PlaceOfService.FACILITY) for c in classes_present}
    if isinstance(setting, Mapping):
        fallback = setting.get("*")
        out: dict[str, PlaceOfService] = {}
        for c in classes_present:
            if c in setting:
                out[c] = PlaceOfService(setting[c])
            elif fallback is not None:
                out[c] = PlaceOfService(fallback)
            else:
                out[c] = CLASS_DEFAULT_SETTING.get(c, PlaceOfService.FACILITY)
        return out
    value = PlaceOfService(setting)
    return dict.fromkeys(classes_present, value)


def _load_named(
    name: str, search: Sequence[Path] | None, *, by_class: dict[str, RulePack], kind: str
) -> None:
    pack = load(name, search=search)
    prior = by_class.get(pack.encounter_class)
    if prior is not None and prior.rule_pack_id != pack.rule_pack_id:
        msg = (
            f"two {kind} rule packs both score encounter class {pack.encounter_class!r}: "
            f"{prior.rule_pack_id!r} and {pack.rule_pack_id!r}"
        )
        raise RulePackError(msg)
    by_class[pack.encounter_class] = pack


def _resolve_packs(
    *,
    pack_name: str | list[str] | tuple[str, ...] | None,
    pack_path: Path | list[Path] | tuple[Path, ...] | None,
    pack_search: Sequence[Path] | None,
    classes_present: tuple[str, ...],
) -> tuple[dict[str, RulePack], tuple[tuple[str, str], ...]]:
    """Resolve ``run()``'s ``pack_name``/``pack_path`` into one
    :class:`~worth_complexity.rulepack.RulePack` per class, plus the packs
    that named a class this extract does not have.

    Neither given: every class present gets its packaged default
    (:data:`CLASS_DEFAULT_PACKS`) — unchanged from before multi-class runs
    existed. Either given, scalar or sequence (decision 3,
    CONTRACT-PACKS-MC.md): every ``pack_name`` entry and every ``pack_path``
    entry resolves to one pack, and two of them — whether both named, both
    paths, or one of each — that score the same class are refused
    (``RulePackError``): "two packs for the same class is an error", stated
    without distinguishing how either was given. A resolved pack whose class
    this extract does not have at all is reported back for the caller to
    record in ``Run.unused_packs`` rather than raising — *except* when the
    extract has exactly one class: there, nothing else the pack could be
    for, so a mismatch is refused immediately, unchanged from the
    single-class behaviour every existing caller already depends on.
    """
    names: list[str] = (
        list(pack_name)
        if isinstance(pack_name, list | tuple)
        else []
        if pack_name is None
        else [pack_name]
    )
    paths: list[Path] = (
        list(pack_path)
        if isinstance(pack_path, list | tuple)
        else []
        if pack_path is None
        else [pack_path]
    )

    if not names and not paths:
        defaults = {
            cls: load(CLASS_DEFAULT_PACKS[cls], search=pack_search)
            for cls in classes_present
            if cls in CLASS_DEFAULT_PACKS
        }
        missing = [cls for cls in classes_present if cls not in CLASS_DEFAULT_PACKS]
        if missing:
            msg = (
                f"no packaged default rule pack for encounter class {missing[0]!r}; "
                "pass pack_name explicitly"
            )
            raise RulePackError(msg)
        return defaults, ()

    given: dict[str, RulePack] = {}
    for n in names:
        _load_named(n, pack_search, by_class=given, kind="pack_name")
    for p in paths:
        pack = load_path(p)
        prior = given.get(pack.encounter_class)
        if prior is not None and prior.rule_pack_id != pack.rule_pack_id:
            msg = (
                f"two rule packs both score encounter class {pack.encounter_class!r}: "
                f"{prior.rule_pack_id!r} and {pack.rule_pack_id!r}"
            )
            raise RulePackError(msg)
        given[pack.encounter_class] = pack

    resolved = given
    if len(classes_present) == 1:
        (only,) = classes_present
        if only not in resolved:
            given_classes = ", ".join(sorted({*resolved} or {"(none)"}))
            msg = (
                f"none of the given rule pack(s) ({given_classes}) score encounter class "
                f"{only!r}, the only class in this extract"
            )
            raise RulePackError(msg)
        # The extract's one class matched something given; any *other*
        # given pack, naming a class this extract does not have, still gets
        # reported in ``Run.unused_packs`` below rather than silently
        # dropped -- only a total mismatch (above) is the hard error.

    unused = tuple(
        (
            pack.rule_pack_id,
            f"scores encounter class {pack.encounter_class!r}, not present in this extract "
            f"({', '.join(classes_present)})",
        )
        for pack in resolved.values()
        if pack.encounter_class not in classes_present
    )
    used = {cls: pack for cls, pack in resolved.items() if cls in classes_present}
    return used, unused


def run(
    extract_dir: Path,
    remittance_dir: Path,
    *,
    locality: str,
    setting: PlaceOfService | str | Mapping[str, PlaceOfService | str] | None = None,
    schedules: SchedulePool | None = None,
    reference: tuple[int, int] | None = None,
    pack_name: str | list[str] | tuple[str, ...] | None = None,
    pack_path: Path | list[Path] | tuple[Path, ...] | None = None,
    pack_search: Sequence[Path] | None = None,
    provenance_filter: frozenset[str] = LAYER_A,
    claims_dir: Path | None = None,
    witness_key: bytes | None = None,
    progress: Callable[[str], None] | None = None,
    policy_date: date | None = None,
) -> Run:
    """Score, link, price, fit, band and sign.

    Args:
        extract_dir: The clinical extract. A single-class directory (one
            anchor table) or a mixed one (more than one anchor, e.g.
            ``or_log.txt`` and ``visit.txt`` and ``episode.txt`` side by
            side) — see :func:`worth_complexity.extracts.read_extract`.
        remittance_dir: The 835 files, for every class, linked once
            (decision 1, CONTRACT-PACKS-MC.md).
        locality: The institution's Medicare locality, e.g. ``NY01``. Required:
            the extract does not carry it and guessing it would misprice every
            comparator encounter silently.
        setting: ``facility`` or ``non-facility``, for every class; a mapping
            from class name to setting for a per-class override (a ``"*"``
            key sets the fallback for classes it does not name); ``None``
            (the default): each class's own packaged default,
            :data:`CLASS_DEFAULT_SETTING`. See :func:`_resolve_settings`.
        schedules: Where fee schedules come from. Default: the worth-fees fixture.
        reference: The (rule year, quarter) the schedule curve is fitted at.
            Default: the release in force on the latest service date across
            every class.
        pack_name: The rule pack(s) to load by name (with or without
            ``.json``), resolved by :func:`worth_complexity.rulepack.load` —
            packaged unless ``pack_search`` or ``WORTH_RULEPACK_DIR`` names
            an external directory that shadows it. A single name (unchanged
            behaviour) or a sequence, one pack per class it declares
            (decision 3, CONTRACT-PACKS-MC.md). ``None`` (the default): the
            packaged pack for each class present, from
            :data:`CLASS_DEFAULT_PACKS`. Ignored for a class ``pack_path``
            also names. Whichever pack is loaded for a class, on an extract
            with exactly one class, a pack that does not score it is refused
            with :class:`~worth_complexity.models.RulePackError` naming both
            classes; on a multi-class extract, a pack naming a class the
            extract does not have is recorded in ``Run.unused_packs``
            instead.
        pack_path: Load this file, or these files, directly as external rule
            pack(s) (layer 2), skipping name resolution entirely; a path
            wins over a name for the same class. An external pack is capped
            at ``provisional`` on load, whatever it declares.
        pack_search: Extra directories to search for a ``pack_name`` entry,
            tried before ``WORTH_RULEPACK_DIR`` and the packaged rule packs.
        claims_dir: The 837 directory. Default: ``extract_dir.parent /
            "claims"`` if that exists, else Method 1 falls back to each
            class's own documented-vs-billed panel for "submitted"
            everywhere.
        witness_key: HMAC key for the witness signature, or ``None`` for the
            plain ``sha256`` scheme. Read from ``WORTH_WITNESS_KEY`` by the
            CLI, not here — the engine only ever takes bytes.
        progress: Called with each name in :data:`STAGES` as that stage
            begins — once per class for the per-class stages, see the module
            docstring.
        policy_date: When given, every class's ``ClassRun.trends`` splits
            its per-code cohort into a ``pre`` (before this date) and
            ``post`` (on or after it) summary, alongside the ordinary
            month-by-month series (decision 7, CONTRACT-SEEDS.md). ``None``
            (the default): the series is computed the same way, just with no
            ``pre``/``post`` split.
    """
    pool = schedules or fixture_schedules
    claims_directory = claims_dir if claims_dir is not None else extract_dir.parent / "claims"

    def stage(name: str) -> None:
        if progress is not None:
            progress(name)

    stage("extract")
    extract = extracts.read_extract(extract_dir)
    class_extracts = _class_extracts(extract)
    classes_present = tuple(c for c in CLASS_ORDER if c in class_extracts)

    stage("encounters")
    encounters_by_class: dict[str, tuple[Encounter, ...]] = {
        cls: class_extracts[cls].encounters() for cls in classes_present
    }
    all_encounters: tuple[Encounter, ...] = tuple(
        e for cls in classes_present for e in encounters_by_class[cls]
    )

    resolved_packs, unused_packs = _resolve_packs(
        pack_name=pack_name,
        pack_path=pack_path,
        pack_search=pack_search,
        classes_present=classes_present,
    )
    resolved_settings = _resolve_settings(setting, classes_present)

    unscored: list[tuple[str, str]] = []
    scored_classes = tuple(c for c in classes_present if c in resolved_packs)
    for cls in classes_present:
        if cls not in resolved_packs:
            unscored.extend(
                (e.encounter_id, f"no rule pack resolved for encounter class {cls!r}")
                for e in encounters_by_class[cls]
            )

    stage("remittance")
    remit_lines = x12.read_directory(remittance_dir)
    stage("link")
    linkage = link(all_encounters, remit_lines)
    # Payer blinding is one assignment for the whole run (decision 2,
    # CONTRACT-PACKS-MC.md: ``payer_labels`` is a shared ``Run`` field), from
    # every linked encounter's payer regardless of class -- computed here,
    # before any class-specific scoring, and threaded into every class's own
    # pass so the same real payer never gets two different blinded labels
    # depending on which class happened to see it first.
    labels = blind_payers(le.payer_id for le in linkage.linked)

    stage("claims")
    claim_records = claims.read_directory(claims_directory)
    linked_claims = claims.link_claims(all_encounters, claim_records)
    input_hash_value = _collect_input_hash(extract, remit_lines, claim_records)

    reference_vintage = (
        vintage_for(*reference)
        if reference is not None
        else vintage_in_force(max(e.service_date for e in all_encounters))[0]
    )

    class_runs: list[ClassRun] = []
    priced_locality = locality.strip().upper()
    locality_name = ""
    for cls in scored_classes:
        class_run, cls_locality, cls_locality_name, cls_unscored = _run_class(
            cls,
            class_extracts[cls],
            encounters_by_class[cls],
            linkage=linkage,
            linked_claims=linked_claims,
            pack=resolved_packs[cls],
            place=resolved_settings[cls],
            locality=locality,
            reference_vintage=reference_vintage,
            pool=pool,
            provenance_filter=provenance_filter,
            input_hash_value=input_hash_value,
            witness_key=witness_key,
            labels=labels,
            stage=stage,
            policy_date=policy_date,
        )
        class_runs.append(class_run)
        unscored.extend(cls_unscored)
        if class_run.priced:
            priced_locality, locality_name = cls_locality, cls_locality_name

    stage("witness")
    all_records = tuple(r for c in class_runs for r in c.records)
    run_witness = witness_mod.witness(
        input_hash=input_hash_value,
        rulebook_version="+".join(sorted({c.rulebook_version for c in class_runs})),
        weights_version="+".join(sorted({c.weights_version for c in class_runs})),
        package_versions={"worth-complexity": __version__},
        outputs=witness_mod.to_jsonable(all_records),
        key=witness_key,
    )

    return Run(
        linkage=linkage,
        claims=linked_claims,
        payer_labels=labels,
        input_hash=input_hash_value,
        witness=run_witness,
        locality=priced_locality,
        locality_name=locality_name,
        reference=reference_vintage,
        classes=tuple(class_runs),
        unscored=tuple(unscored),
        unused_packs=unused_packs,
    )


def _run_class(
    cls: str,
    class_extract: ClinicalExtract,
    encounters: tuple[Encounter, ...],
    *,
    linkage: Linkage,
    linked_claims: dict[str, Claim],
    pack: RulePack,
    place: PlaceOfService,
    locality: str,
    reference_vintage: Vintage,
    pool: SchedulePool,
    provenance_filter: frozenset[str],
    input_hash_value: str,
    witness_key: bytes | None,
    labels: dict[str, str],
    stage: Callable[[str], None],
    policy_date: date | None = None,
) -> tuple[ClassRun, str, str, tuple[tuple[str, str], ...]]:
    """One class's own pass: score through records, decision 2's per-class
    stage list. Returns the :class:`ClassRun`, the locality this class's own
    pricing normalised to (identical across classes; the caller keeps
    whichever class actually produced one), and every encounter this class
    could not score at all (decision 6, CONTRACT-SEEDS.md: an encounter with
    no structured marker left at all is unscorable, not merely thin --
    ``scoring.score`` raises :class:`~worth_complexity.models.MissingMarkerError`
    for it, caught here so one such encounter never fails the whole run)."""
    if pack.encounter_class != cls:
        msg = (
            f"rule pack {pack.rule_pack_id!r} scores encounter class "
            f"{pack.encounter_class!r}, but was resolved for encounter class {cls!r}"
        )
        raise RulePackError(msg)

    stage("markers")
    marker_sets = class_extract.markers(encounters, pack)

    stage("score")
    scored: dict[str, ScoredEncounter] = {}
    class_unscored: list[tuple[str, str]] = []
    for enc in encounters:
        try:
            scored[enc.encounter_id] = scoring.score(
                enc, marker_sets[enc.encounter_id], pack, provenance_filter
            )
        except MissingMarkerError as exc:
            class_unscored.append((enc.encounter_id, str(exc)))

    linked = tuple(le for le in linkage.linked if le.encounter.encounter_id in scored)
    observations = tuple(Observation(scored[le.encounter.encounter_id], le) for le in linked)
    # ``labels`` is the whole run's blinding (decision 2, CONTRACT-PACKS-MC.md),
    # passed in rather than recomputed from this class's own observations
    # alone, so the same real payer carries the same blinded label in every
    # class's cards, queue and compare rows.

    stage("price")
    priced = price_comparators(
        observations,
        locality=locality,
        setting=place,
        schedules=pool,
        reference=reference_vintage,
    )
    first = next(iter(priced.values()), None)
    priced_locality = first.at_reference.locality if first else locality.strip().upper()
    locality_name = first.at_reference.locality_name if first else ""

    stage("curve")
    curve: Fit | None
    try:
        curve = schedule_curve(observations, priced, reference_vintage)
    except NoReferenceCurveError:
        curve = None

    stage("multipliers")
    multipliers, thin_payers = payer_multipliers(observations, priced, labels)
    stage("slopes")
    slopes, thin_strata = method_zero_slopes(observations, labels)

    stage("method1")
    flags_by_encounter: dict[str, tuple[Method1Flag, ...]] = {}
    for obs in observations:
        if obs.cohort is not Cohort.STUDY:
            continue
        enc = obs.scored.encounter
        notes = class_extract.notes_for(enc.encounter_id, _OPERATIVE)
        stmts = tuple(s for note in notes for s in method1.statements(note))
        claim = linked_claims.get(enc.encounter_id)
        facts = class_extract.facts(enc.encounter_id)
        raw_flags = method1.cross_check(enc, stmts, claim, pack.procedures) + (
            method1.evaluate_work_rules(enc, facts, claim, pack.work_rules)
        )
        multiplier = multipliers.get(obs.payer_id)
        flags_by_encounter[enc.encounter_id] = tuple(
            _price_method1_flag(
                flag,
                multiplier,
                locality=priced_locality,
                setting=place,
                reference=reference_vintage,
                pool=pool,
            )
            for flag in raw_flags
        )

    stage("method3")
    m3_comparators: tuple[ComparatorInput, ...] = tuple(
        (
            o.scored.encounter.encounter_id,
            o.scored.encounter.primary_cpt,
            o.scored.encounter.specialty,
            o.score.value,
            priced[o.scored.encounter.encounter_id].at_reference.amount,
        )
        for o in observations
        if o.cohort is Cohort.COMPARATOR
    )

    stage("signature")
    study_scores_by_code: dict[str, list[Decimal]] = defaultdict(list)
    for o in observations:
        if o.cohort is Cohort.STUDY:
            study_scores_by_code[o.scored.encounter.primary_cpt].append(Decimal(o.score.value))
    code_medians = {
        code: quantile(sorted(values), "0.5") for code, values in study_scores_by_code.items()
    }

    stage("adequacy")
    results: list[Adequacy] = []
    unmultiplied: list[str] = []
    unbanded: list[str] = []
    for obs in observations:
        if obs.cohort is not Cohort.STUDY:
            continue
        enc = obs.scored.encounter
        multiplier = multipliers.get(obs.payer_id)
        if multiplier is None:
            unmultiplied.append(enc.encounter_id)
            continue
        seed = int(hashlib.sha256(enc.encounter_id.encode()).hexdigest()[:8], 16)
        try:
            m3 = method3.band_expected(
                enc.encounter_id, obs.score, m3_comparators, multiplier.value, seed=seed
            )
        except NoReferenceCurveError:
            unbanded.append(enc.encounter_id)
            continue

        flags = flags_by_encounter.get(enc.encounter_id, ())
        summary = method1.summarize(flags)
        m1_split = (
            summary.by_bucket["missed"].dollars,
            summary.by_bucket["mismatched"].dollars,
            summary.by_bucket["no_code"].dollars,
        )
        priced_flag_amounts = [
            f.priced for f in flags if f.priced is not None and f.bucket != "no_code"
        ]
        with money_context():
            m1_total = sum(priced_flag_amounts, start=Decimal(0)) if priced_flag_amounts else None
        m1_unpriced_count = sum(1 for f in flags if f.priced is None)

        if curve is None:
            # No schedule curve for this class at all (no comparator ever
            # priced) but this one study encounter's own Method 3 band still
            # reached the suppression floor somehow -- defensive, not
            # expected to fire on real data (the two share the same
            # comparator cohort), but the compression term in ``spine``
            # needs a curve to read, so there is genuinely nothing to
            # compute without one.
            unbanded.append(enc.encounter_id)
            continue
        spine = signature.spine(
            realized=obs.realized,
            score=obs.score,
            curve=curve,
            code_median=code_medians[enc.primary_cpt],
            multiplier=multiplier.value,
            m3_expected=m3.expected,
            m1_total=m1_total,
            m1_split=m1_split,
            m1_unpriced_count=m1_unpriced_count,
        )
        decomposition = signature.decompose(spine)
        any_vehicle = any(f.vehicle_exists for f in flags)
        is_surgical = pack.encounter_class == "surgical"
        code_kind: signature.CodeKind = "procedure" if is_surgical else "level"
        sig = signature.signature_of(spine, any_vehicle=any_vehicle, code_kind=code_kind)

        le = next(x for x in linked if x.encounter.encounter_id == enc.encounter_id)
        friction = _payer_friction(le, linked_claims.get(enc.encounter_id), flags)

        ratio = ratio_of(obs.realized, m3.expected)
        outputs = witness_mod.to_jsonable(
            {
                "score": obs.score.value,
                "realized": obs.realized,
                "expected": m3.expected,
                "ratio": ratio.value,
                "spine": spine,
                "signature": sig,
            }
        )
        enc_witness = witness_mod.witness(
            input_hash=input_hash_value,
            rulebook_version=pack.rulebook_version,
            weights_version=pack.weights_version,
            package_versions={"worth-complexity": __version__},
            outputs=outputs,
            key=witness_key,
        )

        results.append(
            adequacy(
                obs,
                m3,
                curve,
                multiplier,
                spine=spine,
                decomposition=decomposition,
                signature=sig,
                method1_flags=flags,
                payer_friction=friction,
                witness=enc_witness,
                rulebook_version=pack.rulebook_version,
                weights_version=pack.weights_version,
                encounter_class=pack.encounter_class,
            )
        )

    stage("trends")
    # Over time (decision 7, CONTRACT-SEEDS.md): built from the same
    # ``results``/``observations`` the population stage below reads, one
    # ``PeriodRow`` per priced study encounter -- nothing here needs a
    # second pass over the extract.
    obs_by_id = {o.scored.encounter.encounter_id: o for o in observations}
    period_rows = tuple(
        population.PeriodRow(
            code=a.cpt,
            service_date=obs_by_id[a.encounter_id].scored.encounter.service_date,
            score=a.score.value,
            realized=a.realized,
            expected=a.expected,
            signature=a.signature,
            payer_label=labels[obs_by_id[a.encounter_id].payer_id],
            service_line=obs_by_id[a.encounter_id].scored.encounter.service_line,
        )
        for a in results
    )
    trends = population.periods(
        period_rows,
        policy_date=policy_date,
        rulebook_version=pack.rulebook_version,
        weights_version=pack.weights_version,
    )
    domain_trends = population.periods(
        period_rows,
        policy_date=policy_date,
        rulebook_version=pack.rulebook_version,
        weights_version=pack.weights_version,
        by="domain",
    )

    stage("population")
    pack_markers = tuple((m.marker_id, m.provenance) for m in pack.markers)
    by_code_all: dict[str, list[Observation]] = defaultdict(list)
    for o in observations:
        if o.cohort is Cohort.STUDY:
            by_code_all[o.scored.encounter.primary_cpt].append(o)

    comparator_scores_by_code: dict[str, list[Decimal]] = defaultdict(list)
    for o in observations:
        if o.cohort is Cohort.COMPARATOR:
            comparator_scores_by_code[o.scored.encounter.primary_cpt].append(Decimal(o.score.value))
    comparator_distributions = {
        code: describe(tuple(values)) for code, values in comparator_scores_by_code.items()
    }
    comparator_points = tuple(
        population.ScatterPoint(
            o.scored.encounter.encounter_id,
            o.score.value,
            o.realized,
            labels[o.payer_id],
            _schedule_scale(o.realized, multipliers[o.payer_id].value),
        )
        for o in observations
        if o.cohort is Cohort.COMPARATOR and o.payer_id in multipliers
    )
    comparator_slope = (
        population.pooled_slope(tuple((p.score, p.schedule_scale) for p in comparator_points))
        if comparator_points
        else None
    )

    adequacy_by_id = {a.encounter_id: a for a in results}
    cards: dict[str, population.CodeCard] = {}
    queue_rows: list[population.QueueRow] = []

    for code in sorted(by_code_all):
        cohort = by_code_all[code]
        strata = code_strata(cohort, code, labels)
        study_distribution = describe(tuple(Decimal(o.score.value) for o in cohort))
        comp_codes = population.comparator_codes(study_distribution, comparator_distributions)
        service_line_counts = Counter(o.scored.encounter.service_line for o in cohort)
        modal_service_line = service_line_counts.most_common(1)[0][0]

        rows: list[population.EncounterRow] = []
        code_flags: list[Method1Flag] = []
        for o in cohort:
            enc_id = o.scored.encounter.encounter_id
            code_flags.extend(flags_by_encounter.get(enc_id, ()))
            a = adequacy_by_id.get(enc_id)
            if a is None:
                continue
            rows.append(population.encounter_row(a, o, payer_label=labels[o.payer_id]))

        suppressed = len(cohort) < SUPPRESSION_THRESHOLD
        method1_summary = method1.summarize(tuple(code_flags)) if code_flags else None

        if rows:
            cards[code] = population.code_card(
                code=code,
                encounter_class=pack.encounter_class,
                service_line=modal_service_line,
                n=len(cohort),
                rows=tuple(rows),
                strata=strata,
                comparator_codes=comp_codes,
                comparator_points=comparator_points,
                comparator_slope=comparator_slope,
                method1=method1_summary,
                markers=pack_markers,
                rulebook_version=pack.rulebook_version,
                weights_version=pack.weights_version,
                rule_pack_digest=pack.digest,
                suppressed=suppressed,
                suppression_reason=(
                    f"n = {len(cohort)}, below the suppression threshold of {SUPPRESSION_THRESHOLD}"
                    if suppressed
                    else None
                ),
            )
        else:
            # Every code that appears in the study cohort still needs a card
            # to sit behind its IndexRecord (``IndexRecord.card`` is not
            # optional); a code that lost every one of its encounters to
            # ``unmultiplied``/``unbanded`` gets a bare one instead of
            # ``code_card()``'s "at least one row" guard tripping the run.
            health = population.instrument_health(
                {},
                pack_markers,
                rulebook_version=pack.rulebook_version,
                weights_version=pack.weights_version,
                rule_pack_digest=pack.digest,
            )
            cards[code] = population.CodeCard(
                code=code,
                encounter_class=pack.encounter_class,
                service_line=modal_service_line,
                n=len(cohort),
                scored=0,
                method0=None,
                strata=strata,
                comparator_codes=comp_codes,
                comparator_slope=comparator_slope,
                points=(),
                comparator_points=comparator_points,
                distribution=study_distribution,
                histogram=population.histogram(tuple(o.score.value for o in cohort)),
                top_quintile_cutoff=0,
                ratio_by_decile=(),
                ratio_at=(),
                adequacy=None,
                adequacy_interval=None,
                shortfall=None,
                signature_mix=None,
                method1=method1_summary,
                payer_friction=(),
                instrument_health=health,
                headline=f"{code}: no encounter could be priced against a Method 3 band",
                rulebook_version=pack.rulebook_version,
                weights_version=pack.weights_version,
                suppressed=True,
                suppression_reason="no encounter could be priced against a Method 3 band",
            )

        card = cards.get(code)
        cutoff = card.top_quintile_cutoff if card else 0
        for o in cohort:
            enc_id = o.scored.encounter.encounter_id
            a = adequacy_by_id.get(enc_id)
            if a is None:
                continue
            flags = flags_by_encounter.get(enc_id, ())
            missed = [f for f in flags if f.bucket == "missed"]
            if a.score.value < cutoff and not flags:
                continue
            strengths: list[method1.Strength] = [f.evidence_strength for f in flags]
            strength = _strongest(strengths)
            all_scores = [x.score.value for x in cohort]
            rank = sum(1 for s in all_scores if s <= a.score.value)
            with money_context():
                percentile = (Decimal(rank) / Decimal(len(all_scores))).quantize(Decimal("0.0001"))
            outcome: Literal["paid", "denied", "downcoded", "pending"]
            if a.payer_friction.denied:
                outcome = "denied"
            elif a.payer_friction.downcoded:
                outcome = "downcoded"
            else:
                outcome = "paid"
            queue_rows.append(
                population.QueueRow(
                    encounter_id=enc_id,
                    code=code,
                    score=a.score.value,
                    code_percentile=percentile,
                    flags=flags,
                    has_missed_flag=bool(missed),
                    missed_candidate_code=missed[0].candidate_codes[0] if missed else None,
                    evidence_strength=strength,
                    outcome_835=outcome,
                    payer_label=labels[o.payer_id],
                    site=o.scored.encounter.facility_npi,
                    surgeon=o.scored.encounter.surgeon_id,
                    top_quintile_cutoff=cutoff,
                )
            )

    sorted_cards = tuple(cards[code] for code in sorted(cards))
    compare = population.compare_rows(sorted_cards) if sorted_cards else ()
    queue = population.queue(tuple(queue_rows))

    stage("records")
    records = index_records(
        observations,
        tuple(results),
        institution=next((e.facility_npi for e in encounters), "unknown"),
        labels=labels,
        linkage_rate=linkage.rate,
        pack=pack,
        provenance_filter=tuple(sorted(provenance_filter)),
        package_version=__version__,
        locality=priced_locality,
        setting=place,
        reference=reference_vintage,
        cards=cards,
    )

    class_run = ClassRun(
        encounter_class=cls,
        pack=pack,
        rulebook_version=pack.rulebook_version,
        weights_version=pack.weights_version,
        setting=place,
        provenance_filter=tuple(sorted(provenance_filter)),
        observations=observations,
        priced=priced,
        schedule_curve=curve,
        multipliers=multipliers,
        thin_payers=thin_payers,
        slopes=slopes,
        thin_strata=thin_strata,
        adequacies=tuple(results),
        records=records,
        cards=sorted_cards,
        compare=compare,
        queue=queue,
        extrapolated=(),
        unmultiplied=tuple(unmultiplied),
        unbanded=tuple(unbanded),
        trends=trends,
        domain_trends=domain_trends,
    )
    return class_run, priced_locality, locality_name, tuple(class_unscored)
