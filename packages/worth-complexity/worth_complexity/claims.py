"""A small ANSI X12 837P reader, scoped to the loops WORTH actually needs.

Mirrors ``x12.py``'s 835 reader by design, not by accident: it is the same
kind of document for the same reason. An 837P is how the provider tells the
payer what it is asking to be paid for, it is the federally mandated
counterpart to the 835 the payer sends back, and it arrives as the same kind
of segment stream. Reusing that module's ``Segment``/``segments`` tokenizer
rather than writing a second one keeps the two readers agreeing about what a
segment is; everything after that point is genuinely different, because a
claim and a remittance carry different facts.

The 837 side matters to Method 1 specifically: the methodology's
documented-vs-submitted cross-check needs the code list a claim was actually
*submitted* with, not merely what the OR log's procedure panel says was
billed. The OR log panel is Epic's own record of what the surgical team
intended to bill; the 837 is what the biller actually sent to the payer, after
whatever coding review happened in between. They usually agree. When they
do not, the difference is exactly what Method 1 is built to surface, so the
837, when one is available, always wins as the source of "submitted".

As with the 835 reader, this reads perhaps a dozen segment types rather than
the full implementation guide, and every value carries a pointer back to the
segment and element it came from for the same reason: a referee is entitled to
ask where a code or a charge came from and get an answer precise enough to
find by eye in the raw file.

Not handled here, deliberately, for the same reasons ``x12.py`` gives:
secondary-payer coordination of benefits loops and institutional (837I)
claims.

A replacement claim (``CLM05-3 == "7"``, the frequency type code in CLM05's
third component) *is* handled (decision 6, CONTRACT-SEEDS.md): it supersedes
whatever claim was on file for its account, in :func:`link_claims`, rather
than losing to the earliest-submitted rule below every other claim still
follows. A void (``"8"``) is not modelled -- the synthetic dataset never
submits one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from worth_complexity.models import Encounter, SourceRef, WorthComplexityError
from worth_complexity.provenance import sha256_file
from worth_complexity.x12 import COMPONENT_SEPARATOR, _x12_date, segments

__all__ = [
    "Claim",
    "ClaimLine",
    "ClaimsError",
    "link_claims",
    "parse_837",
    "read_837",
    "read_directory",
    "segments",
]


class ClaimsError(WorthComplexityError):
    """The 837 is malformed, or uses a construct this reader does not model."""


def _money(value: str, where: str) -> Decimal:
    try:
        return Decimal(value or "0")
    except ArithmeticError as exc:
        msg = f"{where}: not an amount: {value!r}"
        raise ClaimsError(msg) from exc


def _line_number(value: str, where: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        msg = f"{where}: not an integer: {value!r}"
        raise ClaimsError(msg) from exc


@dataclass(frozen=True, slots=True)
class ClaimLine:
    """One service line of a submitted claim."""

    account_id: str
    """CLM01, the same patient control number the payer echoes as CLP01."""
    payer_id: str
    """NM1*PR's PI identifier, the loop's destination payer."""
    cpt: str
    """SV1-01's procedure code component, prefix (``HC``) stripped."""
    modifiers: tuple[str, ...]
    """SV1-01's components after the code."""
    charge: Decimal
    """SV1-02, the amount billed for this line."""
    units: Decimal
    """SV1-04."""
    service_date: date
    """DTP*472."""
    line_number: int
    """LX01."""
    source_ref: SourceRef


@dataclass(frozen=True, slots=True)
class Claim:
    """One professional claim, as submitted."""

    account_id: str
    """CLM01. The join key to the 835's CLP01 and to ``or_log.billing_account_id``."""
    payer_id: str
    payer_name: str
    total_charge: Decimal
    """CLM02."""
    diagnoses: tuple[str, ...]
    """HI codes, in element order, ICD-10-CM qualifier prefix stripped."""
    lines: tuple[ClaimLine, ...]
    submitted: date
    """BHT04: the date the transaction naming this claim was submitted."""
    source_ref: SourceRef
    frequency_code: str = "1"
    """CLM05-3, the claim frequency type code: ``"1"`` original (the
    default, for a claim that carries no CLM05 component at all -- every
    claim before decision 6, CONTRACT-SEEDS.md, was implicitly one of
    these), ``"7"`` replacement/corrected. See :func:`link_claims`."""

    @property
    def codes(self) -> tuple[str, ...]:
        """Distinct CPTs on this claim, in line order."""
        seen: set[str] = set()
        out: list[str] = []
        for line in self.lines:
            if line.cpt not in seen:
                seen.add(line.cpt)
                out.append(line.cpt)
        return tuple(out)


