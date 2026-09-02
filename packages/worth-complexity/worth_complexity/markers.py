"""Turning an operative extract into provenance-carrying complexity markers.

Every marker produced here is tagged ``structured``: it is read from a discrete
field a hospital system already populates for its own operational reasons, not
inferred from narrative and not produced by a model. That is what puts these
markers inside Layer A, and it is why the first index can be published without
a partner's AI governance committee in the critical path.

Both Layer A lanes run here. The methodology puts the marker families, operative time, anatomic
extent, team composition, intraoperative events,
comorbidity burden, "from structured fields where they exist and rule-based
note reading where they do not", and structured fields carry only three of the
five. Anatomic extent, adhesion severity and intraoperative events live in the
operative note and nowhere else; a score computed without them understates every
hard case, which is why a structured-only figure is a subset of Layer A rather
than Layer A.

Markers from the note are tagged ``rule``, not ``structured``, and the tag is
what a provenance filter acts on. Nothing here produces ``ml`` or ``generative``
markers, so no model can reach a published score by accident.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import TYPE_CHECKING

from worth_complexity import notes as notes_mod
from worth_complexity.cases import ClinicalExtract, parse_decimal, parse_dttm, parse_int
from worth_complexity.models import Encounter, Marker, SourceRef

if TYPE_CHECKING:
    from worth_complexity.rulepack import MarkerRule, RulePack

EXTRACTOR_VERSION = "0.1.0"

_OPERATING_ROLES = frozenset({"Primary Surgeon", "Assistant Surgeon", "Co-Surgeon"})
_ASSISTANT_ROLES = frozenset({"Assistant Surgeon", "Co-Surgeon"})


def extract(
    extract_: ClinicalExtract,
    encounters: tuple[Encounter, ...],
    pack: RulePack | None = None,
) -> dict[str, tuple[Marker, ...]]:
    """Extract every Layer A marker, keyed by encounter id.

    Structured markers always. Narrative markers too when a rule pack is given
    and the extract carries notes, omitting the pack yields the structured
    subset, which is what a partner with no note dataset can produce.
    """
    staff = _staff_by_log(extract_)
    diagnoses = _dx_by_csn(extract_)
    log_index = {row["log_id"]: i for i, row in enumerate(extract_.or_log.rows)}
    out: dict[str, tuple[Marker, ...]] = {}
    for enc in encounters:
        structured = _for_encounter(
            extract_,
            enc,
            log_index[enc.encounter_id],
            staff.get(enc.encounter_id, []),
            diagnoses.get(enc.csn, ()),
        )
        narrative = _narrative(extract_, enc, pack) if pack is not None else ()
        out[enc.encounter_id] = (*structured, *narrative)
    return out


def _narrative(
    extract_: ClinicalExtract,
    enc: Encounter,
    pack: RulePack,
) -> tuple[Marker, ...]:
    """Run every narrative rule in the pack over this encounter's notes."""
    out: list[Marker] = []
    for rule in pack.markers:
        if not rule.reads_narrative:
            continue
        notes = extract_.notes_for(enc.encounter_id, rule.note_types)
        if not notes:
            continue
        marker = _from_notes(enc, rule, notes, pack.digest)
        if marker is not None:
            out.append(marker)
    return tuple(out)


def _from_notes(
    enc: Encounter,
    rule: MarkerRule,
    notes: tuple[notes_mod.Note, ...],
    digest: str,
) -> Marker | None:
    """Aggregate one rule's matches across an encounter's notes into one marker.

    A count or an ordinal is emitted even when nothing matched: a note that never
    mentions the bowel is evidence the bowel was not involved, and zero is the
    reading. A number is not, which is why ``numeric`` returns nothing rather
    than claiming an operative time of zero minutes.
    """
    hits: list[tuple[notes_mod.Note, notes_mod.Match]] = [
        (note, match)
        for note in notes
        for match in notes_mod.find(
            note, rule.patterns, allowed=rule.sections, excluded=rule.excluded_sections
        )
    ]

    value: Decimal
    evidence: str | None
    ref: SourceRef

    if rule.aggregate == "numeric":
        if not hits:
            return None
        note, match = hits[0]
        value, evidence, ref = match.value, match.evidence, notes_mod.source_ref(note, match)
    elif rule.aggregate == "max_value":
        if hits:
            note, match = max(hits, key=lambda hit: hit[1].value)
            value, evidence = match.value, match.evidence
            ref = notes_mod.source_ref(note, match)
        else:
            value, evidence, ref = Decimal(0), None, _unmatched(notes[0])
    else:  # count_distinct
        value = Decimal(len({match.pattern_id for _, match in hits}))
        if hits:
            note, match = hits[0]
            evidence = "; ".join(sorted({f"{m.pattern_id}: {m.evidence}" for _, m in hits}))[:600]
            ref = notes_mod.source_ref(note, match)
        else:
            evidence, ref = None, _unmatched(notes[0])

    return Marker(
        encounter_id=enc.encounter_id,
        marker_id=rule.marker_id,
        value=value,
        provenance="rule",
        source_ref=ref,
        extractor_id=f"rulepack/{rule.marker_id}",
        extractor_version=EXTRACTOR_VERSION,
        rule_pack_digest=digest,
        evidence=evidence,
    )


