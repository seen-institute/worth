"""The visit class (CONTRACT-PACKS.md): ``visits.py``'s reader, its five
structured markers, ``patient_context``'s decision 2 dispatch, and every
``visit-em-v1`` work rule in every Method 1 bucket.

Six hand-built visits, written to a temporary directory as the eight
pipe-delimited tables plus a note apiece, cover: a menopause visit whose note
documents shared decision-making (the narrative branch of ``patient_context``
firing), a menopause visit whose note carries only the negated decoy (the
narrative branch firing at 0, not the decoy), a high-risk maternity visit
(the structured pregnancy-tier branch, which must win over a note that also
carries the phrase, proving the "exactly one" dispatch), a second maternity
visit billed at the lower E/M level, and two chronic-management comparator
visits that between them exercise every work rule and land a flag in each of
``missed``, ``mismatched`` and ``no_code``.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from worth_complexity import method1, rulepack
from worth_complexity.extracts import read_extract
from worth_complexity.models import Cohort, Encounter, SourceRef
from worth_complexity.visits import VisitExtract, read_visit_extract

PACK = rulepack.load("visit-em-v1")

_SHARED_DECISION_NOTE = """MOUNT SINAI HEALTH SYSTEM
OFFICE VISIT NOTE

SUBJECTIVE:
Patient presents for a scheduled visit. No shared decision conversation \
occurred at today's visit.

OBJECTIVE:
Vital signs reviewed and within normal range.

ASSESSMENT:
N95.1 addressed today.

COUNSELING:
Options reviewed with the patient regarding hormone therapy; patient elected \
to proceed.

PLAN:
Continue current management.

TIME: 32 minutes total documented for this encounter.
"""

_NO_PHRASE_NOTE = """MOUNT SINAI HEALTH SYSTEM
OFFICE VISIT NOTE

SUBJECTIVE:
Patient presents for a scheduled visit. No shared decision conversation \
occurred at today's visit; that discussion is deferred.

OBJECTIVE:
Vital signs reviewed and within normal range.

ASSESSMENT:
N95.0 addressed today.

COUNSELING:
Routine anticipatory guidance was provided; no decision point arose today.

PLAN:
Continue current management.

TIME: 28 minutes total documented for this encounter.
"""

_MATERNITY_NOTE_WITH_DECOY_PHRASE = """MOUNT SINAI HEALTH SYSTEM
OFFICE VISIT NOTE

SUBJECTIVE:
Patient presents for a scheduled prenatal visit.

OBJECTIVE:
Vital signs reviewed and within normal range.

ASSESSMENT:
O09.90 addressed today.

COUNSELING:
Options reviewed with the patient regarding delivery planning; patient \
elected a follow-up plan. This sentence must never be read: the structured \
pregnancy flag wins.

PLAN:
Continue current management.

TIME: 45 minutes total documented for this encounter.
"""

_PLAIN_NOTE = """MOUNT SINAI HEALTH SYSTEM
OFFICE VISIT NOTE

SUBJECTIVE:
Patient presents for a scheduled visit.

OBJECTIVE:
Vital signs reviewed and within normal range.

ASSESSMENT:
E11.9 addressed today.

COUNSELING:
Routine anticipatory guidance was provided.

PLAN:
Continue current management.

