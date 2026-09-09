"""Method 1: procedure statements, and the documented-vs-submitted cross-check.

The fixture assertions here are deliberately about *why* the synthetic dataset
was built the way it was (see ``tools/make_synthetic_dataset.py``'s note on
``ADHESIONS`` and ``EVENTS``): adhesiolysis is bundled into 58662 under NCCI,
so a hard 58662 case that documents extensive adhesiolysis but did not also
bill 58660 is a textbook ``mismatched`` flag, not a missed one. Ureterolysis
has no gynaecologic code in this pack at all, so it always reports as
``no_code`` regardless of what was billed.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from worth_complexity import cases
from worth_complexity.claims import Claim, link_claims, read_directory
from worth_complexity.cli import FIXTURE
from worth_complexity.method1 import (
    STRENGTH_RANK,
    BucketTotal,
    Method1Summary,
    ProcedureStatement,
    cross_check,
    price_flag,
    statements,
    summarize,
)
from worth_complexity.models import Cohort, Encounter
from worth_complexity.notes import Note
from worth_complexity.rulepack import load

CLINICAL = FIXTURE / "clinical"
CLAIMS = FIXTURE / "claims"

PACK = load()
RULES = PACK.procedures


def a_note(text: str) -> Note:
    return Note(
        note_id="N1",
        encounter_id="E1",
        note_type="operative",
        service_date=date(2026, 1, 1),
        author_role="Attending Surgeon",
        filename="N1.txt",
        sha256="0" * 64,
        text=text,
    )


def an_encounter(
    encounter_id: str = "E1",
    primary_cpt: str = "58662",
    procedures: tuple[tuple[str, str], ...] = (("58662", ""),),
) -> Encounter:
    return Encounter(
        encounter_id=encounter_id,
        csn="C1",
        patient_id="P1",
        account_id="A1",
        service_date=date(2026, 1, 1),
        service_line="GYN",
        specialty="Obstetrics & Gynecology",
        cohort=Cohort.STUDY,
        facility_npi="1234567893",
        primary_cpt=primary_cpt,
        procedures=procedures,
        inpatient=False,
    )


NOTE_TEXT = """MOUNT SINAI HEALTH SYSTEM
OPERATIVE NOTE

PREOPERATIVE DIAGNOSIS:
N80.531

PROCEDURE PERFORMED:
Laparoscopic excision of pelvic lesions.

INDICATION:
She has a history of appendectomy and a prior lysis of adhesions, without
complication.

FINDINGS:
Dense pelvic adhesions were encountered and adhesiolysis was performed
sharply. There was no adhesiolysis required on the left side.

