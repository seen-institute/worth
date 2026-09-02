"""The Layer A scorer: pure, deterministic, and a filter over provenance."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from worth_complexity import (
    LAYER_A,
    LAYER_A_STRUCTURED_ONLY,
    ComplexityScore,
    MissingMarkerError,
    score,
)
from worth_complexity.models import Cohort, Encounter, Marker, SourceRef
from worth_complexity.rulepack import load

PACK = load("gyn-surgical-v1")
REF = SourceRef("or_log.txt", "0" * 64, 2, "c")

ENCOUNTER = Encounter(
    "88214417",
    "1032884471",
    "Z0004412",
    "A70041188",
    date(2025, 3, 14),
    "GYN",
    "Obstetrics & Gynecology",
    Cohort.STUDY,
    "1234567893",
    "58662",
    (("58662", "22"), ("58660", "")),
    True,
)


def markers(**values: str) -> tuple[Marker, ...]:
    return tuple(
        Marker(ENCOUNTER.encounter_id, k, Decimal(v), "structured", REF, "t", "1")
        for k, v in values.items()
    )


FLOOR = markers(
    operative_minutes="30",
    asa_class="1",
    distinct_surgical_specialties="1",
    estimated_blood_loss_ml="0",
    procedure_count="1",
    comorbidity_count="0",
    assistant_surgeon_count="0",
    inpatient="0",
)
CEILING = markers(
    operative_minutes="240",
    asa_class="4",
    distinct_surgical_specialties="3",
    estimated_blood_loss_ml="600",
    procedure_count="3",
    comorbidity_count="6",
    assistant_surgeon_count="2",
    inpatient="1",
)


def test_the_floor_of_every_anchor_scores_zero() -> None:
    assert score(ENCOUNTER, FLOOR, PACK, LAYER_A_STRUCTURED_ONLY).score == ComplexityScore(0)


def test_the_ceiling_of_every_anchor_scores_one_hundred() -> None:
    assert score(ENCOUNTER, CEILING, PACK, LAYER_A_STRUCTURED_ONLY).score == ComplexityScore(100)


def test_a_worked_case_scores_by_hand() -> None:
    """The methodology's worked example: a five-hour deep-infiltrating excision
    with a colorectal team in the room.

    0.30*(298-30)/210 -> clamped to 1.0   = 30.00
    0.15*(3-1)/3                          =  6.66...  wait: asa 3 -> (3-1)/(4-1)=0.6667
    """
    m = markers(
        operative_minutes="298",
        asa_class="3",
        distinct_surgical_specialties="3",
        estimated_blood_loss_ml="450",
        procedure_count="2",
        comorbidity_count="5",
        assistant_surgeon_count="2",
        inpatient="1",
    )
    expected = (
        Decimal("0.30") * 1
        + Decimal("0.15") * (Decimal(2) / Decimal(3))
        + Decimal("0.15") * 1
        + Decimal("0.10") * (Decimal(450) / Decimal(600))
        + Decimal("0.10") * (Decimal(1) / Decimal(2))
        + Decimal("0.10") * (Decimal(5) / Decimal(6))
        + Decimal("0.05") * 1
        + Decimal("0.05") * 1
    ) * 100
    assert score(ENCOUNTER, m, PACK, LAYER_A_STRUCTURED_ONLY).score.value == int(
        expected.quantize(Decimal("1"))
    )


def test_scoring_is_deterministic() -> None:
    a = score(ENCOUNTER, CEILING, PACK, LAYER_A_STRUCTURED_ONLY)
    b = score(ENCOUNTER, CEILING, PACK, LAYER_A_STRUCTURED_ONLY)
    assert a.score == b.score
    assert a.trace == b.trace


def test_a_missing_marker_refuses_rather_than_defaulting() -> None:
    """A missing operative time is not a fast case. Defaulting it to zero biases
    the index downward on exactly the encounters whose documentation failed."""
    partial = tuple(m for m in CEILING if m.marker_id != "operative_minutes")
    with pytest.raises(MissingMarkerError, match="operative_minutes"):
        score(ENCOUNTER, partial, PACK, LAYER_A_STRUCTURED_ONLY)


def test_an_ml_marker_cannot_reach_a_layer_a_score() -> None:
    """Layer A excludes the model lanes by construction, not by discipline."""
    ml = Marker(
        ENCOUNTER.encounter_id,
        "adhesion_severity",
        Decimal(3),
        "ml",
        REF,
        "comprehend_medical",
        "1",
        model_id="comprehend-medical-2026",
    )
    result = score(ENCOUNTER, (*CEILING, ml), PACK, LAYER_A_STRUCTURED_ONLY)
    assert all(m.provenance == "structured" for m in result.markers)
    assert result.score == score(ENCOUNTER, CEILING, PACK, LAYER_A_STRUCTURED_ONLY).score


def test_the_filter_is_recorded_on_the_result() -> None:
    a = score(ENCOUNTER, CEILING, PACK, LAYER_A_STRUCTURED_ONLY)
    assert a.provenance_filter == ("structured",)
    assert a.rule_pack_digest == PACK.digest


def test_the_layer_a_filter_admits_rules_too() -> None:
    assert "rule" in LAYER_A and "rule" not in LAYER_A_STRUCTURED_ONLY


def test_the_trace_shows_every_admitted_term() -> None:
    """Every marker the filter let in, and nothing it kept out."""
    result = score(ENCOUNTER, CEILING, PACK, LAYER_A_STRUCTURED_ONLY)
    body = "\n".join(result.trace)
    for rule in PACK.markers:
        if rule.provenance == "structured":
            assert rule.marker_id in body
        else:
            assert rule.marker_id not in body


# ------------------------------------------------------------------ precedence

RULE_MINUTES = Marker(
    ENCOUNTER.encounter_id, "operative_minutes", Decimal(300), "rule", REF, "notes", "1"
)
RULE_EXTENT = Marker(
    ENCOUNTER.encounter_id, "anatomic_extent", Decimal(3), "rule", REF, "notes", "1"
)
RULE_ADHESION = Marker(
    ENCOUNTER.encounter_id, "adhesion_severity", Decimal(3), "rule", REF, "notes", "1"
)
RULE_EVENTS = Marker(
    ENCOUNTER.encounter_id, "intraoperative_events", Decimal(2), "rule", REF, "notes", "1"
)
NARRATIVE = (RULE_EXTENT, RULE_ADHESION, RULE_EVENTS)


def test_layer_a_needs_the_markers_only_the_notes_carry() -> None:
    """Structured fields alone cannot satisfy the pack under the Layer A filter."""
    with pytest.raises(MissingMarkerError, match="anatomic_extent"):
        score(ENCOUNTER, CEILING, PACK, LAYER_A)


def test_the_note_raises_the_score_of_a_case_the_fields_call_ordinary() -> None:
    """The case the structured lane cannot see.

    Mid-range on every discrete field, but the note describes bowel, ureter and
    bladder involvement, dense adhesions and two intraoperative events. That gap
    is the reason a structured-only figure understates the hardest cases.
    """
    middling = markers(
        operative_minutes="135",
        asa_class="2",
        distinct_surgical_specialties="1",
        estimated_blood_loss_ml="300",
        procedure_count="2",
        comorbidity_count="3",
        assistant_surgeon_count="1",
        inpatient="1",
    )
    structured_only = score(ENCOUNTER, middling, PACK, LAYER_A_STRUCTURED_ONLY)
    full = score(ENCOUNTER, (*middling, *NARRATIVE), PACK, LAYER_A)
    assert full.score.value > structured_only.score.value
    assert {m.provenance for m in full.markers} == {"structured", "rule"}


def test_both_lanes_at_their_anchors_score_one_hundred() -> None:
    ceiling_narrative = (
        Marker(ENCOUNTER.encounter_id, "anatomic_extent", Decimal(4), "rule", REF, "n", "1"),
        Marker(ENCOUNTER.encounter_id, "adhesion_severity", Decimal(3), "rule", REF, "n", "1"),
        Marker(ENCOUNTER.encounter_id, "intraoperative_events", Decimal(3), "rule", REF, "n", "1"),
    )
    assert score(ENCOUNTER, (*CEILING, *ceiling_narrative), PACK, LAYER_A).score == ComplexityScore(
        100
    )


def test_a_structured_field_outranks_the_note_that_restates_it() -> None:
    """OpTime says 240 minutes and the note says 300. OpTime is the record.

    Preferring the narrative would make the score a measure of how thoroughly a
    surgeon writes, and the busiest services write least.
    """
    result = score(ENCOUNTER, (*CEILING, *NARRATIVE, RULE_MINUTES), PACK, LAYER_A)
    scored_minutes = next(m for m in result.markers if m.marker_id == "operative_minutes")
    assert scored_minutes.provenance == "structured"
    assert scored_minutes.value == Decimal(240)


def test_the_displaced_marker_is_reported_not_dropped() -> None:
    result = score(ENCOUNTER, (*CEILING, *NARRATIVE, RULE_MINUTES), PACK, LAYER_A)
    assert [(m.marker_id, m.provenance) for m in result.superseded] == [
        ("operative_minutes", "rule")
    ]


def test_precedence_does_not_depend_on_extraction_order() -> None:
    """Last-write-wins would make the score an accident of iteration order."""
    forward = score(ENCOUNTER, (*CEILING, RULE_MINUTES, *NARRATIVE), PACK, LAYER_A)
    reverse = score(ENCOUNTER, (RULE_MINUTES, *NARRATIVE, *CEILING), PACK, LAYER_A)
    assert forward.score == reverse.score


def test_the_note_supplies_the_marker_when_the_field_is_absent() -> None:
    """A partner whose extract omits blood loss still gets the marker."""
    without = tuple(m for m in CEILING if m.marker_id != "estimated_blood_loss_ml")
    rule_ebl = Marker(
        ENCOUNTER.encounter_id, "estimated_blood_loss_ml", Decimal(600), "rule", REF, "n", "1"
    )
    result = score(ENCOUNTER, (*without, *NARRATIVE, rule_ebl), PACK, LAYER_A)
    ebl = next(m for m in result.markers if m.marker_id == "estimated_blood_loss_ml")
    assert ebl.provenance == "rule"
