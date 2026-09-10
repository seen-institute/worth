"""Shared ANSI X12 835/837 writers for the synthetic generators.

``write_remittance`` and ``write_claims`` were originally the surgical
generator's own functions (``make_synthetic_dataset.py``); the visit
generator already imported them unchanged, because both functions read their
encounter argument by attribute only (``svc_date``, ``payer``, ``cpts``,
``account``, ``pat_id``, ``csn``, ``dx``, ``allowed``, ``billed``) and never
touch anything surgical-specific. They live here now so that fact is
structural rather than a docstring promise: any class whose per-encounter
record satisfies :class:`EdiEncounter` can hand its cohort to these two
functions and get byte-identical 835/837 output to the surgical and visit
fixtures'.

The episode generator's writers are *not* here. They are a deliberately
retargeted copy (see ``episode.py``'s own module docstring for why: its
``Episode`` record does not satisfy this protocol without constructing fake
attributes), and unifying them carries real risk to the byte-identical
regeneration guarantee for no behavioural gain, so that divergence is kept
rather than forced closed.

``PAYERS`` (four payers, the same identities and contracted multiples used
by every class) lives here too, imported rather than redeclared by each
generator.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Protocol

FACILITY_NPI = "1234567893"
PAYEE_NAME = "MOUNT SINAI FACULTY PRACTICE"
PAYEE_TIN = "133948641"


@dataclass(frozen=True)
class Payer:
    payer_id: str
    name: str
    addr: str
    city_state_zip: str
    claim_filing: str  # CLP06: 12 commercial, MB Medicare B, MC Medicaid
    multiplier: Decimal  # contracted rate as a multiple of the Medicare-like base
    coinsurance: Decimal  # share of allowed billed to the patient


PAYERS = (
    Payer(
        "87726",
        "UNITED HEALTHCARE INSURANCE COMPANY",
        "PO BOX 30555",
        "SALT LAKE CITY*UT*841300555",
        "12",
        Decimal("2.05"),
        Decimal("0.00"),
    ),
    Payer(
        "60054",
        "AETNA HEALTH INC",
        "PO BOX 981106",
        "EL PASO*TX*799981106",
        "12",
        Decimal("1.82"),
        Decimal("0.00"),
    ),
    Payer(
        "04412",
        "NGS MEDICARE PART B",
        "PO BOX 6704",
        "FARGO*ND*581086704",
        "MB",
        Decimal("1.00"),
        Decimal("0.20"),
    ),
    Payer(
        "80141",
        "HEALTHFIRST PHSP INC",
        "PO BOX 5165",
        "NEW YORK*NY*100085165",
        "MC",
        Decimal("0.88"),
        Decimal("0.00"),
    ),
)


class EdiEncounter(Protocol):
    """The attribute set ``write_remittance``/``write_claims`` read. Any
    per-encounter record with these fields -- surgical's ``Encounter``,
    visit's ``VisitRecord``, or a future class's own record -- can be handed
    to either writer."""

    svc_date: date
    payer: Payer
    cpts: list[tuple[str, str]]
    account: str
    pat_id: str
    csn: str
    dx: list[str]
    allowed: dict[str, Decimal]
    billed: dict[str, Decimal]


def write_remittance(out: Path, encs: Sequence[EdiEncounter]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    groups: dict[tuple[str, str], list[EdiEncounter]] = {}
    for e in encs:
        groups.setdefault((e.payer.payer_id, e.svc_date.strftime("%Y%m")), []).append(e)

    for control, (_key, members) in enumerate(sorted(groups.items()), start=101):
        payer = members[0].payer
        pay_date = max(e.svc_date for e in members) + timedelta(days=21)
        total = sum(
            (e.allowed[cpt] * (Decimal(1) - payer.coinsurance)).quantize(Decimal("0.01"))
            for e in members
            for cpt, _ in e.cpts
        )
        body: list[str] = [
            f"ST*835*{control:04d}~",
            f"BPR*I*{total:.2f}*C*ACH*CCP*01*021000021*DA*9876543210*1{PAYEE_TIN}**01*"
            f"021000089*DA*0011223344*{pay_date.strftime('%Y%m%d')}~",
            f"TRN*1*ERA{pay_date.strftime('%y%m%d')}{control}*1{PAYEE_TIN}~",
            f"DTM*405*{pay_date.strftime('%Y%m%d')}~",
            f"N1*PR*{payer.name}~",
            f"N3*{payer.addr}~",
            f"N4*{payer.city_state_zip}~",
            f"REF*2U*{payer.payer_id}~",
            f"N1*PE*{PAYEE_NAME}*XX*{FACILITY_NPI}~",
            f"REF*TJ*{PAYEE_TIN}~",
            "LX*1~",
        ]
        for seq, e in enumerate(sorted(members, key=lambda x: x.account), start=1):
            claim_allowed = sum(e.allowed[cpt] for cpt, _ in e.cpts)
            patient = (claim_allowed * payer.coinsurance).quantize(Decimal("0.01"))
            claim_paid = claim_allowed - patient
            billed_total = Decimal(0)
            lines: list[str] = []
            for cpt, mod in e.cpts:
                allowed = e.allowed[cpt]
                billed = (max(allowed, Decimal("250.00")) * Decimal("3.2")).quantize(
                    Decimal("0.01")
                )
                billed_total += billed
                e.billed[cpt] = billed
                composite = f"HC:{cpt}" + (f":{mod}" if mod else "")
                line_patient = (allowed * payer.coinsurance).quantize(Decimal("0.01"))
                lines.append(f"SVC*{composite}*{billed:.2f}*{allowed - line_patient:.2f}**1~")
                lines.append(f"DTM*472*{e.svc_date.strftime('%Y%m%d')}~")
                lines.append(f"AMT*B6*{allowed:.2f}~")
                if allowed == 0:
                    lines.append(f"CAS*CO*97*{billed:.2f}~")
                    lines.append("LQ*HE*N19~")
                else:
                    lines.append(f"CAS*CO*45*{billed - allowed:.2f}~")
                    if line_patient > 0:
                        lines.append(f"CAS*PR*2*{line_patient:.2f}~")
            body.append(
                f"CLP*{e.account}*1*{billed_total:.2f}*{claim_paid:.2f}*{patient:.2f}*"
                f"{payer.claim_filing}*{pay_date.strftime('%Y')}{control}{seq:05d}*11*1~"
            )
            body.append(f"NM1*QC*1*PATIENT*SAMPLE****MI*{e.pat_id}~")
            body.append(f"REF*EA*{e.csn}~")
            body.append(f"DTM*232*{e.svc_date.strftime('%Y%m%d')}~")
            body.extend(lines)

        body.append(f"SE*{len(body) + 1:04d}*{control:04d}~")
        header = [
            f"ISA*00*          *00*          *ZZ*{payer.payer_id:<15}*ZZ*{PAYEE_TIN:<15}*"
            f"{pay_date.strftime('%y%m%d')}*0800*^*00501*{control:09d}*0*P*:~",
            f"GS*HP*{payer.payer_id}*{PAYEE_TIN}*{pay_date.strftime('%Y%m%d')}*0800*"
            f"{control}*X*005010X221A1~",
        ]
        trailer = [f"GE*1*{control}~", f"IEA*1*{control:09d}~"]
        name = f"835_{pay_date.strftime('%Y%m%d')}_{payer.payer_id}_{control}.edi"
        (out / name).write_text("\n".join([*header, *body, *trailer]) + "\n", encoding="utf-8")


def _edi_seg(*fields: str) -> str:
    """Join fields with the element separator and terminate the segment.

    Used only by ``write_claims``: doing the joining mechanically, rather than
    hand-counting asterisks the way ``write_remittance`` does, is worth it
    once a segment has nine positional elements and most of them are blank.
    """
    return "*".join(fields) + "~"


def write_claims(out: Path, encs: Sequence[EdiEncounter]) -> None:
    """Write the ANSI X12 837P professional claims behind each 835 remittance.

    Grouped exactly the way ``write_remittance`` groups its claims -- one file
    per payer per month -- so every CLP in a remittance has exactly one CLM
    here and vice versa, joined on ``CLM01 == CLP01 == or_log.billing_account_id``.
    Every fact on the claim is one already established elsewhere in the
    generator: the service lines, CPT codes and modifiers are
    ``or_log_proc.txt``'s; the per-line and claim-total charges are the exact
    ``Decimal`` values captured in ``EdiEncounter.billed`` when the matching 835
    was written (so CLM02 and CLP03, and each SV1 line and its SVC line,
    cannot drift apart); the diagnoses are ``encounter_dx.txt``'s; the payer,
    billing provider and subscriber identifiers are the 835 side's.

    No address or contact detail is invented beyond what the 835 side already
    fabricates: loops that would need one -- 1000A's submitter PER, 2010AA's
    billing-provider N3/N4 -- are omitted rather than filled with something
    that isn't in the dataset anywhere else.

    Claims for a payer's month are submitted a few days after the last
    surgery in it, which is always well before that batch's 835 pays (21 days
    after the same date), so submission precedes payment as it must.
    """
    out.mkdir(parents=True, exist_ok=True)
    groups: dict[tuple[str, str], list[EdiEncounter]] = {}
    for e in encs:
        groups.setdefault((e.payer.payer_id, e.svc_date.strftime("%Y%m")), []).append(e)

    seg = _edi_seg
    for control, (_key, members) in enumerate(sorted(groups.items()), start=101):
        payer = members[0].payer
        pay_date = max(e.svc_date for e in members) + timedelta(days=21)
        submit_date = max(e.svc_date for e in members) + timedelta(days=5)
        assert submit_date < pay_date

        body: list[str] = [
            seg("ST", "837", f"{control:04d}", "005010X222A1"),
            seg(
                "BHT",
                "0019",
                "00",
                f"{control:04d}",
                submit_date.strftime("%Y%m%d"),
                "0800",
                "CH",
            ),
            seg("NM1", "41", "2", PAYEE_NAME, "", "", "", "", "46", PAYEE_TIN),
            seg("NM1", "40", "2", payer.name, "", "", "", "", "46", payer.payer_id),
            seg("HL", "1", "", "20", "1"),
            seg("NM1", "85", "2", PAYEE_NAME, "", "", "", "", "XX", FACILITY_NPI),
            seg("REF", "EI", PAYEE_TIN),
        ]

        for n, e in enumerate(sorted(members, key=lambda x: x.account), start=2):
            body.append(seg("HL", str(n), "1", "22", "0"))
            body.append(seg("SBR", "P", "18", "", "", "", "", "", "", payer.claim_filing))
            body.append(seg("NM1", "IL", "1", "PATIENT", "SAMPLE", "", "", "", "MI", e.pat_id))
            body.append(seg("NM1", "PR", "2", payer.name, "", "", "", "", "PI", payer.payer_id))

            total_charge = sum((e.billed[cpt] for cpt, _ in e.cpts), start=Decimal(0))
            body.append(
                seg(
                    "CLM",
                    e.account,
                    f"{total_charge:.2f}",
                    "",
                    "",
                    "11:B:1",  # place of service, facility code qualifier, claim frequency:
                    # the 835 side hardcodes facility type 11 on every claim (CLP08), so this
                    # does too, for consistency with it rather than with the surgery's actual
                    # inpatient/outpatient status.
                    "Y",
                    "A",
                    "Y",
                    "Y",
                )
            )

            dx_composites = [f"ABK:{e.dx[0]}", *(f"ABF:{code}" for code in e.dx[1:])]
            body.append(seg("HI", *dx_composites))

            for line, (cpt, mod) in enumerate(e.cpts, start=1):
                composite = ":".join(["HC", cpt, *([mod] if mod else [])])
                body.append(seg("LX", str(line)))
                body.append(seg("SV1", composite, f"{e.billed[cpt]:.2f}", "UN", "1", "", "", "1"))
                body.append(seg("DTP", "472", "D8", e.svc_date.strftime("%Y%m%d")))

        body.append(seg("SE", f"{len(body) + 1:04d}", f"{control:04d}"))
        header = [
            f"ISA*00*          *00*          *ZZ*{PAYEE_TIN:<15}*ZZ*{payer.payer_id:<15}*"
            f"{submit_date.strftime('%y%m%d')}*0800*^*00501*{control:09d}*0*P*:~",
            f"GS*HC*{PAYEE_TIN}*{payer.payer_id}*{submit_date.strftime('%Y%m%d')}*0800*"
            f"{control}*X*005010X222A1~",
        ]
        trailer = [f"GE*1*{control}~", f"IEA*1*{control:09d}~"]
        name = f"837_{submit_date.strftime('%Y%m%d')}_{payer.payer_id}_{control}.edi"
        (out / name).write_text("\n".join([*header, *body, *trailer]) + "\n", encoding="utf-8")