TIME: minutes as documented.
"""


def _stamp(d: date, hour: int = 10, minute: int = 0) -> str:
    return f"{d.strftime('%m/%d/%Y')} {hour:02d}:{minute:02d}:00"


def _write(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "notes").mkdir(parents=True, exist_ok=True)
    day = date(2026, 3, 2)

    visit = [
        "visit_id|csn|patient_id|billing_account_id|service_date|service_line|specialty|"
        "cohort|site_npi|clinician_id|total_documented_minutes|time_attested",
        f"V1|C1|P1|A1|{_stamp(day)}|MENOPAUSE|Obstetrics & Gynecology|study|1447890215|CLIN1|32|Y",
        f"V2|C2|P2|A2|{_stamp(day)}|MENOPAUSE|Obstetrics & Gynecology|study|1447890215|CLIN1|28|Y",
        f"V3|C3|P3|A3|{_stamp(day)}|MATERNITY|Obstetrics & Gynecology|study|1447890215|CLIN2|45|Y",
        f"V4|C4|P4|A4|{_stamp(day)}|MATERNITY|Obstetrics & Gynecology|study|1447890215|CLIN2|33|Y",
        f"V5|C5|P5|A5|{_stamp(day)}|ENDOCRINOLOGY|Endocrinology|comparator|1447890215|CLIN3|60|Y",
        f"V6|C6|P6|A6|{_stamp(day)}|CARDIOLOGY|Cardiology|comparator|1447890215|CLIN4|30|Y",
    ]
    (directory / "visit.txt").write_text("\n".join(visit) + "\n", encoding="utf-8")

    visit_proc = [
        "visit_id|cpt|modifier|sequence",
        "V1|99214||1",
        "V2|99213||1",
        "V3|99214||1",
        "V4|99213||1",
        "V5|99215||1",
        "V6|99214||1",
    ]
    (directory / "visit_proc.txt").write_text("\n".join(visit_proc) + "\n", encoding="utf-8")

    encounter_dx = [
        "csn|icd10|sequence|addressed",
        "C1|N95.1|1|Y",
        "C1|E66.01|2|Y",
        "C2|N95.0|1|Y",
        "C3|O09.90|1|Y",
        "C3|Z34.90|2|Y",
        "C3|O26.849|3|N",
        "C4|O09.90|1|Y",
        "C5|E11.9|1|Y",
        "C5|E03.9|2|Y",
        "C5|I10|3|Y",
        "C6|I25.10|1|Y",
    ]
    (directory / "encounter_dx.txt").write_text("\n".join(encounter_dx) + "\n", encoding="utf-8")

    orders = [
        "visit_id|order_type|action|order_dttm",
        f"V1|LAB|ORDERED|{_stamp(day)}",
        f"V3|FETAL_SURVEILLANCE|REVIEWED|{_stamp(day)}",
        f"V3|LAB|ORDERED|{_stamp(day)}",
        f"V5|LAB|ORDERED|{_stamp(day)}",
        f"V5|IMAGING|REVIEWED|{_stamp(day)}",
    ]
    (directory / "orders.txt").write_text("\n".join(orders) + "\n", encoding="utf-8")

    med_orders = [
        "visit_id|rxnorm|action",
        "V1|12345|START",
        "V2|12345|CONTINUE",
        "V5|22222|ADJUST",
    ]
    (directory / "med_orders.txt").write_text("\n".join(med_orders) + "\n", encoding="utf-8")

    def contact(offset: int) -> str:
        return _stamp(day + timedelta(days=offset), 9, 30)

    window_encounters = [
        "visit_id|encounter_type|contact_dttm|minutes|patient_initiated|clinician_id",
        f"V1|PORTAL|{contact(3)}|9|Y|CLIN1",
        f"V5|PORTAL|{contact(2)}|15|N|CLIN3",
        f"V5|TELEPHONE|{contact(5)}|10|N|CLIN3",
        f"V5|PORTAL|{contact(1)}|8|Y|CLIN3",
        f"V5|LAB_REVIEW|{contact(4)}|6|N|CLIN3",
        f"V6|PORTAL|{contact(2)}|15|N|CLIN4",
        f"V6|TELEPHONE|{contact(6)}|10|N|CLIN4",
    ]
    (directory / "window_encounters.txt").write_text(
        "\n".join(window_encounters) + "\n", encoding="utf-8"
    )

    problem_list = [
        "patient_id|flag|tier",
        "P3|PREGNANCY_HIGH_RISK|3",
        "P4|PREGNANCY|1",
        "P5|T2DM|2",
        "P5|CHRONIC_HTN|1",
        "P6|CHRONIC_HTN|1",
    ]
    (directory / "problem_list.txt").write_text("\n".join(problem_list) + "\n", encoding="utf-8")

    patient_lds = ["pat_id|birth_date|sex|zip5|county"]
    for pid in ("P1", "P2", "P3", "P4", "P5", "P6"):
        patient_lds.append(f"{pid}|06/1985|F|10029|New York")
    (directory / "patient_lds.txt").write_text("\n".join(patient_lds) + "\n", encoding="utf-8")

    notes_index = ["note_id|log_id|csn|note_type|service_date|author_role|filename"]
    note_bodies = {
        "V1": _SHARED_DECISION_NOTE,
        "V2": _NO_PHRASE_NOTE,
        "V3": _MATERNITY_NOTE_WITH_DECOY_PHRASE,
        "V4": _PLAIN_NOTE,
        "V5": _PLAIN_NOTE,
        "V6": _PLAIN_NOTE,
    }
    for visit_id, body in note_bodies.items():
        filename = f"N{visit_id}.txt"
        (directory / "notes" / filename).write_text(body, encoding="utf-8")
        notes_index.append(
            f"N{visit_id}|{visit_id}|C{visit_id[1:]}|visit|{_stamp(day)}|"
            f"Attending Physician|{filename}"
        )
    (directory / "notes.txt").write_text("\n".join(notes_index) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def extract(tmp_path_factory: pytest.TempPathFactory) -> VisitExtract:
    directory = tmp_path_factory.mktemp("visit_extract") / "clinical"
    _write(directory)
    return read_visit_extract(directory)


@pytest.fixture(scope="module")
def encounters(extract: VisitExtract) -> tuple[Encounter, ...]:
    return extract.encounters()


def _by_id(encounters: tuple[Encounter, ...], encounter_id: str) -> Encounter:
    return next(e for e in encounters if e.encounter_id == encounter_id)


# --------------------------------------------------------------------- reader


def test_visit_txt_dispatches_to_the_visit_reader(extract: VisitExtract) -> None:
    directory = extract.visit.name  # sanity: just confirms the fixture built
    assert directory == "visit.txt"
    assert extract.encounter_class == "visit"


def test_read_extract_dispatches_the_same_way(tmp_path: Path) -> None:
    _write(tmp_path)
    extract = read_extract(tmp_path)
    assert extract.encounter_class == "visit"
    assert isinstance(extract, VisitExtract)


def test_file_hashes_cover_every_table_and_note(extract: VisitExtract) -> None:
    hashes = extract.file_hashes()
    names = {name for name, _ in hashes}
    assert names == {t.name for t in extract.tables} | {n.filename for n in extract.notes}
    assert all(len(sha) == 64 for _, sha in hashes)
    assert len(hashes) == len(set(hashes))


def test_encounters_carry_the_window_and_the_primary_em_code(
    encounters: tuple[Encounter, ...],
) -> None:
    v1 = _by_id(encounters, "V1")
    assert v1.primary_cpt == "99214"
    assert v1.service_line == "MENOPAUSE"
    assert v1.cohort is Cohort.STUDY
    assert v1.inpatient is False
    assert v1.clinician_id == "CLIN1"
    assert v1.window_start == v1.service_date
    assert v1.window_end == v1.service_date + timedelta(days=30)

    v5 = _by_id(encounters, "V5")
    assert v5.cohort is Cohort.COMPARATOR
    assert v5.primary_cpt == "99215"


# -------------------------------------------------------------------- markers


def test_total_documented_minutes_marker(
    extract: VisitExtract, encounters: tuple[Encounter, ...]
) -> None:
    markers = extract.markers(encounters, PACK)
    m = next(x for x in markers["V1"] if x.marker_id == "total_documented_minutes")
    assert m.value == Decimal("32")
    assert m.provenance == "structured"
    assert m.source_ref.column == "total_documented_minutes"


def test_problems_addressed_counts_only_addressed_rows(
    extract: VisitExtract, encounters: tuple[Encounter, ...]
) -> None:
    markers = extract.markers(encounters, PACK)
    v1 = next(x for x in markers["V1"] if x.marker_id == "problems_addressed")
    assert v1.value == Decimal("2")
    # V3 has three dx rows, only two addressed=Y
    v3 = next(x for x in markers["V3"] if x.marker_id == "problems_addressed")
    assert v3.value == Decimal("2")


def test_data_reviewed_ordered_counts_orders_rows(
    extract: VisitExtract, encounters: tuple[Encounter, ...]
) -> None:
    markers = extract.markers(encounters, PACK)
    v3 = next(x for x in markers["V3"] if x.marker_id == "data_reviewed_ordered")
    assert v3.value == Decimal("2")
    v2 = next(x for x in markers["V2"] if x.marker_id == "data_reviewed_ordered")
    assert v2.value == Decimal("0")


def test_prescription_management_ignores_continuation_alone(
    extract: VisitExtract, encounters: tuple[Encounter, ...]
) -> None:
    markers = extract.markers(encounters, PACK)
    v1 = next(x for x in markers["V1"] if x.marker_id == "prescription_management")
    assert v1.value == Decimal("1")  # START
    v2 = next(x for x in markers["V2"] if x.marker_id == "prescription_management")
    assert v2.value == Decimal("0")  # CONTINUE only
    v4 = next(x for x in markers["V4"] if x.marker_id == "prescription_management")
    assert v4.value == Decimal("0")  # no med_orders rows at all


def test_coordination_minutes_sums_every_window_row(
    extract: VisitExtract, encounters: tuple[Encounter, ...]
) -> None:
    markers = extract.markers(encounters, PACK)
    v5 = next(x for x in markers["V5"] if x.marker_id == "coordination_minutes")
    assert v5.value == Decimal("39")  # PORTAL(15) + TELEPHONE(10) + PORTAL(8) + LAB_REVIEW(6)
    v1 = next(x for x in markers["V1"] if x.marker_id == "coordination_minutes")
    assert v1.value == Decimal("9")


# ------------------------------------------- patient_context, decision 2


def test_patient_context_reads_the_shared_decision_phrase(
    extract: VisitExtract, encounters: tuple[Encounter, ...]
) -> None:
    markers = extract.markers(encounters, PACK)
    v1 = next(x for x in markers["V1"] if x.marker_id == "patient_context")
    assert v1.value == Decimal("1")
    assert v1.provenance == "rule"
    assert v1.evidence is not None


def test_patient_context_is_zero_when_only_the_negated_decoy_is_present(
    extract: VisitExtract, encounters: tuple[Encounter, ...]
) -> None:
    """The negated "no shared decision conversation" sentence in SUBJECTIVE
    must never count, both because it is negated and because SUBJECTIVE is
    not in the rule's allowed sections (COUNSELING, PLAN)."""
    markers = extract.markers(encounters, PACK)
    v2 = next(x for x in markers["V2"] if x.marker_id == "patient_context")
    assert v2.value == Decimal("0")
    assert v2.provenance == "rule"


