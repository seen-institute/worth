"""Reading a partner's office-visit extract.

The visit class scores an E/M office encounter the way ``cases.py`` scores an
operative log: eight pipe-delimited tables plus a note dataset, read into one
:class:`Encounter` per visit and one set of Layer A markers per encounter,
behind the same :class:`~worth_complexity.extracts.ClinicalExtract` shape the
pipeline reads every class through.

Five of the pack's six markers are read straight off structured fields: total
documented time, problems addressed, data reviewed or ordered, whether a
medication was actually changed, and the coordination minutes the visit set
in motion over the following 30 days. The sixth, ``patient_context``, is
decision 2 of CONTRACT-PACKS.md: a pregnancy-risk tier read off the problem
list when the patient carries a ``PREGNANCY*`` flag, and a shared
decision-making note rule — the same section-scoped, negation-aware reading
``notes.py`` does for the surgical pack — when they do not. Exactly one of
the two is ever emitted for a given encounter, so lane precedence in
``scoring.py`` never has to choose between them.

Method 1 for this class is not the surgical pack's note-phrase cross-check:
there is no separate procedure to name in an office note, only a structured
threshold that either was or was not crossed. :meth:`VisitExtract.facts`
supplies the flat mapping ``method1.evaluate_work_rules`` reads the pack's
``work_rules`` block against.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from worth_complexity import notes as notes_mod
from worth_complexity.cases import (
    ExtractError,
    Table,
    parse_date,
    parse_decimal,
    parse_int,
    read_notes,
    read_table,
)
from worth_complexity.models import Cohort, Encounter, Marker, SourceRef

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from worth_complexity.notes import Note
    from worth_complexity.rulepack import MarkerRule, RulePack

__all__ = ["VisitExtract", "read_visit_extract"]

EXTRACTOR_VERSION = "0.1.0"

_WINDOW_DAYS = 30
"""The clinical window a visit's ``window_end`` covers, per CONTRACT-PACKS.md's
visit table (``window_encounters.txt``: "one row per contact in the 30 days
after the visit")."""

_CLINICIAN_COORDINATION_TYPES = frozenset({"PORTAL", "TELEPHONE", "NURSE_CALL", "MFM_COMANAGEMENT"})
"""``coordination_minutes_clinician`` fact: clinician-initiated contact of
these kinds (CONTRACT-PACKS.md's Visit class section)."""

_NON_VEHICLE_TYPES = frozenset({"LAB_REVIEW", "PRIOR_AUTH"})
"""``non_vehicle_minutes`` fact: real time with no billing vehicle of its own."""

_PRESCRIPTION_ACTIONS = frozenset({"START", "ADJUST", "STOP"})
"""``prescription_management`` marker: a continuation alone does not count."""


@dataclass(frozen=True, slots=True)
class VisitExtract:
    """The office-visit tables and the note dataset, as delivered."""

    visit: Table
    visit_proc: Table
    encounter_dx: Table
    orders: Table
    med_orders: Table
    window_encounters: Table
    problem_list: Table
    patient_lds: Table
    notes: tuple[Note, ...]

    @property
    def encounter_class(self) -> str:
        return "visit"

    @property
    def tables(self) -> tuple[Table, ...]:
        return (
            self.visit,
            self.visit_proc,
            self.encounter_dx,
            self.orders,
            self.med_orders,
            self.window_encounters,
            self.problem_list,
            self.patient_lds,
        )

    def notes_for(self, encounter_id: str, note_types: frozenset[str]) -> tuple[Note, ...]:
        """This visit's notes of the given types, in delivery order."""
        return tuple(
            n for n in self.notes if n.encounter_id == encounter_id and n.note_type in note_types
        )

    def file_hashes(self) -> tuple[tuple[str, str], ...]:
        pairs = {(t.name, t.sha256) for t in self.tables}
        pairs |= {(n.filename, n.sha256) for n in self.notes}
        return tuple(sorted(pairs))

    def encounters(self) -> tuple[Encounter, ...]:
        return _encounters(self)

    def markers(
        self, encounters: tuple[Encounter, ...], pack: RulePack | None = None
    ) -> dict[str, tuple[Marker, ...]]:
        return _markers(self, encounters, pack)

    def facts(self, encounter_id: str) -> Mapping[str, Decimal | str | bool]:
        return _facts(self, encounter_id)


def read_visit_extract(directory: Path) -> VisitExtract:
    """Read the whole visit extract from a directory."""
    return VisitExtract(
        visit=read_table(
            directory / "visit.txt",
            required=frozenset(
                {
                    "visit_id",
                    "csn",
                    "patient_id",
                    "billing_account_id",
                    "service_date",
                    "service_line",
                    "specialty",
                    "cohort",
                    "site_npi",
                    "clinician_id",
                    "total_documented_minutes",
                }
            ),
        ),
        visit_proc=read_table(directory / "visit_proc.txt"),
        encounter_dx=read_table(directory / "encounter_dx.txt"),
        orders=read_table(directory / "orders.txt"),
        med_orders=read_table(directory / "med_orders.txt"),
        window_encounters=read_table(directory / "window_encounters.txt"),
        problem_list=read_table(directory / "problem_list.txt"),
        patient_lds=read_table(directory / "patient_lds.txt"),
        notes=read_notes(directory),
    )


# --------------------------------------------------------------------- encounters


def _proc_by_visit(extract: VisitExtract) -> dict[str, list[tuple[int, str, str]]]:
    out: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    for row in extract.visit_proc.rows:
        out[row["visit_id"]].append(
            (parse_int(row["sequence"], "visit_proc.sequence"), row["cpt"], row["modifier"])
        )
    return out


def _encounters(extract: VisitExtract) -> tuple[Encounter, ...]:
    """One :class:`Encounter` per visit, its primary CPT the sequence-1 code
    on ``visit_proc.txt`` (the billed E/M level; any care-management,
    digital-E/M or prolonged-service add-on sits at a later sequence)."""
    panels = _proc_by_visit(extract)
    out: list[Encounter] = []
    for row in extract.visit.rows:
        visit_id = row["visit_id"]
        panel = sorted(panels.get(visit_id, []))
        if not panel:
            msg = f"visit {visit_id}: no billed procedure lines"
            raise ExtractError(msg)
        service_date = parse_date(row["service_date"], f"visit {visit_id}.service_date")
        out.append(
            Encounter(
                encounter_id=visit_id,
                csn=row["csn"],
                patient_id=row["patient_id"],
                account_id=row["billing_account_id"],
                service_date=service_date,
                service_line=row["service_line"].upper(),
                specialty=row["specialty"],
                cohort=Cohort(row["cohort"].strip().lower()),
                facility_npi=row["site_npi"],
                primary_cpt=panel[0][1],
                procedures=tuple((cpt, mod) for _, cpt, mod in panel),
                inpatient=False,
                encounter_class="visit",
                clinician_id=row["clinician_id"],
                window_start=service_date,
                window_end=service_date + timedelta(days=_WINDOW_DAYS),
            )
        )
    return tuple(out)


# ------------------------------------------------------------------------ markers


def _rows_by_key(table: Table, key: str) -> dict[str, list[int]]:
    out: dict[str, list[int]] = defaultdict(list)
    for i, row in enumerate(table.rows):
        out[row[key]].append(i)
    return out


def _dx_addressed_by_csn(extract: VisitExtract) -> dict[str, list[int]]:
    out: dict[str, list[int]] = defaultdict(list)
    for i, row in enumerate(extract.encounter_dx.rows):
        if row["addressed"].strip().upper() == "Y":
            out[row["csn"]].append(i)
    return out


def _structured(encounter: Encounter, marker_id: str, value: Decimal, ref: SourceRef) -> Marker:
    return Marker(
        encounter_id=encounter.encounter_id,
        marker_id=marker_id,
        value=value,
        provenance="structured",
        source_ref=ref,
        extractor_id=f"visit/{marker_id}",
        extractor_version=EXTRACTOR_VERSION,
    )


def _first_ref(
    table: Table,
    indices: list[int],
    column: str,
    fallback_table: Table,
    fallback_index: int,
    fallback_column: str,
) -> SourceRef:
    """Point at the first matching row, or, when nothing matched, at the
    visit's own row: still a traceable answer, the same convention
    ``markers._unmatched`` uses for a narrative rule that found nothing."""
    if indices:
        return table.ref(indices[0], column)
    return fallback_table.ref(fallback_index, fallback_column)


def _patient_context(
    extract: VisitExtract,
    enc: Encounter,
    problem_rows: list[int],
    rule: MarkerRule | None,
    pack: RulePack | None,
) -> Marker | None:
    """Decision 2, CONTRACT-PACKS.md: the pregnancy-risk tier when the
    problem list carries a ``PREGNANCY*`` flag, otherwise a shared
    decision-making note rule. Exactly one branch ever returns a marker."""
    pregnancy = [
        (i, parse_int(extract.problem_list.rows[i]["tier"], "problem_list.tier"))
        for i in problem_rows
        if extract.problem_list.rows[i]["flag"].strip().upper().startswith("PREGNANCY")
    ]
    if pregnancy:
        i, tier = max(pregnancy, key=lambda pair: pair[1])
        value = (Decimal(tier) / Decimal(3)).quantize(Decimal("0.0001"))
        return Marker(
            encounter_id=enc.encounter_id,
            marker_id="patient_context",
            value=value,
            provenance="structured",
            source_ref=extract.problem_list.ref(i, "tier"),
            extractor_id="visit/patient_context",
            extractor_version=EXTRACTOR_VERSION,
        )

    if rule is None or pack is None:
        return None
    notes = extract.notes_for(enc.encounter_id, rule.note_types)
    if not notes:
        return None

    hits = [
        (note, match)
        for note in notes
        for match in notes_mod.find(
            note, rule.patterns, allowed=rule.sections, excluded=rule.excluded_sections
        )
    ]
    if hits:
        note, match = hits[0]
        value = Decimal(1)
        ref = notes_mod.source_ref(note, match)
        evidence = match.evidence
    else:
        value = Decimal(0)
        ref = SourceRef(notes[0].filename, notes[0].sha256, 1, "no match")
        evidence = None
    return Marker(
        encounter_id=enc.encounter_id,
        marker_id="patient_context",
        value=value,
        provenance="rule",
        source_ref=ref,
        extractor_id="visit/patient_context",
        extractor_version=EXTRACTOR_VERSION,
        rule_pack_digest=pack.digest,
        evidence=evidence,
    )


def _markers(
    extract: VisitExtract, encounters: tuple[Encounter, ...], pack: RulePack | None
) -> dict[str, tuple[Marker, ...]]:
    visit_index = {row["visit_id"]: i for i, row in enumerate(extract.visit.rows)}
    dx_addressed = _dx_addressed_by_csn(extract)
    orders_by_visit = _rows_by_key(extract.orders, "visit_id")
    med_by_visit = _rows_by_key(extract.med_orders, "visit_id")
    window_by_visit = _rows_by_key(extract.window_encounters, "visit_id")
    problem_by_patient = _rows_by_key(extract.problem_list, "patient_id")

    narrative_rule: MarkerRule | None = None
    if pack is not None:
        narrative_rule = next(
            (m for m in pack.markers if m.marker_id == "patient_context" and m.reads_narrative),
            None,
        )

    out: dict[str, tuple[Marker, ...]] = {}
    for enc in encounters:
        idx = visit_index[enc.encounter_id]
        visit_row = extract.visit.rows[idx]
        dx_rows = dx_addressed.get(enc.csn, [])
        order_rows = orders_by_visit.get(enc.encounter_id, [])
        med_rows = med_by_visit.get(enc.encounter_id, [])
        window_rows = window_by_visit.get(enc.encounter_id, [])

        markers_list = [
            _structured(
                enc,
                "total_documented_minutes",
                parse_decimal(
                    visit_row["total_documented_minutes"], "visit.total_documented_minutes"
                ),
                extract.visit.ref(idx, "total_documented_minutes"),
            ),
            _structured(
                enc,
                "problems_addressed",
                Decimal(len(dx_rows)),
                _first_ref(extract.encounter_dx, dx_rows, "addressed", extract.visit, idx, "csn"),
            ),
            _structured(
                enc,
                "data_reviewed_ordered",
                Decimal(len(order_rows)),
                _first_ref(
                    extract.orders, order_rows, "order_type", extract.visit, idx, "visit_id"
                ),
            ),
            _structured(
                enc,
                "prescription_management",
                Decimal(
                    1
                    if any(
                        extract.med_orders.rows[i]["action"] in _PRESCRIPTION_ACTIONS
                        for i in med_rows
                    )
                    else 0
                ),
                _first_ref(extract.med_orders, med_rows, "action", extract.visit, idx, "visit_id"),
            ),
            _structured(
                enc,
                "coordination_minutes",
                sum(
                    (
                        parse_decimal(
                            extract.window_encounters.rows[i]["minutes"],
                            "window_encounters.minutes",
                        )
                        for i in window_rows
                    ),
                    start=Decimal(0),
                ),
                _first_ref(
                    extract.window_encounters,
                    window_rows,
                    "minutes",
                    extract.visit,
                    idx,
                    "visit_id",
                ),
            ),
        ]
        pc = _patient_context(
            extract, enc, problem_by_patient.get(enc.patient_id, []), narrative_rule, pack
        )
        if pc is not None:
            markers_list.append(pc)
        out[enc.encounter_id] = tuple(markers_list)
    return out


# -------------------------------------------------------------------------- facts


def _facts(extract: VisitExtract, encounter_id: str) -> Mapping[str, Decimal | str | bool]:
    """The flat mapping :func:`worth_complexity.method1.evaluate_work_rules`
    reads the pack's ``work_rules`` block against (CONTRACT-PACKS.md's
    Visit class section)."""
    visit_row = next(row for row in extract.visit.rows if row["visit_id"] == encounter_id)
    proc_rows = sorted(
        (row for row in extract.visit_proc.rows if row["visit_id"] == encounter_id),
        key=lambda row: parse_int(row["sequence"], "visit_proc.sequence"),
    )
    billed_em = proc_rows[0]["cpt"] if proc_rows else ""

    window_rows = [row for row in extract.window_encounters.rows if row["visit_id"] == encounter_id]
    portal_minutes_patient_initiated = sum(
        (
            parse_decimal(row["minutes"], "window_encounters.minutes")
            for row in window_rows
            if row["encounter_type"].strip().upper() == "PORTAL"
            and row["patient_initiated"].strip().upper() == "Y"
        ),
        start=Decimal(0),
    )
    coordination_minutes_clinician = sum(
        (
            parse_decimal(row["minutes"], "window_encounters.minutes")
            for row in window_rows
            if row["encounter_type"].strip().upper() in _CLINICIAN_COORDINATION_TYPES
            and row["patient_initiated"].strip().upper() != "Y"
        ),
        start=Decimal(0),
    )
    non_vehicle_minutes = sum(
        (
            parse_decimal(row["minutes"], "window_encounters.minutes")
            for row in window_rows
            if row["encounter_type"].strip().upper() in _NON_VEHICLE_TYPES
        ),
        start=Decimal(0),
    )

    problem_rows = [
        row for row in extract.problem_list.rows if row["patient_id"] == visit_row["patient_id"]
    ]
    chronic_condition_count = sum(
        1
        for row in problem_rows
        if not row["flag"].strip().upper().startswith("PREGNANCY")
        and parse_int(row["tier"], "problem_list.tier") >= 1
    )
    is_pregnancy = any(row["flag"].strip().upper().startswith("PREGNANCY") for row in problem_rows)

    return {
        "total_documented_minutes": parse_decimal(
            visit_row["total_documented_minutes"], "visit.total_documented_minutes"
        ),
        "billed_em": billed_em,
        "portal_minutes_patient_initiated": portal_minutes_patient_initiated,
        "coordination_minutes_clinician": coordination_minutes_clinician,
        "non_vehicle_minutes": non_vehicle_minutes,
        "chronic_condition_count": Decimal(chronic_condition_count),
        "service_line": visit_row["service_line"].upper(),
        "is_pregnancy": is_pregnancy,
    }
