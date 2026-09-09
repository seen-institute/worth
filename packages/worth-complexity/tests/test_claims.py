"""The 837P claims fixture, cross-checked against the 835 remittance fixture.

Deliberately does not import ``worth_complexity.x12``: that module only reads
835s, and the point of this file is to check the 835 and 837 sides against
each other from outside any code that might be wrong on both. The reader
below is the "minimal segment reader" the two are checked with.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from worth_complexity import cases
from worth_complexity.claims import (
    Claim,
    ClaimLine,
    ClaimsError,
    link_claims,
    parse_837,
    read_837,
    read_directory,
)
from worth_complexity.claims import segments as claims_segments
from worth_complexity.cli import FIXTURE
from worth_complexity.models import Cohort

CLAIMS = FIXTURE / "claims"
REMITTANCE = FIXTURE / "remittance"
CLINICAL = FIXTURE / "clinical"

PAYER_NAMES = {
    "UNITED HEALTHCARE INSURANCE COMPANY",
    "AETNA HEALTH INC",
    "NGS MEDICARE PART B",
    "HEALTHFIRST PHSP INC",
}


def _segments(path: Path) -> list[list[str]]:
    """Split one EDI file into segments, each a list of elements (tag first)."""
    raw = path.read_text(encoding="utf-8").replace("\r", "").replace("\n", "")
    return [chunk.split("*") for chunk in raw.split("~") if chunk.strip()]


def _envelope(segs: list[list[str]]) -> tuple[str, str, str, str]:
    """Return (ISA13, GS06, ST/SE control, GE02) after checking they balance."""
    isa = next(s for s in segs if s[0] == "ISA")
    gs = next(s for s in segs if s[0] == "GS")
    st = next(s for s in segs if s[0] == "ST")
    se = next(s for s in segs if s[0] == "SE")
    ge = next(s for s in segs if s[0] == "GE")
    iea = next(s for s in segs if s[0] == "IEA")

    st_index = segs.index(st)
    se_index = segs.index(se)
    assert int(se[1]) == se_index - st_index + 1, "SE01 must count ST..SE inclusive"
    assert st[2] == se[2], "ST02 and SE02 must be the same control number"
    assert ge[2] == gs[6], "GE02 must echo the functional group control number (GS06)"
    assert iea[2] == isa[13], "IEA02 must echo the interchange control number (ISA13)"
    return isa[13], gs[6], st[2], ge[2]


def _claims_837(path: Path) -> dict[str, tuple[Decimal, frozenset[tuple[str, tuple[str, ...]]]]]:
    """account -> (CLM02 total charge, {(cpt, modifiers)} from SV1 lines)."""
    segs = _segments(path)
    _envelope(segs)
    out: dict[str, tuple[Decimal, frozenset[tuple[str, tuple[str, ...]]]]] = {}
    account = ""
    total = Decimal(0)
    lines: set[tuple[str, tuple[str, ...]]] = set()
    for s in segs:
        if s[0] == "CLM":
            if account:
                out[account] = (total, frozenset(lines))
            account = s[1]
            total = Decimal(s[2])
            lines = set()
        elif s[0] == "SV1":
            parts = s[1].split(":")  # HC:cpt[:mod...]
            lines.add((parts[1], tuple(parts[2:])))
    if account:
        out[account] = (total, frozenset(lines))
    return out


def _claims_835(path: Path) -> dict[str, tuple[Decimal, frozenset[tuple[str, tuple[str, ...]]]]]:
    """account -> (CLP03 total charge, {(cpt, modifiers)} from SVC lines)."""
    segs = _segments(path)
    out: dict[str, tuple[Decimal, frozenset[tuple[str, tuple[str, ...]]]]] = {}
    account = ""
    total = Decimal(0)
    lines: set[tuple[str, tuple[str, ...]]] = set()
    for s in segs:
        if s[0] == "CLP":
            if account:
                out[account] = (total, frozenset(lines))
            account = s[1]
            total = Decimal(s[3])
            lines = set()
        elif s[0] == "SVC":
            parts = s[1].split(":")  # HC:cpt[:mod...]
            lines.add((parts[1], tuple(parts[2:])))
    if account:
        out[account] = (total, frozenset(lines))
    return out


def _all_claims_837() -> dict[str, tuple[Decimal, frozenset[tuple[str, tuple[str, ...]]]]]:
    out: dict[str, tuple[Decimal, frozenset[tuple[str, tuple[str, ...]]]]] = {}
    for path in sorted(CLAIMS.glob("*.edi")):
        claims = _claims_837(path)
        assert out.keys().isdisjoint(claims), f"{path.name} repeats an account seen elsewhere"
        out.update(claims)
    return out


def _all_claims_835() -> dict[str, tuple[Decimal, frozenset[tuple[str, tuple[str, ...]]]]]:
    out: dict[str, tuple[Decimal, frozenset[tuple[str, tuple[str, ...]]]]] = {}
    for path in sorted(REMITTANCE.glob("*.edi")):
        claims = _claims_835(path)
        assert out.keys().isdisjoint(claims), f"{path.name} repeats an account seen elsewhere"
        out.update(claims)
    return out


def test_the_fixture_has_837_files() -> None:
    assert list(CLAIMS.glob("*.edi"))


def test_every_835_claim_has_exactly_one_837_claim_and_vice_versa() -> None:
    assert set(_all_claims_835()) == set(_all_claims_837())


def test_the_service_lines_match_cpt_and_modifiers() -> None:
    """Per claim, the (CPT, modifiers) on the 837 SV1 lines equal the 835 SVC lines'."""
    claims_835 = _all_claims_835()
    claims_837 = _all_claims_837()
    for account, (_, lines_835) in claims_835.items():
        _, lines_837 = claims_837[account]
        assert lines_837 == lines_835, account


