"""One run, as the console reads it.

This is ``tools/export_run.py`` from worth-complexity, moved here and typed.
The dict-building became pydantic models for one reason: FastAPI turns them
into an OpenAPI document, and the console's TypeScript types are generated
from that document, so the front end cannot drift from what the server sends.
The field names are unchanged from the file the console used to compile in.

Two rules the serializer enforces, both inherited:

* Payers are blinded in every computed value. The label, never the name.
* Comparator encounters anchor the curve and are never opened in the console,
  so their derivations are not shipped. Study encounters carry everything
  needed to check a number by hand.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from worth_complexity import cases, x12
from worth_complexity import markers as markers_mod
from worth_complexity import notes as notes_mod
from worth_complexity.models import Cohort
from worth_complexity.pipeline import STAGES
from worth_complexity.version import __version__

from worth_api.dataset import (
    DeliveredFile,
    EdiBundle,
    NoteBundle,
    TableFile,
    delivered_files,
)

if TYPE_CHECKING:
    from pathlib import Path

    from worth_complexity.models import ScoredEncounter
    from worth_complexity.pipeline import Run
    from worth_complexity.rulepack import RulePack as PackType


class Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


def d(value: Decimal | float | int) -> float:
    return float(value)


# --------------------------------------------------------------------------
# Models. One per interface in the console's types.ts, same names.
# --------------------------------------------------------------------------


class RunMeta(Model):
    package: str
    rule_pack: str
    digest: str
    status: str
    filter: str


class Step(Model):
    label: str
    detail: str
    result: str
    stage: str
    """Which pipeline stage this step belongs to; see ``RunResult.timings``."""


class MarkerRow(Model):
    id: str
    provenance: str
    value: float
    low: float
    high: float
    norm: float
    weight: float
    contribution: float
    source: str
    evidence: str | None


class SupersededMarker(Model):
    id: str
    provenance: str
    value: float
    source: str
    evidence: str | None


class DeniedLine(Model):
    cpt: str
    codes: list[str]


class Encounter(Model):
    id: str
    account: str
    cpt: str
    procedures: list[str]
    cohort: str
    specialty: str
    date: str
    payer: str
    """Blinded label, never the payer's name."""
    score: int
    minutes: float
    realized: float
    """The 835 allowed amount on the primary procedure's line."""
    realized_claim: float
    """The allowed amount across every line on the claim, for when they differ."""
    increased_service: bool
    """Modifier 22 on the primary procedure: the surgeon's own complexity claim."""
    schedule_expected: float | None
    """What the schedule curve pays at this score, in Medicare dollars."""
    expected: float | None
    """``multiplier x schedule_expected``, in this payer's dollars."""
    ratio: float | None
    pfs: PfsAmounts | None
    """Comparator encounters only: what they were priced at."""
    denied: list[DeniedLine]
    markers: list[MarkerRow]
    superseded: list[SupersededMarker]
    adequacy_trace: list[str]


class PfsAmounts(Model):
    """A comparator encounter's fee-schedule amounts, the denominator's inputs."""

    release: str
    """The CMS release in force on the service date."""
    amount: float
    """PFS allowed amount at that release. Feeds the payer multiplier."""
    reference_release: str
    reference_amount: float
    """PFS allowed amount at the run's reference release. Feeds the schedule curve."""
    archive_sha256: str
    """The reference release's CMS archive, the receipt for ``reference_amount``."""


class ScheduleCurve(Model):
    """The fee schedule's complexity relation, fitted on the comparator cohort.

    Medicare PFS dollars against Layer A score, no payer in it.
    """

    id: str
    n: int
    slope: float
    intercept: float
    r2: float
    x_min: float
    x_max: float
    release: str
    archive_sha256: str
    locality: str
    locality_name: str
    setting: str


class MultiplierRow(Model):
    """A payer's multiple of the schedule. Partner-only: never in an index record."""

    payer: str
    """Blinded label."""
    n: int
    value: float
    q1: float
    q3: float


class Distribution(Model):
    n: int
    min: float
    q1: float
    median: float
    q3: float
    max: float


class SlopeStratum(Model):
    payer: str
    n: int
    slope: float
    ci_low: float
    ci_high: float
    r2: float
    differentiates: bool
    suppressed: bool


