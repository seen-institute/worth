"""The dollar spine, its decomposition, and every reachable signature pattern."""

from __future__ import annotations

from decimal import Decimal

from worth_complexity.curve import fit
from worth_complexity.models import ComplexityScore
from worth_complexity.signature import (
    DollarSpine,
    decompose,
    signature_of,
    spine,
)

PERFECT = tuple((Decimal(x), Decimal(10 + 2 * x)) for x in range(10, 90, 8))


def _spine(
    *,
    realized: Decimal,
    m2_expected: Decimal,
    m2_adjustment: Decimal,
    m3_expected: Decimal,
    m1_unpriced: Decimal | None,
) -> DollarSpine:
    return DollarSpine(
        realized=realized,
        m1_unpriced=m1_unpriced,
        m1_missed=None,
        m1_mismatched=None,
        m1_no_code=None,
        m1_unpriced_count=0,
        m2_expected=m2_expected,
        m2_adjustment=m2_adjustment,
        m3_expected=m3_expected,
    )


# --------------------------------------------------------------------------
# spine() and decompose()
# --------------------------------------------------------------------------


def test_spine_computes_m2_from_the_curve_at_score_and_at_the_code_median() -> None:
    f = fit("t", PERFECT)
    s = spine(
        realized=Decimal(200),
        score=ComplexityScore(50),
        curve=f,
        code_median=Decimal(40),
        multiplier=Decimal(2),
        m3_expected=Decimal(300),
        m1_total=Decimal(50),
        m1_split=(Decimal(30), Decimal(20), None),
        m1_unpriced_count=1,
    )
    # curve: 10 + 2*x -> at 50 = 110, at 40 = 90
    assert s.m2_expected == Decimal("220.00")
    assert s.m2_adjustment == Decimal("40.00")
    assert s.m1_unpriced == Decimal(50)
    assert s.m1_missed == Decimal(30)
    assert s.m1_mismatched == Decimal(20)
    assert s.m1_no_code is None
    assert s.m1_unpriced_count == 1
    assert s.m3_expected == Decimal(300)


def test_decomposition_identity_holds() -> None:
    f = fit("t", PERFECT)
    s = spine(
        realized=Decimal(200),
        score=ComplexityScore(50),
        curve=f,
        code_median=Decimal(40),
        multiplier=Decimal(2),
        m3_expected=Decimal(300),
        m1_total=Decimal(50),
        m1_split=(Decimal(30), Decimal(20), None),
        m1_unpriced_count=1,
    )
    d = decompose(s)
    assert d.m3_shortfall == Decimal("100.00")
    assert d.compression == Decimal("40.00")
    assert d.center_mispricing == Decimal("60.00")
    # The identity the contract names explicitly.
    assert d.m3_shortfall == d.center_mispricing + d.compression
    # M1 is reported beside, never folded into the arithmetic above.
    assert d.m1_unpriced == Decimal(50)


# --------------------------------------------------------------------------
# signature_of(): every pattern reachable
# --------------------------------------------------------------------------


def test_m1_dominant_with_a_vehicle() -> None:
    s = _spine(
        realized=Decimal(500),
        m2_expected=Decimal(600),
        m2_adjustment=Decimal(100),
        m3_expected=Decimal(600),
        m1_unpriced=Decimal(1000),
    )
    sig = signature_of(s, any_vehicle=True, code_kind="procedure")
    assert sig.pattern_id == "m1-dominant-vehicle"
    assert sig.m1.lever == "coding-documentation"
    assert sig.m1.built is True


def test_m1_dominant_with_no_vehicle() -> None:
    s = _spine(
        realized=Decimal(500),
        m2_expected=Decimal(600),
        m2_adjustment=Decimal(100),
        m3_expected=Decimal(600),
        m1_unpriced=Decimal(1000),
    )
    sig = signature_of(s, any_vehicle=False, code_kind="procedure")
    assert sig.pattern_id == "m1-dominant-no-vehicle"
    assert sig.m1.lever == "code-set-gap"


def test_m1_and_m3_dominant_over_m2() -> None:
    s = _spine(
        realized=Decimal(0),
        m2_expected=Decimal(100),
        m2_adjustment=Decimal(100),
        m3_expected=Decimal(300),
        m1_unpriced=Decimal(250),
    )
    sig = signature_of(s, any_vehicle=False, code_kind="procedure")
    assert sig.pattern_id == "m1-m3-dominant"


def test_structural_converging() -> None:
    s = _spine(
        realized=Decimal(800),
        m2_expected=Decimal(950),
        m2_adjustment=Decimal(30),
        m3_expected=Decimal(1000),
        m1_unpriced=Decimal(10),
    )
    sig = signature_of(s, any_vehicle=True, code_kind="procedure")
    assert sig.m2_m3_converge is True
    assert sig.pattern_id == "structural-converging"


def test_center_mispriced() -> None:
    s = _spine(
        realized=Decimal(800),
        m2_expected=Decimal(700),
        m2_adjustment=Decimal(50),
        m3_expected=Decimal(1000),
        m1_unpriced=Decimal(10),
    )
    sig = signature_of(s, any_vehicle=True, code_kind="procedure")
    assert sig.m2_m3_converge is False
    assert sig.pattern_id == "center-mispriced"


def test_compression() -> None:
    s = _spine(
        realized=Decimal(500),
        m2_expected=Decimal(200),
        m2_adjustment=Decimal(150),
        m3_expected=Decimal(600),
        m1_unpriced=Decimal(5),
    )
    sig = signature_of(s, any_vehicle=True, code_kind="level")
    assert sig.pattern_id == "compression"
    assert sig.m2.lever == "code-structure"


def test_mixed_when_nothing_dominates() -> None:
    s = _spine(
        realized=Decimal(500),
        m2_expected=Decimal(300),
        m2_adjustment=Decimal(50),
        m3_expected=Decimal(550),
        m1_unpriced=Decimal(40),
    )
    sig = signature_of(s, any_vehicle=True, code_kind="procedure")
    assert sig.pattern_id == "mixed"


def test_missing_m1_is_treated_as_zero_but_built_is_false() -> None:
    s = _spine(
        realized=Decimal(500),
        m2_expected=Decimal(0),
        m2_adjustment=Decimal(200),
        m3_expected=Decimal(600),
        m1_unpriced=None,
    )
    sig = signature_of(s, any_vehicle=False, code_kind="procedure")
    assert sig.m1.built is False
    assert sig.m1.dollars is None
    # Treated as zero: small relative to M3's 100-dollar shortfall, and M2's
    # compression (200) dominates M3 (100), so the compression pattern fires.
    assert sig.pattern_id == "compression"


def test_m3_lever_is_always_fee_schedule_and_m2_always_code_structure() -> None:
    s = _spine(
        realized=Decimal(500),
        m2_expected=Decimal(300),
        m2_adjustment=Decimal(50),
        m3_expected=Decimal(550),
        m1_unpriced=Decimal(40),
    )
    sig = signature_of(s, any_vehicle=True, code_kind="procedure")
    assert sig.m3.lever == "fee-schedule"
    assert sig.m2.lever == "code-structure"
    assert sig.m3.built is True
    assert sig.m2.built is True