def test_patient_context_prefers_the_pregnancy_tier_over_a_note_that_also_matches(
    extract: VisitExtract, encounters: tuple[Encounter, ...]
) -> None:
    """V3 is PREGNANCY_HIGH_RISK tier 3 *and* its note carries a shared
    decision phrase in COUNSELING. Decision 2: the structured branch wins
    outright, the note is never even consulted."""
    markers = extract.markers(encounters, PACK)
    v3 = next(x for x in markers["V3"] if x.marker_id == "patient_context")
    assert v3.value == (Decimal(3) / Decimal(3)).quantize(Decimal("0.0001"))
    assert v3.provenance == "structured"


def test_patient_context_pregnancy_tier_one(
    extract: VisitExtract, encounters: tuple[Encounter, ...]
) -> None:
    markers = extract.markers(encounters, PACK)
    v4 = next(x for x in markers["V4"] if x.marker_id == "patient_context")
    assert v4.value == (Decimal(1) / Decimal(3)).quantize(Decimal("0.0001"))
    assert v4.provenance == "structured"


def test_patient_context_omitted_without_a_pack(
    extract: VisitExtract, encounters: tuple[Encounter, ...]
) -> None:
    """No pack, no notes consulted: a comparator with no pregnancy flag and
    no narrative rule to fall back on gets no patient_context marker at all."""
    markers = extract.markers(encounters, pack=None)
    ids = {m.marker_id for m in markers["V6"]}
    assert "patient_context" not in ids
    assert len(markers["V6"]) == 5