def test_the_total_charge_matches_clp03() -> None:
    """Per claim, CLM02 equals CLP03: the same submitted charge on both sides."""
    claims_835 = _all_claims_835()
    claims_837 = _all_claims_837()
    for account, (total_835, _) in claims_835.items():
        total_837, _ = claims_837[account]
        assert total_837 == total_835, account


def test_every_837_envelope_balances() -> None:
    """SE segment count, and the GE/IEA control numbers, for every claims file."""
    for path in sorted(CLAIMS.glob("*.edi")):
        _envelope(_segments(path))  # raises on any mismatch


def test_no_payer_name_other_than_the_four_the_835s_carry() -> None:
    seen: set[str] = set()
    for path in sorted(CLAIMS.glob("*.edi")):
        for s in _segments(path):
            if s[0] == "NM1" and s[1] in {"40", "PR"}:
                seen.add(s[3])
    assert seen == PAYER_NAMES


# ---------------------------------------------------------------------------
# worth_complexity.claims: the module under test, rather than the fixture
# cross-check above.
# ---------------------------------------------------------------------------

SAMPLE = CLAIMS / "837_20260122_80141_125.edi"


def test_the_sample_file_parses_into_one_claim_per_hl_loop() -> None:
    claims = read_837(SAMPLE)
    assert len(claims) == 4
    assert all(isinstance(c, Claim) for c in claims)


def test_a_specific_claim_carries_its_account_payer_and_diagnoses() -> None:
    claims = {c.account_id: c for c in read_837(SAMPLE)}
    claim = claims["A7167976"]
    assert claim.payer_id == "80141"
    assert claim.payer_name == "HEALTHFIRST PHSP INC"
    assert claim.total_charge == Decimal("2164.26")
    assert claim.diagnoses == ("N80.531", "K66.0", "N92.0")
    assert claim.submitted == date(2026, 1, 22)
    assert claim.codes == ("58662",)


def test_the_claim_line_carries_money_as_decimal_and_a_source_ref() -> None:
    claims = {c.account_id: c for c in read_837(SAMPLE)}
    (line,) = claims["A7167976"].lines
    assert isinstance(line, ClaimLine)
    assert line.cpt == "58662"
    assert line.charge == Decimal("2164.26")
    assert isinstance(line.charge, Decimal)
    assert line.units == Decimal("1")
    assert line.service_date == date(2026, 1, 13)
    assert line.line_number == 1
    assert line.source_ref.file == SAMPLE.name
    assert line.source_ref.row > 0
    assert line.source_ref.column == "SV101"


def test_newlines_are_padding_not_structure() -> None:
    text = SAMPLE.read_text(encoding="utf-8")
    assert len(claims_segments(text)) == len(claims_segments(text.replace("~", "~\n")))


def test_read_directory_over_the_fixture_links_every_study_encounter() -> None:
    extract = cases.read_extract(CLINICAL)
    encs = cases.encounters(extract)
    claims = read_directory(CLAIMS)
    assert claims  # the fixture ships claims
    linked = link_claims(encs, claims)
    study = [e for e in encs if e.cohort == Cohort.STUDY]
    assert study
    assert all(e.encounter_id in linked for e in study)
    for e in study:
        assert linked[e.encounter_id].account_id == e.account_id


def test_read_directory_tolerates_a_missing_directory() -> None:
    assert read_directory(CLAIMS.parent / "does-not-exist") == ()


