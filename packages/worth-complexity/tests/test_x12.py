"""The 835 reader."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from worth_complexity.x12 import X12Error, parse_835, segments

if TYPE_CHECKING:
    from worth_complexity.models import RemitLine

CLAIM = (
    "ST*835*0001~"
    "N1*PR*UNITED HEALTHCARE INSURANCE COMPANY~"
    "REF*2U*87726~"
    "CLP*A70041188*1*18450.00*3894.20*0.00*12*2025061500123456*11*1~"
    "DTM*232*20250314~"
    "SVC*HC:58662:22*14200.00*3894.20**1~"
    "DTM*472*20250314~"
    "AMT*B6*3894.20~"
    "CAS*CO*45*10305.80~"
    "SVC*HC:58660*4250.00*0.00**1~"
    "DTM*472*20250314~"
    "AMT*B6*0.00~"
    "CAS*CO*97*4250.00~"
    "SE*13*0001~"
)


def parse(text: str = CLAIM) -> tuple[RemitLine, ...]:
    return parse_835(text, "t.edi", "0" * 64)


def test_newlines_are_padding_not_structure() -> None:
    """Real 835s arrive as one long line as often as not."""
    assert len(segments(CLAIM)) == len(segments(CLAIM.replace("~", "~\n")))


def test_the_composite_procedure_element_splits_into_code_and_modifiers() -> None:
    line = parse()[0]
    assert line.cpt == "58662"
    assert line.modifiers == ("22",)


def test_the_allowed_amount_comes_from_amt_b6() -> None:
    assert parse()[0].allowed == Decimal("3894.20")


def test_the_payer_is_carried_onto_every_line() -> None:
    for line in parse():
        assert line.payer_id == "87726"
        assert line.payer_name.startswith("UNITED HEALTHCARE")


def test_adjustments_are_kept_with_their_reason_codes() -> None:
    """The CAS segments are why the raw 835 is required rather than Epic's summary."""
    assert parse()[0].adjustments == (("CO", "45", Decimal("10305.80")),)


def test_a_line_allowed_nothing_reports_its_denial_code() -> None:
    """Documented, coded, submitted and refused: a fourth gap, free from the parse."""
    bundled = parse()[1]
    assert bundled.allowed == 0
    assert bundled.denial_codes == ("97",)


def test_every_value_names_the_segment_it_came_from() -> None:
    ref = parse()[0].source_ref
    assert ref.file == "t.edi"
    assert ref.column == "SVC03"
    assert ref.row > 0


def test_a_reversal_parses_and_is_tagged() -> None:
    """Decision 6 (CONTRACT-SEEDS.md): a reversal is no longer refused at
    parse time -- ``linkage.link`` is where it gets netted against the
    remit it reverses."""
    lines = parse(CLAIM.replace("*A70041188*1*", "*A70041188*22*"))
    assert all(line.is_reversal for line in lines)
    assert all(line.claim_status == "22" for line in lines)


def test_every_line_of_one_clp_occurrence_shares_a_claim_seq() -> None:
    lines = parse()
    assert {line.claim_seq for line in lines} == {0}


def test_a_second_clp_occurrence_gets_the_next_claim_seq() -> None:
    lines = parse(CLAIM + CLAIM)
    seqs = sorted({line.claim_seq for line in lines})
    assert seqs == [0, 1]


def test_a_line_without_an_allowed_amount_is_refused() -> None:
    """Deriving allowed from billed less adjustments guesses at bundling."""
    with pytest.raises(X12Error, match="AMT\\*B6"):
        parse(CLAIM.replace("AMT*B6*3894.20~", ""))


def test_an_empty_file_is_refused() -> None:
    with pytest.raises(X12Error, match="no service lines"):
        parse("ST*835*0001~SE*2*0001~")


def test_a_line_with_no_remark_code_carries_an_empty_rarc() -> None:
    """Most lines in the fixture carry no ``LQ*HE`` segment at all."""
    assert parse()[0].rarc == ()


def test_lq_he_segments_become_rarc_codes() -> None:
    """``LQ*HE*<code>`` names a remark code on the line it follows (decision 11)."""
    with_remark = CLAIM.replace("CAS*CO*97*4250.00~", "CAS*CO*97*4250.00~LQ*HE*N19~")
    bundled = parse(with_remark)[1]
    assert bundled.rarc == ("N19",)


def test_multiple_lq_he_segments_on_one_line_all_carry_through() -> None:
    with_remarks = CLAIM.replace("CAS*CO*97*4250.00~", "CAS*CO*97*4250.00~LQ*HE*N19~LQ*HE*M15~")
    bundled = parse(with_remarks)[1]
    assert bundled.rarc == ("N19", "M15")
