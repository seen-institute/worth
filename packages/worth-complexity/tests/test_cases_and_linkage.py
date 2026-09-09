"""Reading the partner extract, and matching it to the money."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from worth_complexity import Cohort, link
from worth_complexity.cases import (
    ClinicalExtract,
    ExtractError,
    encounters,
    read_extract,
    read_table,
)
from worth_complexity.cli import FIXTURE
from worth_complexity.markers import extract as extract_markers
from worth_complexity.models import Encounter, RemitLine, SourceRef

CLINICAL = FIXTURE / "clinical"
REF = SourceRef("t.edi", "0" * 64, 1, "SVC03")


@pytest.fixture(scope="module")
def parsed() -> tuple[ClinicalExtract, tuple[Encounter, ...]]:
    e = read_extract(CLINICAL)
    return e, encounters(e)


def test_the_extract_hashes_the_bytes_it_read(
    parsed: tuple[ClinicalExtract, tuple[Encounter, ...]],
) -> None:
    e, _ = parsed
    for table in e.tables:
        assert len(table.sha256) == 64


def test_service_line_decides_which_cohort_an_encounter_is_in(
    parsed: tuple[ClinicalExtract, tuple[Encounter, ...]],
) -> None:
    _, encs = parsed
    gyn = [x for x in encs if x.service_line == "GYN"]
    assert all(x.cohort is Cohort.STUDY for x in gyn)
    assert all(x.cohort is Cohort.COMPARATOR for x in encs if x.service_line != "GYN")


def test_the_procedure_panel_is_ordered_and_the_primary_code_leads(
    parsed: tuple[ClinicalExtract, tuple[Encounter, ...]],
) -> None:
    _, encs = parsed
    multi = [x for x in encs if len(x.procedures) > 1]
    assert multi
    for x in multi:
        assert x.primary_cpt == x.procedures[0][0]


def test_every_marker_names_the_file_row_and_column_it_came_from(
    parsed: tuple[ClinicalExtract, tuple[Encounter, ...]],
) -> None:
    e, encs = parsed
    sets = extract_markers(e, encs)
    for markers in sets.values():
        for m in markers:
            assert m.source_ref.file.endswith(".txt")
            assert m.source_ref.row >= 2
            assert m.provenance == "structured"


def test_operative_minutes_are_incision_to_close(
    parsed: tuple[ClinicalExtract, tuple[Encounter, ...]],
) -> None:
    """Not in-room to out-of-room: turnover and positioning are real work but they
    are not operative effort, and in-room time is the easier of the two to game."""
    e, encs = parsed
    sets = extract_markers(e, encs)
    row = e.or_log.rows[0]
    enc = next(x for x in encs if x.encounter_id == row["log_id"])
    minutes = next(m for m in sets[enc.encounter_id] if m.marker_id == "operative_minutes")
    assert "procedure_start_dttm" in minutes.source_ref.column
    assert "procedure_close_dttm" in minutes.source_ref.column


def test_a_ragged_row_is_refused_with_its_line_number(tmp_path: Path) -> None:
    p = tmp_path / "or_log.txt"
    p.write_text("a|b|c\n1|2|3\n4|5\n")
    with pytest.raises(ExtractError, match=":3:"):
        read_table(p)


def test_a_missing_file_is_named(tmp_path: Path) -> None:
    with pytest.raises(ExtractError, match="missing extract file"):
        read_table(tmp_path / "nope.txt")


def _encounter(account: str) -> Encounter:
    return Encounter(
        "88",
        "1",
        "Z",
        account,
        date(2025, 3, 14),
        "GYN",
        "Obstetrics & Gynecology",
        Cohort.STUDY,
        "1234567893",
        "58662",
        (("58662", ""),),
        True,
    )


def _line(account: str, allowed: str, *, status: str = "1", claim_seq: int = 0) -> RemitLine:
    return RemitLine(
        account,
        "87726",
        "UHC",
        status,
        "58662",
        (),
        Decimal("9800"),
        Decimal(allowed),
        Decimal(allowed),
        date(2025, 3, 14),
        (),
        REF,
        claim_seq=claim_seq,
    )


def test_the_linkage_rate_is_part_of_the_result_not_a_log_line() -> None:
    """Failures are not random: complex multi-payer cases fail most, so a cohort
    that drops them silently is biased toward adequacy."""
    linkage = link((_encounter("A1"), _encounter("A2")), (_line("A1", "500"),))
    assert linkage.rate == Decimal("0.5000")
    assert "50.00%" in linkage.render()


def test_remittance_for_an_unknown_account_is_reported_as_an_orphan() -> None:
    linkage = link((_encounter("A1"),), (_line("A1", "500"), _line("A9", "500")))
    assert len(linkage.orphan_lines) == 1


def test_realized_payment_sums_the_allowed_amount_not_the_paid_amount() -> None:
    """Patient responsibility is a function of benefit design, not of what the
    work was worth; including it would make the index a measure of deductible season."""
    linkage = link((_encounter("A1"),), (_line("A1", "500"), _line("A1", "120")))
    assert linkage.linked[0].realized == Decimal("620")


# --------------------------------------------------------------------- reasons


def test_no_remittance_at_all_is_the_default_unlinked_reason() -> None:
    linkage = link((_encounter("A1"),), ())
    assert linkage.unlinked == (_encounter("A1"),)
    assert linkage.reasons == {"no remittance": 1}


def test_two_encounters_sharing_an_account_link_neither() -> None:
    """Decision 6, CONTRACT-SEEDS.md: link neither, report both."""
    encounters = (_encounter("A1"), _encounter("A1"))
    linkage = link(encounters, (_line("A1", "500"),))
    assert linkage.linked == ()
    assert len(linkage.unlinked) == 2
    assert linkage.reasons == {"duplicate account": 2}


def test_a_reversal_nets_against_the_earlier_remit_and_leaves_the_account_unlinked() -> None:
    """No corrected claim follows the reversal: nothing survives to count as
    this account's realized payment, and the encounter is reported unlinked
    with its own reason rather than silently dropped."""
    original = _line("A1", "500", status="1", claim_seq=0)
    reversal = _line("A1", "500", status="22", claim_seq=1)
    linkage = link((_encounter("A1"),), (original, reversal))
    assert linkage.linked == ()
    assert linkage.unlinked == (_encounter("A1"),)
    assert linkage.reasons == {"reversed without correction": 1}


def test_a_reversal_followed_by_a_corrected_claim_counts_the_correction() -> None:
    original = _line("A1", "500", status="1", claim_seq=0)
    reversal = _line("A1", "500", status="22", claim_seq=1)
    corrected = _line("A1", "650", status="1", claim_seq=2)
    linkage = link((_encounter("A1"),), (original, reversal, corrected))
    assert len(linkage.linked) == 1
    assert linkage.linked[0].realized == Decimal("650")
    assert linkage.reasons == {}


def test_reversal_and_correction_out_of_claim_seq_order_still_net_correctly() -> None:
    """``claim_seq`` order, not list order, is what ``link`` walks."""
    original = _line("A1", "500", status="1", claim_seq=0)
    reversal = _line("A1", "500", status="22", claim_seq=1)
    corrected = _line("A1", "650", status="1", claim_seq=2)
    linkage = link((_encounter("A1"),), (corrected, original, reversal))
    assert linkage.linked[0].realized == Decimal("650")