def test_read_directory_tolerates_an_empty_directory(tmp_path: Path) -> None:
    empty = tmp_path / "claims"
    empty.mkdir()
    assert read_directory(empty) == ()


def test_link_claims_prefers_the_first_submitted_claim_for_a_repeated_account() -> None:
    early = parse_837(
        "BHT*0019*00*1*20260101*0800*CH~"
        "NM1*PR*2*HEALTHFIRST PHSP INC*****PI*80141~"
        "CLM*ACC1*100.00***11:B:1*Y*A*Y*Y~"
        "HI*ABK:N80.531~"
        "LX*1~"
        "SV1*HC:58662*100.00*UN*1***1~"
        "DTP*472*D8*20260101~"
        "SE*8*1~",
        "early.edi",
        "0" * 64,
    )
    late = parse_837(
        "BHT*0019*00*2*20260201*0800*CH~"
        "NM1*PR*2*HEALTHFIRST PHSP INC*****PI*80141~"
        "CLM*ACC1*100.00***11:B:1*Y*A*Y*Y~"
        "HI*ABK:N80.531~"
        "LX*1~"
        "SV1*HC:58660*100.00*UN*1***1~"
        "DTP*472*D8*20260101~"
        "SE*8*2~",
        "late.edi",
        "1" * 64,
    )
    enc = replace(cases.encounters(cases.read_extract(CLINICAL))[0], account_id="ACC1")
    linked = link_claims((enc,), (*late, *early))
    assert linked[enc.encounter_id].source_ref.file == "early.edi"
    assert linked[enc.encounter_id].codes == ("58662",)


def test_a_frequency_code_7_claim_defaults_to_original() -> None:
    (claim,) = parse_837(
        "BHT*0019*00*1*20260101*0800*CH~"
        "NM1*PR*2*HEALTHFIRST PHSP INC*****PI*80141~"
        "CLM*ACC1*100.00***11:B:1*Y*A*Y*Y~"
        "HI*ABK:N80.531~"
        "LX*1~"
        "SV1*HC:58662*100.00*UN*1***1~"
        "DTP*472*D8*20260101~"
        "SE*8*1~",
        "t.edi",
        "0" * 64,
    )
    assert claim.frequency_code == "1"


def test_a_frequency_code_7_claim_replaces_the_earlier_one_regardless_of_date() -> None:
    """Decision 6, CONTRACT-SEEDS.md: an explicit replacement (CLM05-3 == 7)
    supersedes whatever is on file for the account, even though it was
    submitted *before* a later, ordinary duplicate would have been."""
    original = parse_837(
        "BHT*0019*00*1*20260101*0800*CH~"
        "NM1*PR*2*HEALTHFIRST PHSP INC*****PI*80141~"
        "CLM*ACC1*100.00***11:B:1*Y*A*Y*Y~"
        "HI*ABK:N80.531~"
        "LX*1~"
        "SV1*HC:58660*100.00*UN*1***1~"
        "DTP*472*D8*20260101~"
        "SE*8*1~",
        "early.edi",
        "0" * 64,
    )
    replacement = parse_837(
        "BHT*0019*00*2*20260102*0800*CH~"
        "NM1*PR*2*HEALTHFIRST PHSP INC*****PI*80141~"
        "CLM*ACC1*150.00***11:B:7*Y*A*Y*Y~"
        "HI*ABK:N80.531~"
        "LX*1~"
        "SV1*HC:58662*150.00*UN*1***1~"
        "DTP*472*D8*20260101~"
        "SE*8*2~",
        "replacement.edi",
        "1" * 64,
    )
    enc = replace(cases.encounters(cases.read_extract(CLINICAL))[0], account_id="ACC1")

    assert replacement[0].frequency_code == "7"
    linked = link_claims((enc,), (*original, *replacement))
    assert linked[enc.encounter_id].source_ref.file == "replacement.edi"
    assert linked[enc.encounter_id].codes == ("58662",)

    # Order given doesn't matter: the replacement still wins.
    linked_reversed = link_claims((enc,), (*replacement, *original))
    assert linked_reversed[enc.encounter_id].source_ref.file == "replacement.edi"


def test_a_claim_with_no_service_lines_is_refused() -> None:
    with pytest.raises(ClaimsError, match="no SV1"):
        parse_837(
            "BHT*0019*00*1*20260101*0800*CH~"
            "NM1*PR*2*HEALTHFIRST PHSP INC*****PI*80141~"
            "CLM*ACC1*100.00***11:B:1*Y*A*Y*Y~"
            "HI*ABK:N80.531~"
            "SE*4*1~",
            "bad.edi",
            "0" * 64,
        )