class IndexRecord(Model):
    code: str
    institution: str
    period_start: str
    period_end: str
    n: int
    distribution: Distribution
    slopes: list[SlopeStratum]
    adequacy: float | None
    adequacy_ci_low: float | None
    adequacy_ci_high: float | None
    scored: int
    excluded: int
    locality: str
    setting: str
    reference_release: str
    suppressed: bool
    suppression_reason: str | None


class RulePattern(Model):
    id: str
    regex: str
    value: float | None
    scale: float


class MarkerRule(Model):
    id: str
    provenance: str
    weight: float
    anchor_low: float
    anchor_high: float
    note: str | None
    aggregate: str
    note_types: list[str]
    sections: list[str]
    excluded_sections: list[str]
    patterns: list[RulePattern]


class RulePack(Model):
    id: str
    version: str
    status: str
    digest: str
    markers: list[MarkerRule]


class NoteEngine(Model):
    header: str
    negation: str
    historical: str
    terminator: str
    lookback: int


class Summary(Model):
    linkage_rate: float
    study: int
    comparator: int
    scored: int
    excluded: int
    codes: list[str]
    institution: str
    period_start: str | None
    period_end: str | None
    suppressed_strata: int
    strata: int
    locality: str
    locality_name: str
    setting: str
    reference_release: str
    unmultiplied: int
    """Study encounters whose payer had too few comparators for a multiplier."""


class Scoring(Model):
    """The formula and the reader, served without a run: the Score tab needs no numbers."""

    rule_pack: RulePack
    note_engine: NoteEngine


class RunResult(Model):
    """Everything one run computed. No file contents: those are served separately."""

    run: RunMeta
    rule_pack: RulePack
    note_engine: NoteEngine
    steps: list[Step]
    records: list[IndexRecord]
    encounters: list[Encounter]
    schedule_curve: ScheduleCurve
    multipliers: list[MultiplierRow]
    """Partner-only. The console shows them because it runs on the partner's own data."""
    thin_payers: list[str]
    thin_strata: list[str]
    summary: Summary
    timings: dict[str, int]
    """Milliseconds per pipeline stage, measured by the server around ``run()``."""


# --------------------------------------------------------------------------
# Serializers. Same logic as before, returning models instead of dicts.
# --------------------------------------------------------------------------


def record_rows(r: Run) -> list[IndexRecord]:
    """The published unit: one record per code, per institution, per period."""
    return [
        IndexRecord(
            code=rec.code,
            institution=rec.institution,
            period_start=rec.period_start.isoformat(),
            period_end=rec.period_end.isoformat(),
            n=rec.n,
            distribution=Distribution(
                n=rec.distribution.n,
                min=d(rec.distribution.minimum),
                q1=d(rec.distribution.q1),
                median=d(rec.distribution.median),
                q3=d(rec.distribution.q3),
                max=d(rec.distribution.maximum),
            ),
            slopes=[
                SlopeStratum(
                    payer=s.payer_label,
                    n=s.n,
                    slope=d(s.slope),
                    ci_low=d(s.slope_interval.low),
                    ci_high=d(s.slope_interval.high),
                    r2=d(s.r_squared),
                    differentiates=s.differentiates,
                    suppressed=s.suppressed,
                )
                for s in rec.slopes
            ],
            adequacy=d(rec.adequacy.value) if rec.adequacy else None,
            adequacy_ci_low=d(rec.adequacy_interval.low) if rec.adequacy_interval else None,
            adequacy_ci_high=d(rec.adequacy_interval.high) if rec.adequacy_interval else None,
            scored=rec.scored,
            excluded=rec.excluded,
            locality=rec.locality,
            setting=rec.setting,
            reference_release=rec.reference_release,
            suppressed=rec.suppressed,
            suppression_reason=rec.suppression_reason,
        )
        for rec in r.records
    ]


def marker_rows(pack: PackType, scored: ScoredEncounter) -> list[MarkerRow]:
    """Every admitted marker with its arithmetic, its lane and its evidence."""
    rules = {r.marker_id: r for r in pack.markers}
    total = sum((rules[m.marker_id].weight for m in scored.markers), start=Decimal(0))
    out = []
    for m in scored.markers:
        rule = rules[m.marker_id]
        norm = rule.normalise(m.value)
        weight = rule.weight / total
        out.append(
            MarkerRow(
                id=m.marker_id,
                provenance=m.provenance,
                value=d(m.value),
                low=d(rule.anchor_low),
                high=d(rule.anchor_high),
                norm=d(norm),
                weight=d(weight),
                contribution=d(norm * weight * 100),
                source=str(m.source_ref),
                evidence=m.evidence[:240] if m.evidence else None,
            )
        )
    return out