def parse_837(text: str, filename: str, sha256: str) -> tuple[Claim, ...]:
    """Parse an 837P into one :class:`Claim` per subscriber (2000B) loop."""
    out: list[Claim] = []

    submitted = date(1900, 1, 1)
    payer_id = ""
    payer_name = ""

    claim_open = False
    account = ""
    total_charge = Decimal(0)
    frequency_code = "1"
    diagnoses: list[str] = []
    claim_ref: SourceRef | None = None
    lines: list[ClaimLine] = []

    line_open = False
    line_number = 0
    cpt = ""
    modifiers: tuple[str, ...] = ()
    charge = Decimal(0)
    units = Decimal(0)
    svc_date: date | None = None
    line_ref: SourceRef | None = None

    def flush_line() -> None:
        nonlocal line_open, svc_date, line_ref
        if not line_open:
            return
        if svc_date is None:
            msg = f"{filename}: claim {account} line {line_number} carries no DTP*472 service date"
            raise ClaimsError(msg)
        assert line_ref is not None
        lines.append(
            ClaimLine(
                account_id=account,
                payer_id=payer_id,
                cpt=cpt,
                modifiers=modifiers,
                charge=charge,
                units=units,
                service_date=svc_date,
                line_number=line_number,
                source_ref=line_ref,
            )
        )
        line_open = False
        svc_date = None
        line_ref = None

    def flush_claim() -> None:
        nonlocal claim_open, claim_ref, lines, diagnoses, frequency_code
        flush_line()
        if not claim_open:
            return
        if not lines:
            msg = f"{filename}: claim {account} carries no SV1 service lines"
            raise ClaimsError(msg)
        assert claim_ref is not None
        out.append(
            Claim(
                account_id=account,
                payer_id=payer_id,
                payer_name=payer_name,
                total_charge=total_charge,
                diagnoses=tuple(diagnoses),
                lines=tuple(lines),
                submitted=submitted,
                source_ref=claim_ref,
                frequency_code=frequency_code,
            )
        )
        claim_open = False
        claim_ref = None
        lines = []
        diagnoses = []
        frequency_code = "1"

    for seg in segments(text):
        if seg.tag == "BHT":
            submitted = _x12_date(seg.get(4), f"{filename}:{seg.index} BHT04")
        elif seg.tag == "NM1" and seg.get(1) == "PR":
            payer_name = seg.get(3)
            payer_id = seg.get(9) if seg.get(8) == "PI" else ""
        elif seg.tag == "CLM":
            flush_claim()
            claim_open = True
            account = seg.get(1)
            total_charge = _money(seg.get(2), f"{filename}:{seg.index} CLM02")
            frequency_code = seg.component(5, 3) or "1"
            claim_ref = seg.ref(filename, sha256, 1)
        elif seg.tag == "HI" and claim_open:
            for position in range(1, len(seg.elements) + 1):
                code = seg.component(position, 2)
                if code:
                    diagnoses.append(code)
        elif seg.tag == "LX" and claim_open:
            flush_line()
            line_open = True
            line_number = _line_number(seg.get(1), f"{filename}:{seg.index} LX01")
        elif seg.tag == "SV1" and line_open:
            cpt = seg.component(1, 2)
            modifiers = tuple(p for p in seg.get(1).split(COMPONENT_SEPARATOR)[2:] if p)
            charge = _money(seg.get(2), f"{filename}:{seg.index} SV102")
            units = _money(seg.get(4), f"{filename}:{seg.index} SV104")
            line_ref = seg.ref(filename, sha256, 1)
        elif seg.tag == "DTP" and seg.get(1) == "472" and line_open:
            svc_date = _x12_date(seg.get(3), f"{filename}:{seg.index} DTP03")
    flush_claim()

    if not out:
        msg = f"{filename}: no claims found"
        raise ClaimsError(msg)
    return tuple(out)


def read_837(path: Path) -> tuple[Claim, ...]:
    """Read every claim of one 837P file."""
    sha = sha256_file(path)
    return parse_837(path.read_text(encoding="utf-8"), path.name, sha)


def read_directory(directory: Path) -> tuple[Claim, ...]:
    """Read every ``.edi`` file in a directory, in sorted order.

    Unlike the 835 reader, a missing or empty claims directory is not an
    error: a partner extract that has not started delivering the 837 side yet
    still has a complete clinical and remittance record, and Method 1 simply
    falls back to the OR log panel for "submitted" until claims arrive.
    """
    if not directory.is_dir():
        return ()
    out: list[Claim] = []
    for path in sorted(directory.glob("*.edi")):
        out.extend(read_837(path))
    return tuple(out)


def link_claims(encounters: tuple[Encounter, ...], claims: tuple[Claim, ...]) -> dict[str, Claim]:
    """Map each encounter to the claim of record for its billing account.

    Keyed by ``billing_account_id``, the same join key the 835 side uses.
    Two claims can legitimately share an account: an accidental duplicate
    submission, where the earliest-submitted one wins on the theory that a
    resubmission usually repeats rather than reduces the original code list
    and the first submission is what the documented-vs-submitted
    cross-check should judge the operative note against; and a genuine
    replacement (``frequency_code == "7"``, decision 6, CONTRACT-SEEDS.md),
    which explicitly supersedes whatever was on file regardless of
    submission order -- that is what the frequency code is *for*. Between
    two replacements for the same account, the later-submitted one is the
    one still in force.
    """
    by_account: dict[str, Claim] = {}
    for claim in claims:
        existing = by_account.get(claim.account_id)
        if existing is None:
            by_account[claim.account_id] = claim
            continue
        replaces = claim.frequency_code == "7"
        replaced = existing.frequency_code == "7"
        if replaces and not replaced:
            by_account[claim.account_id] = claim
        elif replaced and not replaces:
            continue
        elif replaces and replaced:
            if claim.submitted > existing.submitted:
                by_account[claim.account_id] = claim
        elif claim.submitted < existing.submitted:
            by_account[claim.account_id] = claim
    return {
        encounter.encounter_id: by_account[encounter.account_id]
        for encounter in encounters
        if encounter.account_id in by_account
    }
