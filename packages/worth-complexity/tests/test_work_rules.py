"""``WorkRule`` and ``evaluate_work_rules``: Method 1 for the visit and
episode classes, decision 4 (CONTRACT-PACKS.md) — threshold rules over
structured facts, evaluated by the same machinery that produces a
``Method1Flag`` for the surgical class's note-phrase cross-check.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from worth_complexity.method1 import Method1Error, WorkRule, evaluate_work_rules
from worth_complexity.models import Cohort, Encounter

FACTS: dict[str, Decimal | str | bool] = {
    "total_documented_minutes": Decimal("42"),
    "coordination_minutes_clinician": Decimal("18"),
    "chronic_condition_count": Decimal("1"),
    "is_pregnancy": True,
    "reading_days": Decimal("10"),
}


def an_encounter(
    encounter_id: str = "V1",
    primary_cpt: str = "99214",
    procedures: tuple[tuple[str, str], ...] = (("99214", ""),),
) -> Encounter:
    return Encounter(
        encounter_id=encounter_id,
        csn="C1",
        patient_id="P1",
        account_id="A1",
        service_date=date(2026, 1, 1),
        service_line="MENOPAUSE",
        specialty="Obstetrics & Gynecology",
        cohort=Cohort.STUDY,
        facility_npi="1234567893",
        primary_cpt=primary_cpt,
        procedures=procedures,
        inpatient=False,
        encounter_class="visit",
    )


def a_rule(**overrides: object) -> WorkRule:
    base: dict[str, object] = {
        "rule_id": "em-level-by-time",
        "when": {"fact": "total_documented_minutes", "min": "40"},
        "billed_any_of": ("99214",),
        "candidate_codes": ("99215",),
        "replaces": "99214",
        "bucket_if_absent": "missed",
        "bucket_if_present": None,
        "statement": "Total documented time {total_documented_minutes} min supports the "
        "next E/M level by time",
        "section": "time attestation",
        "evidence_strength": "strong",
        "note": "the note's own reasoning",
    }
    base.update(overrides)
    return WorkRule(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# when: min / max / equals / flag
# ---------------------------------------------------------------------------


def test_min_fires_when_the_fact_meets_the_threshold() -> None:
    rule = a_rule()
    flags = evaluate_work_rules(an_encounter(), FACTS, None, (rule,))
    assert len(flags) == 1
    assert flags[0].bucket == "missed"
    assert flags[0].rule_id == "em-level-by-time"


def test_min_does_not_fire_below_the_threshold() -> None:
    rule = a_rule(when={"fact": "total_documented_minutes", "min": "50"})
    assert evaluate_work_rules(an_encounter(), FACTS, None, (rule,)) == ()


def test_max_fires_when_the_fact_is_at_or_below_the_threshold() -> None:
    rule = a_rule(
        rule_id="light-visit",
        when={"fact": "total_documented_minutes", "max": "45"},
        billed_any_of=(),
        candidate_codes=(),
        replaces=None,
        bucket_if_absent="no_code",
    )
    flags = evaluate_work_rules(an_encounter(), FACTS, None, (rule,))
    assert len(flags) == 1
    assert flags[0].bucket == "no_code"
    assert flags[0].vehicle_exists is False


def test_max_does_not_fire_above_the_threshold() -> None:
    rule = a_rule(
        when={"fact": "total_documented_minutes", "max": "10"},
        billed_any_of=(),
    )
    assert evaluate_work_rules(an_encounter(), FACTS, None, (rule,)) == ()


def test_equals_matches_a_string_fact() -> None:
    rule = a_rule(
        rule_id="chronic-count-equals",
        when={"fact": "chronic_condition_count", "equals": "1"},
        billed_any_of=(),
    )
    flags = evaluate_work_rules(an_encounter(), FACTS, None, (rule,))
    assert len(flags) == 1


def test_equals_refuses_a_mismatch() -> None:
    rule = a_rule(when={"fact": "chronic_condition_count", "equals": "2"}, billed_any_of=())
    assert evaluate_work_rules(an_encounter(), FACTS, None, (rule,)) == ()


def test_flag_checks_truthy() -> None:
    rule = a_rule(
        rule_id="prenatal-em-basis",
        when={"fact": "is_pregnancy", "flag": True},
        billed_any_of=(),
        candidate_codes=(),
        replaces=None,
        bucket_if_absent="mismatched",
    )
    flags = evaluate_work_rules(an_encounter(), FACTS, None, (rule,))
    assert len(flags) == 1
    assert flags[0].bucket == "mismatched"
    assert flags[0].candidate_codes == ()


def test_flag_false_checks_falsy() -> None:
    rule = a_rule(
        when={"fact": "is_pregnancy", "flag": False},
        billed_any_of=(),
    )
    assert evaluate_work_rules(an_encounter(), FACTS, None, (rule,)) == ()


def test_a_fact_the_extract_did_not_supply_reads_as_not_met() -> None:
    """The fact mapping is deliberately partial across classes; a pack
    naming a fact this extract omits should not crash the run."""
    rule = a_rule(when={"fact": "reading_days", "min": "1"}, billed_any_of=())
    assert evaluate_work_rules(an_encounter(), {}, None, (rule,)) == ()


# ---------------------------------------------------------------------------
# when: all_of / any_of
# ---------------------------------------------------------------------------


def test_all_of_requires_every_clause() -> None:
    rule = a_rule(
        rule_id="care-management-coordination",
        when={
            "all_of": [
                {"fact": "coordination_minutes_clinician", "min": "20"},
                {"fact": "chronic_condition_count", "min": "2"},
            ]
        },
        billed_any_of=(),
        candidate_codes=("99490",),
        replaces=None,
        bucket_if_absent="missed",
    )
    # coordination_minutes_clinician is 18 < 20: fails the first clause
    assert evaluate_work_rules(an_encounter(), FACTS, None, (rule,)) == ()


def test_all_of_fires_when_every_clause_holds() -> None:
    facts = {
        **FACTS,
        "coordination_minutes_clinician": Decimal("25"),
        "chronic_condition_count": Decimal("3"),
    }
    rule = a_rule(
        rule_id="care-management-coordination",
        when={
            "all_of": [
                {"fact": "coordination_minutes_clinician", "min": "20"},
                {"fact": "chronic_condition_count", "min": "2"},
            ]
        },
        billed_any_of=(),
        candidate_codes=("99490",),
        replaces=None,
        bucket_if_absent="missed",
    )
    flags = evaluate_work_rules(an_encounter(), facts, None, (rule,))
    assert len(flags) == 1
    assert flags[0].bucket == "missed"


def test_any_of_fires_when_one_clause_holds() -> None:
    rule = a_rule(
        rule_id="either-threshold",
        when={
            "any_of": [
                {"fact": "total_documented_minutes", "min": "1000"},
                {"fact": "coordination_minutes_clinician", "min": "10"},
            ]
        },
        billed_any_of=(),
    )
    flags = evaluate_work_rules(an_encounter(), FACTS, None, (rule,))
    assert len(flags) == 1


def test_any_of_does_not_fire_when_no_clause_holds() -> None:
    rule = a_rule(
        when={
            "any_of": [
                {"fact": "total_documented_minutes", "min": "1000"},
                {"fact": "coordination_minutes_clinician", "min": "1000"},
            ]
        },
        billed_any_of=(),
    )
    assert evaluate_work_rules(an_encounter(), FACTS, None, (rule,)) == ()


# ---------------------------------------------------------------------------
# billed_any_of, bucket_if_present / bucket_if_absent, candidate presence
# ---------------------------------------------------------------------------


def test_billed_any_of_gates_the_rule() -> None:
    rule = a_rule(billed_any_of=("99213",))  # encounter bills 99214, not 99213
    assert evaluate_work_rules(an_encounter(), FACTS, None, (rule,)) == ()


def test_candidate_already_submitted_produces_no_flag_by_default() -> None:
    rule = a_rule(candidate_codes=("99214",))  # 99214 is already on the claim
    assert evaluate_work_rules(an_encounter(), FACTS, None, (rule,)) == ()


def test_candidate_already_submitted_uses_bucket_if_present_when_named() -> None:
    rule = a_rule(candidate_codes=("99214",), bucket_if_present="mismatched")
    flags = evaluate_work_rules(an_encounter(), FACTS, None, (rule,))
    assert len(flags) == 1
    assert flags[0].bucket == "mismatched"


def test_empty_candidate_codes_is_a_no_code_style_flag_with_no_vehicle() -> None:
    rule = a_rule(
        rule_id="non-vehicle-coordination",
        when={"fact": "coordination_minutes_clinician", "min": "5"},
        billed_any_of=(),
        candidate_codes=(),
        replaces=None,
        bucket_if_absent="no_code",
    )
    flags = evaluate_work_rules(an_encounter(), FACTS, None, (rule,))
    assert len(flags) == 1
    assert flags[0].bucket == "no_code"
    assert flags[0].candidate_codes == ()
    assert flags[0].vehicle_exists is False


# ---------------------------------------------------------------------------
# statement formatting, replaces, claim fallback to encounter.procedures
# ---------------------------------------------------------------------------


def test_statement_is_formatted_from_facts() -> None:
    rule = a_rule()
    flags = evaluate_work_rules(an_encounter(), FACTS, None, (rule,))
    assert flags[0].statement == (
        "Total documented time 42 min supports the next E/M level by time"
    )


def test_statement_referencing_an_unknown_fact_raises() -> None:
    rule = a_rule(statement="needs {no_such_fact}")
    with pytest.raises(Method1Error, match="cannot be formatted"):
        evaluate_work_rules(an_encounter(), FACTS, None, (rule,))


def test_replaces_is_carried_onto_the_flag() -> None:
    rule = a_rule()
    flags = evaluate_work_rules(an_encounter(), FACTS, None, (rule,))
    assert flags[0].replaces == "99214"
    assert flags[0].candidate_codes == ("99215",)


def test_replaces_defaults_to_none() -> None:
    rule = a_rule(replaces=None)
    flags = evaluate_work_rules(an_encounter(), FACTS, None, (rule,))
    assert flags[0].replaces is None


def test_falls_back_to_encounter_procedures_when_no_claim_is_linked() -> None:
    """The same convention ``cross_check`` uses: no linked claim means the
    encounter's own procedure panel stands in for 'submitted'."""
    enc = an_encounter(procedures=(("99215", ""),))
    rule = a_rule(billed_any_of=("99215",), candidate_codes=("99417",), replaces="99215")
    flags = evaluate_work_rules(enc, FACTS, None, (rule,))
    assert len(flags) == 1
    assert flags[0].submitted_codes == ("99215",)


def test_evidence_strength_and_section_come_from_the_rule() -> None:
    rule = a_rule(evidence_strength="weak", section="portal messaging")
    flags = evaluate_work_rules(an_encounter(), FACTS, None, (rule,))
    assert flags[0].evidence_strength == "weak"
    assert flags[0].section == "portal messaging"