# ----------------------------------------------------------------------- facts


def test_facts_for_the_comparator_with_qualifying_coordination(
    extract: VisitExtract,
) -> None:
    facts = extract.facts("V5")
    assert facts["total_documented_minutes"] == Decimal("60")
    assert facts["billed_em"] == "99215"
    assert facts["portal_minutes_patient_initiated"] == Decimal("8")
    assert facts["coordination_minutes_clinician"] == Decimal("25")  # 15 + 10
    assert facts["non_vehicle_minutes"] == Decimal("6")
    assert facts["chronic_condition_count"] == Decimal("2")
    assert facts["service_line"] == "ENDOCRINOLOGY"
    assert facts["is_pregnancy"] is False


def test_facts_chronic_condition_count_excludes_pregnancy_flags(
    extract: VisitExtract,
) -> None:
    facts = extract.facts("V3")
    assert facts["is_pregnancy"] is True
    assert facts["chronic_condition_count"] == Decimal("0")


def test_facts_for_the_ineligible_comparator(extract: VisitExtract) -> None:
    facts = extract.facts("V6")
    assert facts["coordination_minutes_clinician"] == Decimal("25")
    assert facts["chronic_condition_count"] == Decimal("1")


# ------------------------------------------------------- work rules, all buckets


def test_every_work_rule_fires_in_the_right_bucket(
    extract: VisitExtract, encounters: tuple[Encounter, ...]
) -> None:
    flags_by_encounter = {
        enc.encounter_id: method1.evaluate_work_rules(
            enc, extract.facts(enc.encounter_id), None, PACK.work_rules
        )
        for enc in encounters
    }

    def rule_ids(encounter_id: str) -> dict[str, method1.Method1Flag]:
        return {f.rule_id: f for f in flags_by_encounter[encounter_id]}

    # em-level-by-time: V4 (99213, 33 min >= 30) and V1 (99214, 32 min < 40, no flag)
    v4 = rule_ids("V4")
    assert v4["em-level-by-time-99213"].bucket == "missed"
    assert v4["em-level-by-time-99213"].candidate_codes == ("99214",)
    assert v4["em-level-by-time-99213"].replaces == "99213"
    assert "em-level-by-time-99214" not in rule_ids("V1")

    # V3: 99214 at 45 min >= 40 -> missed, plus pregnancy mismatch
    v3 = rule_ids("V3")
    assert v3["em-level-by-time-99214"].bucket == "missed"
    assert v3["em-level-by-time-99214"].candidate_codes == ("99215",)
    assert v3["em-level-by-time-99214"].replaces == "99214"
    assert v3["prenatal-em-basis"].bucket == "mismatched"
    assert v3["prenatal-em-basis"].candidate_codes == ()
    assert v3["prenatal-em-basis"].vehicle_exists is False

    # V5: 99215 at 60 min >= 55 -> prolonged service missed
    v5 = rule_ids("V5")
    assert v5["prolonged-service"].bucket == "missed"
    assert v5["prolonged-service"].candidate_codes == ("G2212",)

    # V5: 8 portal patient-initiated minutes -> the 5-10 digital E/M tier
    assert v5["online-digital-em-99421"].bucket == "missed"
    assert v5["online-digital-em-99421"].candidate_codes == ("99421",)

    # V5: 25 clinician-coordination minutes, chronic_condition_count 2 -> eligible
    assert v5["care-management-coordination"].bucket == "missed"
    assert v5["care-management-coordination"].candidate_codes == ("99490",)

    # V5: 6 non-vehicle minutes (LAB_REVIEW) -> no_code
    assert v5["non-vehicle-coordination"].bucket == "no_code"
    assert v5["non-vehicle-coordination"].candidate_codes == ()
    assert v5["non-vehicle-coordination"].vehicle_exists is False

    # V6: same 25 coordination minutes, but only 1 chronic condition -> ineligible
    v6 = rule_ids("V6")
    assert v6["care-management-ineligible"].bucket == "no_code"
    assert v6["care-management-ineligible"].candidate_codes == ()
    assert "care-management-coordination" not in v6