def superseded_rows(scored: ScoredEncounter) -> list[SupersededMarker]:
    """Markers a higher-precedence lane displaced. Shown, not hidden."""
    return [
        SupersededMarker(
            id=m.marker_id,
            provenance=m.provenance,
            value=d(m.value),
            source=str(m.source_ref),
            evidence=m.evidence,
        )
        for m in scored.superseded
    ]


def encounters_payload(r: Run) -> list[Encounter]:
    labels = r.payer_labels
    ratios = {a.encounter_id: a for a in r.adequacies}
    out = []
    for obs in r.observations:
        enc = obs.scored.encounter
        study = enc.cohort is Cohort.STUDY
        a = ratios.get(enc.encounter_id)
        minutes = next(m.value for m in obs.scored.markers if m.marker_id == "operative_minutes")
        out.append(
            Encounter(
                id=enc.encounter_id,
                account=enc.account_id,
                cpt=enc.primary_cpt,
                procedures=[f"{c}-{m}" if m else c for c, m in enc.procedures],
                cohort=enc.cohort.value,
                specialty=enc.specialty,
                date=enc.service_date.isoformat(),
                payer=labels.get(obs.payer_id, "Payer ?"),
                score=obs.score.value,
                minutes=d(minutes),
                realized=d(obs.realized),
                realized_claim=d(obs.linked.realized_claim),
                increased_service=enc.increased_service,
                schedule_expected=d(a.schedule_expected) if a else None,
                expected=d(a.expected) if a else None,
                ratio=d(a.ratio.value) if a else None,
                pfs=pfs_amounts(r, enc.encounter_id),
                denied=[
                    DeniedLine(cpt=ln.cpt, codes=list(ln.denial_codes))
                    for ln in obs.linked.denied_lines
                ],
                markers=marker_rows(r.pack, obs.scored) if study else [],
                superseded=superseded_rows(obs.scored) if study else [],
                adequacy_trace=list(a.trace) if a else [],
            )
        )
    out.sort(key=lambda e: (e.cohort, e.cpt, e.score))
    return out


def pfs_amounts(r: Run, encounter_id: str) -> PfsAmounts | None:
    p = r.priced.get(encounter_id)
    if p is None:
        return None
    return PfsAmounts(
        release=f"RVU{p.at_date.rule_year % 100}{'ABCD'[p.at_date.quarter - 1]}",
        amount=d(p.at_date.amount),
        reference_release=r.reference.label,
        reference_amount=d(p.at_reference.amount),
        archive_sha256=r.reference.archive_sha256,
    )


def rule_pack(pack: PackType) -> RulePack:
    """The scoring formula itself, as data, rendered from the JSON the scorer loads."""
    return RulePack(
        id=pack.rule_pack_id,
        version=pack.version,
        status=pack.status,
        digest=pack.digest,
        markers=[
            MarkerRule(
                id=m.marker_id,
                provenance=m.provenance,
                weight=d(m.weight),
                anchor_low=d(m.anchor_low),
                anchor_high=d(m.anchor_high),
                note=m.note,
                aggregate=m.aggregate,
                note_types=sorted(m.note_types),
                sections=sorted(m.sections),
                excluded_sections=sorted(m.excluded_sections),
                patterns=[
                    RulePattern(
                        id=pat.pattern_id,
                        regex=pat.regex.pattern,
                        value=d(pat.value) if pat.value is not None else None,
                        scale=d(pat.scale),
                    )
                    for pat in m.patterns
                ],
            )
            for m in pack.markers
        ],
    )


def note_engine() -> NoteEngine:
    """The reader's own expressions, so the page shows what actually runs."""
    return NoteEngine(
        header=notes_mod._HEADER.pattern,
        negation=notes_mod._NEGATION.pattern,
        historical=notes_mod._HISTORICAL.pattern,
        terminator=notes_mod._TERMINATOR.pattern,
        lookback=notes_mod._LOOKBACK,
    )