def _unmatched(note: notes_mod.Note) -> SourceRef:
    """Where a rule looked and found nothing. Still a traceable answer."""
    return SourceRef(note.filename, note.sha256, 1, "no match")


def _for_encounter(
    extract_: ClinicalExtract,
    enc: Encounter,
    index: int,
    team: list[tuple[str, str]],
    diagnoses: tuple[str, ...],
) -> tuple[Marker, ...]:
    """Every structured marker for one operative log."""
    row = extract_.or_log.rows[index]
    where = f"or_log {enc.encounter_id}"

    start = parse_dttm(row["procedure_start_dttm"], f"{where}.procedure_start_dttm")
    close = parse_dttm(row["procedure_close_dttm"], f"{where}.procedure_close_dttm")
    minutes = (Decimal((close - start).total_seconds()) / Decimal(60)).quantize(Decimal("1"))

    specialties = {sp for role, sp in team if role in _OPERATING_ROLES}
    assistants = sum(1 for role, _ in team if role in _ASSISTANT_ROLES)

    log = extract_.or_log
    return (
        _marker(
            enc,
            "operative_minutes",
            minutes,
            log.ref(index, "procedure_start_dttm..procedure_close_dttm"),
            "optime_incision_close",
        ),
        _marker(
            enc,
            "asa_class",
            Decimal(parse_int(row["asa_class"], f"{where}.asa_class")),
            log.ref(index, "asa_class"),
            "optime_asa_class",
        ),
        _marker(
            enc,
            "estimated_blood_loss_ml",
            parse_decimal(row["ebl_ml"], f"{where}.ebl_ml"),
            log.ref(index, "ebl_ml"),
            "optime_ebl",
        ),
        _marker(
            enc,
            "inpatient",
            Decimal(1 if enc.inpatient else 0),
            log.ref(index, "patient_class"),
            "optime_patient_class",
        ),
        _marker(
            enc,
            "distinct_surgical_specialties",
            Decimal(max(len(specialties), 1)),
            extract_.or_staff.ref(0, "specialty"),
            "optime_staff_specialty",
        ),
        _marker(
            enc,
            "assistant_surgeon_count",
            Decimal(assistants),
            extract_.or_staff.ref(0, "role"),
            "optime_staff_role",
        ),
        _marker(
            enc,
            "procedure_count",
            Decimal(len(enc.procedures)),
            extract_.or_log_proc.ref(0, "cpt"),
            "optime_procedure_panel",
        ),
        _marker(
            enc,
            "comorbidity_count",
            Decimal(len(diagnoses)),
            extract_.encounter_dx.ref(0, "icd10"),
            "encounter_dx_count",
        ),
    )


def _marker(
    encounter: Encounter,
    marker_id: str,
    value: Decimal,
    ref: SourceRef,
    extractor_id: str,
) -> Marker:
    """One structured marker. Every field of the provenance record is populated."""
    return Marker(
        encounter_id=encounter.encounter_id,
        marker_id=marker_id,
        value=value,
        provenance="structured",
        source_ref=ref,
        extractor_id=extractor_id,
        extractor_version=EXTRACTOR_VERSION,
    )


def _staff_by_log(extract_: ClinicalExtract) -> dict[str, list[tuple[str, str]]]:
    by_log: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in extract_.or_staff.rows:
        by_log[row["log_id"]].append((row["role"], row["specialty"]))
    return by_log


def _dx_by_csn(extract_: ClinicalExtract) -> dict[str, tuple[str, ...]]:
    by_csn: dict[str, list[str]] = defaultdict(list)
    for row in extract_.encounter_dx.rows:
        by_csn[row["csn"]].append(row["icd10"])
    return {k: tuple(v) for k, v in by_csn.items()}