def test_all_three_method1_buckets_are_exercised(
    extract: VisitExtract, encounters: tuple[Encounter, ...]
) -> None:
    all_flags = [
        f
        for enc in encounters
        for f in method1.evaluate_work_rules(
            enc, extract.facts(enc.encounter_id), None, PACK.work_rules
        )
    ]
    buckets = {f.bucket for f in all_flags}
    assert buckets == {"missed", "mismatched", "no_code"}


def test_the_replaces_field_prices_the_level_step_not_the_candidate_outright() -> None:
    """decision 4, CONTRACT-PACKS.md: a work-rule flag naming ``replaces``
    prices ``(PFS(candidate) - PFS(billed)) x multiplier``. Mirrors
    ``test_pipeline.py``'s equivalent surgical-pack check, using the visit
    pack's own ``em-level-by-time-99214`` rule and real NY01 fee amounts."""
    from worth_complexity.adequacy import Multiplier, fixture_schedules
    from worth_complexity.pipeline import _price_method1_flag
    from worth_fees import PlaceOfService
    from worth_fees.sources import vintage_for

    rule = next(r for r in PACK.work_rules if r.rule_id == "em-level-by-time-99214")
    flag = method1.Method1Flag(
        encounter_id="X",
        statement="test",
        section=rule.section,
        rule_id=rule.rule_id,
        candidate_codes=rule.candidate_codes,
        submitted_codes=("99214",),
        bucket="missed",
        reason="test",
        vehicle_exists=True,
        evidence_strength="strong",
        priced=None,
        pricing_trace=(),
        source_ref=SourceRef("facts", "", 0, "total_documented_minutes"),
        replaces=rule.replaces,
    )
    multiplier = Multiplier(
        payer_id="p", payer_label="Payer A", n=20,
        value=Decimal("2"), q1=Decimal("1"), q3=Decimal("3"), trace=(),
    )  # fmt: skip
    reference = vintage_for(2026, 4)
    priced = _price_method1_flag(
        flag,
        multiplier,
        locality="NY01",
        setting=PlaceOfService.NON_FACILITY,
        reference=reference,
        pool=fixture_schedules,
    )
    assert priced.priced is not None
    assert priced.priced > 0
    assert "candidate 99215 priced" in priced.pricing_trace[0]
    assert "replaces 99214 priced" in priced.pricing_trace[0]