def schedule_curve_payload(r: Run) -> ScheduleCurve:
    c = r.schedule_curve
    return ScheduleCurve(
        id=c.curve_id,
        n=c.n,
        slope=d(c.slope),
        intercept=d(c.intercept),
        r2=d(c.r_squared),
        x_min=d(c.x_min),
        x_max=d(c.x_max),
        release=r.reference.label,
        archive_sha256=r.reference.archive_sha256,
        locality=r.locality,
        locality_name=r.locality_name,
        setting=r.setting.value,
    )


def multipliers_payload(r: Run) -> list[MultiplierRow]:
    return [
        MultiplierRow(payer=m.payer_label, n=m.n, value=d(m.value), q1=d(m.q1), q3=d(m.q3))
        for m in sorted(r.multipliers.values(), key=lambda m: m.payer_label)
    ]


def summary_payload(r: Run) -> Summary:
    starts = [rec.period_start for rec in r.records]
    ends = [rec.period_end for rec in r.records]
    return Summary(
        linkage_rate=d(r.linkage.rate),
        study=len(r.study),
        comparator=len(r.comparator),
        scored=len(r.adequacies),
        excluded=len(r.extrapolated),
        codes=sorted({o.scored.encounter.primary_cpt for o in r.study}),
        institution=r.records[0].institution if r.records else "unknown",
        period_start=min(starts).isoformat() if starts else None,
        period_end=max(ends).isoformat() if ends else None,
        suppressed_strata=sum(1 for rec in r.records for s in rec.slopes if s.suppressed),
        strata=sum(len(rec.slopes) for rec in r.records),
        locality=r.locality,
        locality_name=r.locality_name,
        setting=r.setting.value,
        reference_release=r.reference.label,
        unmultiplied=len(r.unmultiplied),
    )


def steps(
    r: Run,
    *,
    tables: int,
    remittance_files: int,
    lines: int,
    structured: int,
    narrative: int,
    note_count: int,
) -> list[Step]:
    scores = [o.score.value for o in r.observations]
    curve = r.schedule_curve
    return [
        Step(
            label="Read the extract",
            detail="Five delimited tables and the note dataset. Each file is hashed as "
            "received, and every value read from it carries that hash.",
            result=f"{tables} tables, {note_count} notes",
            stage="extract",
        ),
        Step(
            label="Check the limited data set",
            detail="Confirm the extract carries no direct identifiers. Service dates and ZIP "
            "stay: episode timing and market slicing depend on them.",
            result=f"{len(r.observations)} records, no direct identifiers",
            stage="extract",
        ),
        Step(
            label="Build encounters",
            detail="One encounter per operative log, with its procedure panel in order.",
            result=f"{len(r.observations)} encounters",
            stage="encounters",
        ),
        Step(
            label="Extract structured markers",
            detail="Discrete fields from the OR record: operative time, ASA class, team "
            "composition, blood loss, comorbidity count. Each records the file, row and column "
            "it came from.",
            result=f"{structured} structured markers",
            stage="markers",
        ),
        Step(
            label="Read the operative notes",
            detail="Section parsing, pattern matching and negation over native free text. An "
            "organ named under INDICATION is history, not work performed today, and a finding "
            "that was ruled out is not a finding. No model in the path.",
            result=f"{narrative} narrative markers",
            stage="markers",
        ),
        Step(
            label="Normalise against anchors",
            detail="Each marker maps to 0-1 against anchors published in the rule pack. Fixed "
            "anchors, not cohort percentiles, so a score means the same thing next month.",
            result=f"{len(r.pack.markers)} anchors",
            stage="score",
        ),
        Step(
            label="Build the reference distribution",
            detail="Layer A scores per code, as a distribution rather than a mean. One of "
            "the two outputs the methodology calls defensible from the first validated "
            "extract, and what Method 2 compares a case against.",
            result=f"{len(r.records)} codes described",
            stage="records",
        ),
        Step(
            label="Weight and score",
            detail="Weighted sum, rounded to the ordinal 0-100 scale. No model in the scoring "
            "path.",
            result=f"scores {min(scores)}-{max(scores)}",
            stage="score",
        ),
        Step(
            label="Parse the remittance",
            detail="X12 835 segment streams. Allowed amount from AMT*B6, adjustments and "
            "reason codes from CAS.",
            result=f"{remittance_files} files, {lines} service lines",
            stage="remittance",
        ),
        Step(
            label="Link payment to case",
            detail="Match on the billing account number. The linkage rate is part of the "
            "result, not a log line.",
            result=f"linkage {d(r.linkage.rate) * 100:.2f}%",
            stage="link",
        ),
        Step(
            label="Price the comparator cohort",
            detail="Every comparator encounter's primary code through worth-fees: the Medicare "
            "PFS allowed amount in the institution's locality and setting, at the release in "
            "force on the service date and again at the reference release. Each amount carries "
            "the CMS archive hash it came from.",
            result=f"{len(r.priced)} encounters, {r.locality} {r.setting.value}, "
            f"{r.reference.label}",
            stage="price",
        ),
        Step(
            label="Fit the schedule curve",
            detail="Least squares through (score, PFS amount) across the whole comparator "
            "cohort. The fee schedule's own price of complexity in specialties whose "
            "valuation is not in dispute, in Medicare dollars, with no payer in it.",
            result=f"n={curve.n}, R2 {d(curve.r_squared):.2f}",
            stage="curve",
        ),
        Step(
            label="Derive payer multipliers",
            detail="Per payer, the median of realized over PFS across its own comparator "
            "encounters: its contract level as a multiple of Medicare. A rate, so it stays "
            "with the partner and never enters an index record.",
            result=f"{len(r.multipliers)} payers"
            + (f", {len(r.thin_payers)} below the floor" if r.thin_payers else ""),
            stage="multipliers",
        ),
        Step(
            label="Fit the Method 0 slopes",
            detail="Within one code and one blinded payer, does payment move with "
            "complexity? Reported as a slope with its confidence interval; a stratum below "
            "the suppression floor is withheld rather than shown.",
            result=f"{sum(len(rec.slopes) for rec in r.records)} strata",
            stage="slopes",
        ),
        Step(
            label="Compute adequacy",
            detail="Realized payment on the primary line over the payer's multiplier times "
            "the schedule curve at the encounter's score, both dollars, published per code "
            "rather than blended across them. Depends on the curve, which the methodology "
            "calls the part that will take longer.",
            result=f"{len(r.records)} index records",
            stage="adequacy",
        ),
    ]


