"""The type-level methodology guards."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from worth_complexity import ComplexityScore, MethodologyViolation
from worth_complexity.models import Cohort, Encounter, Marker, SourceRef, ratio_of

REF = SourceRef("or_log.txt", "0" * 64, 2, "asa_class")


def test_dividing_money_by_a_complexity_score_raises() -> None:
    """I-3: the score is ordinal and is never a denominator."""
    with pytest.raises(MethodologyViolation, match="never a denominator"):
        Decimal("4812.66") / ComplexityScore(78)


def test_the_refusal_message_points_at_the_methodology() -> None:
    """Whoever hits this in 2028 should understand why, not route around it."""
    with pytest.raises(MethodologyViolation) as exc:
        Decimal(1) / ComplexityScore(50)
    message = str(exc.value)
    assert "realized payment" in message
    assert "expected payment" in message
    assert "Methodology Notes" in message


@pytest.mark.parametrize("value", [-1, 101, 1000])
def test_scores_outside_the_ordinal_scale_are_refused(value: int) -> None:
    with pytest.raises(MethodologyViolation):
        ComplexityScore(value)


def test_scores_order_and_compare() -> None:
    assert ComplexityScore(78) > ComplexityScore(32)
    assert str(ComplexityScore(78)) == "78/100"


def test_a_model_lane_marker_must_name_its_model() -> None:
    with pytest.raises(MethodologyViolation, match="requires a model_id"):
        Marker("88", "adhesion_severity", Decimal(2), "ml", REF, "x", "1")


def test_a_structured_marker_must_not_name_a_model() -> None:
    """A structured field did not come from a model; claiming one would be a lie
    in the provenance record, and provenance is what Layer A rests on."""
    with pytest.raises(MethodologyViolation, match="must not name a model"):
        Marker("88", "asa_class", Decimal(3), "structured", REF, "x", "1", model_id="m")


def test_the_ratio_is_money_over_money() -> None:
    assert str(ratio_of(Decimal("700.00"), Decimal("1000.00"))) == "0.70"


def test_a_non_positive_expected_payment_is_refused() -> None:
    from worth_complexity import NoReferenceCurveError

    with pytest.raises(NoReferenceCurveError):
        ratio_of(Decimal("700.00"), Decimal("0.00"))


def test_an_encounter_validates_its_procedure_code() -> None:
    from worth_complexity.models import WorthComplexityError

    with pytest.raises(WorthComplexityError, match="not a CPT"):
        Encounter(
            "88",
            "1",
            "Z",
            "A",
            date(2025, 3, 14),
            "GYN",
            "Obstetrics & Gynecology",
            Cohort.STUDY,
            "1234567893",
            "not-a-code",
            (("58662", ""),),
            True,
        )