DESCRIPTION OF PROCEDURE:
The patient was prepped and draped in the usual sterile fashion. A small
serosal tear was identified and repaired primarily in two layers.
"""


# ---------------------------------------------------------------------------
# statements()
# ---------------------------------------------------------------------------


def test_the_procedure_performed_line_is_a_statement() -> None:
    stmts = statements(a_note(NOTE_TEXT))
    performed = [s for s in stmts if s.section == "procedure performed"]
    assert performed == [
        ProcedureStatement(
            encounter_id="E1",
            text="Laparoscopic excision of pelvic lesions.",
            section="procedure performed",
            source_ref=performed[0].source_ref,
        ),
    ]


def test_a_findings_sentence_naming_a_rule_pattern_is_a_statement() -> None:
    stmts = statements(a_note(NOTE_TEXT))
    findings = [s for s in stmts if s.section == "findings"]
    assert len(findings) == 1
    assert "adhesiolysis was performed" in findings[0].text


def test_the_negated_sentence_is_excluded() -> None:
    stmts = statements(a_note(NOTE_TEXT))
    assert not any("required" in s.text for s in stmts)
    assert not any(s.text.lower().startswith("there was no") for s in stmts)


def test_the_indication_section_is_never_read() -> None:
    """The decoy in INDICATION even contains the phrase "lysis of adhesions"."""
    stmts = statements(a_note(NOTE_TEXT))
    assert all(s.section != "indication" for s in stmts)
    assert not any("history of appendectomy" in s.text for s in stmts)


def test_a_description_sentence_naming_a_rule_pattern_is_a_statement() -> None:
    stmts = statements(a_note(NOTE_TEXT))
    description = [s for s in stmts if s.section == "description of procedure"]
    assert len(description) == 1
    assert "serosal tear" in description[0].text


def test_every_statement_carries_a_source_ref_into_the_note() -> None:
    for s in statements(a_note(NOTE_TEXT)):
        assert s.source_ref.file == "N1.txt"
        assert s.source_ref.sha256 == "0" * 64
        assert s.source_ref.row > 0


# ---------------------------------------------------------------------------
# cross_check()
# ---------------------------------------------------------------------------


def test_missed_when_the_candidate_is_absent_and_nothing_bundles_it() -> None:
    """58662 alone was billed; adhesiolysis's candidate 58660 bundles into it,
    but if the submitted set carried none of the bundled codes at all the
    flag would be missed. Force that by submitting an unrelated code."""
    enc = an_encounter(procedures=(("99999", ""),))
    stmts = statements(a_note(NOTE_TEXT))
    flags = cross_check(enc, stmts, claim=None, rules=RULES)
    adhesiolysis = next(f for f in flags if f.rule_id == "adhesiolysis")
    assert adhesiolysis.bucket == "missed"
    assert adhesiolysis.candidate_codes == ("58660",)
    assert adhesiolysis.vehicle_exists is True


def test_mismatched_when_the_candidate_is_bundled_into_what_was_submitted() -> None:
    enc = an_encounter(procedures=(("58662", ""),))
    stmts = statements(a_note(NOTE_TEXT))
    flags = cross_check(enc, stmts, claim=None, rules=RULES)
    adhesiolysis = next(f for f in flags if f.rule_id == "adhesiolysis")
    assert adhesiolysis.bucket == "mismatched"
    assert "58662" in adhesiolysis.reason
    assert "ncci/58660-58662" in adhesiolysis.reason


def test_no_flag_when_the_candidate_was_actually_submitted() -> None:
    enc = an_encounter(procedures=(("58662", ""), ("58660", "")))
    stmts = statements(a_note(NOTE_TEXT))
    flags = cross_check(enc, stmts, claim=None, rules=RULES)
    assert not any(f.rule_id == "adhesiolysis" for f in flags)


def test_no_code_when_no_candidate_exists() -> None:
    note = a_note(
        "FINDINGS:\n"
        "The left ureter was dissected free along its pelvic course and "
        "ureterolysis was performed.\n"
    )
    enc = an_encounter(procedures=(("58662", ""),))
    flags = cross_check(enc, statements(note), claim=None, rules=RULES)
    ureterolysis = next(f for f in flags if f.rule_id == "ureterolysis")
    assert ureterolysis.bucket == "no_code"
    assert ureterolysis.candidate_codes == ()
    assert ureterolysis.vehicle_exists is False
    assert ureterolysis.reason == "no candidate code"


def test_submitted_codes_prefer_the_claim_over_the_or_log_panel() -> None:
    enc = an_encounter(procedures=(("58662", ""),))
    stmts = statements(a_note(NOTE_TEXT))
    claim = Claim(
        account_id="A1",
        payer_id="80141",
        payer_name="HEALTHFIRST PHSP INC",
        total_charge=Decimal("100"),
        diagnoses=("N80.531",),
        lines=(),
        submitted=date(2026, 1, 5),
        source_ref=stmts[0].source_ref,
    )
    flags = cross_check(enc, stmts, claim=claim, rules=RULES)
    adhesiolysis = next(f for f in flags if f.rule_id == "adhesiolysis")
    # The claim carries no codes at all, unlike the OR log panel (58662), so
    # the reported submitted_codes must reflect the claim, not the panel.
    assert adhesiolysis.submitted_codes == ()
    assert adhesiolysis.bucket == "missed"


def test_evidence_strength_is_strong_in_procedure_performed() -> None:
    note = a_note("PROCEDURE PERFORMED:\nAdhesiolysis was performed.\n")
    enc = an_encounter(procedures=(("99999", ""),))
    flags = cross_check(enc, statements(note), claim=None, rules=RULES)
    assert flags[0].evidence_strength == "strong"


def test_evidence_strength_is_strong_when_the_or_log_panel_agrees() -> None:
    """The claim omits 58660 (so a flag is still produced), but the OR log's
    own procedure panel lists it: the biller's own coding agrees with the
    note even though the submitted claim does not, which is strong evidence
    on its own terms."""
    note = a_note("FINDINGS:\nDense adhesions were noted; adhesiolysis was performed.\n")
    enc = an_encounter(procedures=(("58662", ""), ("58660", "")))
    stmts = statements(note)
    claim = Claim(
        account_id="A1",
        payer_id="80141",
        payer_name="HEALTHFIRST PHSP INC",
        total_charge=Decimal("100"),
        diagnoses=(),
        lines=(),
        submitted=date(2026, 1, 5),
        source_ref=stmts[0].source_ref,
    )
    flags = cross_check(enc, stmts, claim=claim, rules=RULES)
    assert flags[0].bucket == "missed"
    assert flags[0].evidence_strength == "strong"


def test_evidence_strength_is_moderate_with_a_performed_verb_but_no_panel_code() -> None:
    note = a_note("FINDINGS:\nDense adhesions were noted; adhesiolysis was performed.\n")
    enc = an_encounter(procedures=(("58662", ""),))
    flags = cross_check(enc, statements(note), claim=None, rules=RULES)
    assert flags[0].evidence_strength == "moderate"


def test_evidence_strength_is_weak_without_a_performed_verb() -> None:
    note = a_note("FINDINGS:\nThe field showed extensive adhesiolysis changes.\n")
    enc = an_encounter(procedures=(("58662", ""),))
    flags = cross_check(enc, statements(note), claim=None, rules=RULES)
    assert flags[0].evidence_strength == "weak"


def test_strength_rank_is_ordinal() -> None:
    assert STRENGTH_RANK["weak"] < STRENGTH_RANK["moderate"] < STRENGTH_RANK["strong"]


# ---------------------------------------------------------------------------
# price_flag() / summarize()
# ---------------------------------------------------------------------------


def test_price_flag_returns_a_copy_leaving_the_original_unpriced() -> None:
    note = a_note("PROCEDURE PERFORMED:\nAdhesiolysis was performed.\n")
    enc = an_encounter(procedures=(("99999", ""),))
    flag = cross_check(enc, statements(note), claim=None, rules=RULES)[0]
    priced = price_flag(flag, amount=Decimal("214.55"), trace=("PFS at RVU26D",))
    assert flag.priced is None
    assert flag.pricing_trace == ()
    assert priced.priced == Decimal("214.55")
    assert priced.pricing_trace == ("PFS at RVU26D",)
    assert priced.statement == flag.statement


def test_summarize_counts_and_sums_by_bucket() -> None:
    note = a_note("PROCEDURE PERFORMED:\nAdhesiolysis was performed.\n")
    enc = an_encounter(procedures=(("99999", ""),))
    flags = cross_check(enc, statements(note), claim=None, rules=RULES)
    priced = tuple(price_flag(f, amount=Decimal("214.55"), trace=()) for f in flags)
    summary = summarize(priced)
    assert isinstance(summary, Method1Summary)
    assert summary.n_flags == len(flags)
    missed = summary.by_bucket["missed"]
    assert isinstance(missed, BucketTotal)
    assert missed.count == 1
    assert missed.priced_count == 1
    assert missed.dollars == Decimal("214.55")


def test_summarize_leaves_dollars_none_for_an_unpriced_bucket() -> None:
    note = a_note("FINDINGS:\nThe left ureter was dissected free and ureterolysis was performed.\n")
    enc = an_encounter()
    flags = cross_check(enc, statements(note), claim=None, rules=RULES)
    summary = summarize(flags)
    no_code = summary.by_bucket["no_code"]
    assert no_code.count == 1
    assert no_code.priced_count == 0
    assert no_code.dollars is None


# ---------------------------------------------------------------------------
# Whole fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def linked_claims() -> dict[str, Claim]:
    extract = cases.read_extract(CLINICAL)
    encs = cases.encounters(extract)
    claims = read_directory(CLAIMS)
    return link_claims(encs, claims)


def test_every_hard_58662_case_documenting_adhesiolysis_without_58660_is_mismatched(
    linked_claims: dict[str, Claim],
) -> None:
    extract = cases.read_extract(CLINICAL)
    by_id = {e.encounter_id: e for e in cases.encounters(extract)}

    checked = 0
    for note in extract.notes:
        enc = by_id.get(note.encounter_id)
        if enc is None or enc.primary_cpt != "58662":
            continue
        if "adhesiolysis" not in note.text.lower():
            continue
        claim = linked_claims.get(enc.encounter_id)
        submitted = claim.codes if claim is not None else tuple(c for c, _ in enc.procedures)
        if "58660" in submitted:
            continue  # already billed separately: correctly produces no flag

        checked += 1
        flags = cross_check(enc, statements(note), claim, RULES)
        adhesiolysis = [f for f in flags if f.rule_id == "adhesiolysis"]
        assert adhesiolysis, enc.encounter_id
        assert all(f.bucket == "mismatched" for f in adhesiolysis), enc.encounter_id

    assert checked > 0, "the fixture should contain at least one such case"


def test_ureterolysis_sentences_always_report_no_code(linked_claims: dict[str, Claim]) -> None:
    extract = cases.read_extract(CLINICAL)
    by_id = {e.encounter_id: e for e in cases.encounters(extract)}

    checked = 0
    for note in extract.notes:
        enc = by_id.get(note.encounter_id)
        if enc is None:
            continue
        ureter_stmts = tuple(
            s
            for s in statements(note)
            if "ureter" in s.text.lower() and "dissected" in s.text.lower()
        )
        if not ureter_stmts:
            continue
        checked += 1
        claim = linked_claims.get(enc.encounter_id)
        flags = cross_check(enc, ureter_stmts, claim, RULES)
        assert flags, enc.encounter_id
        assert all(f.bucket == "no_code" for f in flags), enc.encounter_id

    assert checked > 0, "the fixture should contain at least one ureterolysis mention"