def run_result(r: Run, dataset_dir: Path, timings: dict[str, int] | None = None) -> RunResult:
    """Serialize one finished run against the dataset it ran on."""
    clinical = dataset_dir / "clinical"
    remittance = dataset_dir / "remittance"

    extract = cases.read_extract(clinical)
    encs = cases.encounters(extract)
    all_markers = [m for v in markers_mod.extract(extract, encs, r.pack).values() for m in v]
    structured = sum(1 for m in all_markers if m.provenance == "structured")
    narrative = sum(1 for m in all_markers if m.provenance == "rule")

    return RunResult(
        run=RunMeta(
            package=f"worth-complexity {__version__}",
            rule_pack=f"{r.pack.rule_pack_id} v{r.pack.version}",
            digest=r.pack.digest,
            status=r.pack.status,
            filter="+".join(r.provenance_filter),
        ),
        rule_pack=rule_pack(r.pack),
        note_engine=note_engine(),
        steps=steps(
            r,
            tables=len(extract_tables(extract)),
            remittance_files=len(list(remittance.glob("*.edi"))),
            lines=len(x12.read_directory(remittance)),
            structured=structured,
            narrative=narrative,
            note_count=len(extract.notes),
        ),
        records=record_rows(r),
        encounters=encounters_payload(r),
        schedule_curve=schedule_curve_payload(r),
        multipliers=multipliers_payload(r),
        thin_payers=list(r.thin_payers),
        thin_strata=list(r.thin_strata),
        summary=summary_payload(r),
        timings={stage: (timings or {}).get(stage, 0) for stage in STAGES},
    )


def extract_tables(extract: cases.ClinicalExtract) -> tuple[cases.Table, ...]:
    """The delimited tables an extract is made of, for the step count."""
    return (
        extract.or_log,
        extract.or_log_proc,
        extract.or_staff,
        extract.encounter_dx,
        extract.patient_lds,
    )


__all__ = [
    "DeliveredFile",
    "EdiBundle",
    "NoteBundle",
    "RunResult",
    "Scoring",
    "TableFile",
    "delivered_files",
    "run_result",
]
