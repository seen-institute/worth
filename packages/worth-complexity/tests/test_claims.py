"""The 837P claims fixture, cross-checked against the 835 remittance fixture.

Deliberately does not import ``worth_complexity.x12``: that module only reads
835s, and the point of this file is to check the 835 and 837 sides against
each other from outside any code that might be wrong on both. The reader
below is the "minimal segment reader" the two are checked with.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from worth_complexity.cli import FIXTURE

CLAIMS = FIXTURE / "claims"
REMITTANCE = FIXTURE / "remittance"

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
