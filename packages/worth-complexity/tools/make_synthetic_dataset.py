"""Generate the Mount Sinai synthetic dataset.

This writes a fabricated but internally consistent partner extract: a surgical
log in the shape Epic Clarity produces, the ANSI X12 835 remittances that pay
for it, and the ANSI X12 837P professional claims that were submitted to earn
those remittances. It exists so the Layer A scorer, the linkage, the reference
curve and the adequacy ratio can all be exercised end to end before any real
data arrives.

The 837s are not read by the pipeline yet. They exist because a partner would
deliver the complete linked record, not just the money side, and because
Method 1 needs them: it cross-checks documented operative steps against the
submitted claim's code list, and payer friction is measured by comparing what
was submitted (837) with what was allowed, denied or downcoded (835). Every
CLP in a remittance has exactly one CLM in a claims file, joined on
``CLM01 == CLP01 == or_log.billing_account_id``, with the same service lines,
modifiers and charges on both sides.

Read this file before trusting any number that comes out of the dataset. The
generative assumptions are stated here explicitly, and they are the reason the
demo produces the result it does:

  * Every payer pays a fixed multiple of the Medicare Physician Fee Schedule.
    A comparator encounter's allowed amount is that multiple times the real PFS
    amount for its code, in the institution's locality, at the vintage in force
    on the service date. Commercial contracts really are written this way, and
    it is what lets the pipeline recover each payer's multiplier from the
    comparator cohort alone.

  * The fee schedule's own complexity relation is whatever a line through the
    comparator cohort's (Layer A score, PFS amount) pairs says it is. The
    generator fits that line with the pipeline's own least squares, at the
    reference vintage, and does not assert a slope of its own.

  * The five benign-GYN study codes are paid GYN_VALUATION_FACTOR of what that
    line pays for their mean complexity, times the payer's multiple, and each
    code pays one flat amount regardless of how hard the individual case was.
    That is the phenomenon under study: a code that does not differentiate.

Because those assumptions are *put in*, the adequacy ratio that comes out is a
property of this file, not evidence about American healthcare. What the
dataset validates is the pipeline: that a score, a linkage, a priced cohort, a
fitted curve and a ratio can be computed reproducibly from files of this shape.

Deterministic: a fixed seed, so regenerating produces byte-identical output.
"""

from __future__ import annotations

import random
import shutil
import zlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

SEED = 20260902
FACILITY_NPI = "1234567893"
PAYEE_NAME = "MOUNT SINAI FACULTY PRACTICE"
PAYEE_TIN = "133948641"

# Where the institution is, for the fee schedule: Medicare locality NY01,
# Manhattan. Every case is an OR case, so the facility setting.
LOCALITY = "NY01"
SETTING = "facility"

# The vintage the fee schedule's complexity relation is fitted at. Each
# encounter's own amount is priced at the vintage in force on its service date.
REFERENCE_VINTAGE = (2026, 4)

# What the study codes are paid, as a fraction of what the schedule's
# complexity relation pays at their mean score.
GYN_VALUATION_FACTOR = Decimal("0.70")


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


@dataclass(frozen=True)
class Code:
    cpt: str
    service: str
    specialty: str
    cohort: str  # "study" or "comparator"
    n_cases: int
    minutes: tuple[int, int]  # incision-to-close range across the difficulty draw
    ebl: tuple[int, int]
    difficulty: tuple[float, float]
    secondary_cpt: str | None = None  # add-on billed on the hardest cases


# Five benign-GYN study codes. 58662 deliberately spans the widest difficulty
# range of any code here: it is the code the methodology's worked example uses,
# and the point is that one code absorbs a 90-minute ablation and a five-hour
# transmural excision.
STUDY = (
    Code("58558", "GYN", "Obstetrics & Gynecology", "study", 6, (18, 55), (5, 60), (0.02, 0.30)),
    Code("58563", "GYN", "Obstetrics & Gynecology", "study", 6, (25, 70), (10, 90), (0.06, 0.36)),
    Code("58661", "GYN", "Obstetrics & Gynecology", "study", 6, (55, 190), (40, 350), (0.18, 0.62)),
    Code(
        "58662",
        "GYN",
        "Obstetrics & Gynecology",
        "study",
        6,
        (75, 305),
        (25, 700),
        (0.16, 0.94),
        "58660",
    ),
    Code("58570", "GYN", "Obstetrics & Gynecology", "study", 6, (95, 250), (75, 500), (0.30, 0.78)),
)

# Comparator specialties: general surgery, orthopaedics, urology. Eight codes
# spanning the same complexity range as the study cohort, each occupying a
# narrow band of it, which is exactly the property the study codes lack.
COMPARATOR = (
    Code(
        "29881", "ORTHO", "Orthopaedic Surgery", "comparator", 12, (28, 62), (5, 40), (0.08, 0.24)
    ),
    Code("49505", "GENSURG", "General Surgery", "comparator", 12, (42, 85), (10, 70), (0.14, 0.32)),
    Code(
        "44970", "GENSURG", "General Surgery", "comparator", 12, (48, 100), (10, 90), (0.20, 0.38)
    ),
    Code(
        "47562", "GENSURG", "General Surgery", "comparator", 12, (55, 120), (15, 120), (0.26, 0.46)
    ),
    Code("52601", "URO", "Urology", "comparator", 12, (70, 150), (60, 350), (0.36, 0.56)),
    Code(
        "27447",
        "ORTHO",
        "Orthopaedic Surgery",
        "comparator",
        12,
        (95, 195),
        (150, 600),
        (0.52, 0.72),
    ),
    Code("50543", "URO", "Urology", "comparator", 12, (145, 265), (150, 750), (0.64, 0.84)),
    Code("55866", "URO", "Urology", "comparator", 12, (170, 300), (200, 900), (0.72, 0.94)),
)

ALL_CODES = STUDY + COMPARATOR

DX_BY_SERVICE = {
    "GYN": [
        "N80.531",
        "N80.03",
        "N80.111",
        "N94.6",
        "D25.1",
        "N92.0",
        "N83.201",
        "K66.0",
        "N13.5",
        "E66.01",
        "D50.0",
        "I10",
        "E11.9",
        "F41.1",
    ],
    "GENSURG": ["K80.20", "K35.80", "K40.90", "K21.9", "E66.01", "I10", "E11.9", "Z79.4"],
    "ORTHO": ["M17.11", "M23.221", "M25.561", "M79.604", "E66.01", "I10", "E11.9", "M81.0"],
    "URO": ["N40.1", "C61", "D41.01", "N20.0", "R31.9", "I10", "E11.9", "N18.3"],
}


@dataclass
class Encounter:
    log_id: str
    csn: str
    pat_id: str
    account: str
    svc_date: date
    code: Code
    payer: Payer
    minutes: int
    anes_pre: int
    anes_post: int
    asa: int
    specialties: int
    assistants: int
    ebl: int
    inpatient: bool
    dx: list[str]
    cpts: list[tuple[str, str]]  # (cpt, modifier)
    difficulty: float  # the draw the whole encounter derives from
    start_hour: int
    start_min: int
    surgeon: str
    room: str
    allowed: dict[str, Decimal]  # cpt -> allowed amount
    billed: dict[str, Decimal] = field(default_factory=dict)  # cpt -> submitted charge, set
    # when the 835 is written, and reused verbatim by the 837 so the two sides
    # of the same claim never drift apart by so much as a cent.


def lerp(lo: float, hi: float, t: float) -> float:
    return lo + (hi - lo) * t


PERIOD_START = date(2026, 1, 5)
PERIOD_DAYS = 360
"""One calendar year, CY2026, so every service date falls inside a pinned CMS
vintage. The partner brief asks for two years; that needs CY2024 and CY2025
releases pinned in worth-fees, which waits on a real extract's dates."""


def build_encounters(
    rng: random.Random, study_scale: int = 1, comparator_scale: int = 1
) -> list[Encounter]:
    """Draw the cohort.

    The two cohorts scale independently. Method 0 needs a fitted line per code
    per payer and the suppression floor is eleven encounters, so a study cohort
    that can demonstrate it is several times the size of the comparator cohort
    that only has to anchor one curve per payer.
    """
    out: list[Encounter] = []
    seq = 0
    surgeons = {
        "GYN": ["S00817", "S01244", "S01402"],
        "GENSURG": ["S02901", "S02955"],
        "ORTHO": ["S04120", "S04188"],
        "URO": ["S03318", "S03370"],
    }
    for code in ALL_CODES:
        scale = study_scale if code.cohort == "study" else comparator_scale
        for _ in range(code.n_cases * scale):
            seq += 1
            d = rng.uniform(*code.difficulty)
            minutes = round(lerp(*code.minutes, d) * rng.uniform(0.94, 1.06))
            ebl = round(lerp(*code.ebl, d) * rng.uniform(0.85, 1.15) / 5) * 5
            asa = max(1, min(4, 1 + round(3.0 * d + rng.uniform(-0.35, 0.35))))
            specialties = 1 + (1 if d > 0.62 else 0) + (1 if d > 0.85 else 0)
            assistants = (1 if d > 0.44 else 0) + (1 if d > 0.74 else 0) + (1 if d > 0.90 else 0)
            inpatient = d > 0.50
            n_dx = max(1, min(8, 1 + round(6.0 * d + rng.uniform(-0.8, 0.8))))
            pool = DX_BY_SERVICE[code.service]
            dx = [pool[0], *rng.sample(pool[1:], k=min(n_dx - 1, len(pool) - 1))]

            cpts: list[tuple[str, str]] = [(code.cpt, "22" if d > 0.70 else "")]
            if code.secondary_cpt is not None and d > 0.66:
                cpts.append((code.secondary_cpt, ""))

            # Spread across the whole window rather than walking forward code by
            # code: a published record names the period it covers, and a code
            # confined to three consecutive weeks would misstate it.
            day = PERIOD_START + timedelta(days=rng.randrange(PERIOD_DAYS))
            out.append(
                Encounter(
                    log_id=f"88{200000 + seq * 137:06d}",
                    csn=f"10{32000000 + seq * 4409:08d}",
                    pat_id=f"Z{4000 + seq * 7:07d}",
                    account=f"A7{100000 + seq * 293:06d}",
                    svc_date=day,
                    code=code,
                    payer=rng.choice(PAYERS),
                    minutes=minutes,
                    anes_pre=rng.randint(14, 26),
                    anes_post=rng.randint(9, 20),
                    asa=asa,
                    specialties=specialties,
                    assistants=assistants,
                    ebl=ebl,
                    inpatient=inpatient,
                    dx=dx,
                    cpts=cpts,
                    difficulty=d,
                    start_hour=rng.choice([7, 7, 8, 9, 11, 12, 13, 14]),
                    start_min=rng.choice([0, 5, 10, 15, 20, 25, 35, 40, 50]),
                    surgeon=rng.choice(surgeons[code.service]),
                    room=f"MSH-OR-{rng.randint(2, 18):02d}",
                    allowed={},
                )
            )
    out.sort(key=lambda e: (e.svc_date, e.log_id))
    return out


EXTRA_SPECIALTIES = {
    "GYN": ["Colon & Rectal Surgery", "Urology"],
    "GENSURG": ["Urology", "Vascular Surgery"],
    "ORTHO": ["Plastic Surgery", "Vascular Surgery"],
    "URO": ["General Surgery", "Colon & Rectal Surgery"],
}
COUNTIES = [
    ("10029", "New York"),
    ("11215", "Kings"),
    ("10456", "Bronx"),
    ("11106", "Queens"),
    ("10002", "New York"),
    ("10314", "Richmond"),
]


def stamp(d: date, hour: int, minute: int, plus: int = 0) -> str:
    total = hour * 60 + minute + plus
    return f"{d.strftime('%m/%d/%Y')} {total // 60:02d}:{total % 60:02d}:00"


def write_clinical(out: Path, encs: list[Encounter], rng: random.Random) -> None:
    out.mkdir(parents=True, exist_ok=True)

    log = [
        "log_id|csn|pat_id|billing_account_id|surgery_date|facility_npi|room|service|"
        "specialty|patient_class|in_room_dttm|anes_start_dttm|procedure_start_dttm|"
        "procedure_close_dttm|anes_end_dttm|out_room_dttm|asa_class|asa_emergency_yn|"
        "ebl_ml|case_class|primary_surgeon_id|scheduled_minutes"
    ]
    proc = ["log_id|line|cpt|mod1|mod2|laterality|primary_yn|surgeon_id"]
    staff = ["log_id|provider_id|role|specialty|panel_seq"]
    dx = ["csn|icd10|seq|poa_yn"]
    pat = ["pat_id|birth_date|sex|zip5|county"]

    for e in encs:
        h, m = e.start_hour, e.start_min
        log.append(
            "|".join(
                [
                    e.log_id,
                    e.csn,
                    e.pat_id,
                    e.account,
                    f"{e.svc_date.strftime('%m/%d/%Y')} 00:00:00",
                    FACILITY_NPI,
                    e.room,
                    e.code.service,
                    e.code.specialty,
                    "Inpatient" if e.inpatient else "Outpatient",
                    stamp(e.svc_date, h, m),
                    stamp(e.svc_date, h, m, 5),
                    stamp(e.svc_date, h, m, e.anes_pre),
                    stamp(e.svc_date, h, m, e.anes_pre + e.minutes),
                    stamp(e.svc_date, h, m, e.anes_pre + e.minutes + 8),
                    stamp(e.svc_date, h, m, e.anes_pre + e.minutes + e.anes_post),
                    str(e.asa),
                    "N",
                    str(e.ebl),
                    "Elective",
                    e.surgeon,
                    str(round(e.minutes / 15) * 15),
                ]
            )
        )

        for i, (cpt, mod) in enumerate(e.cpts, start=1):
            proc.append(
                "|".join(
                    [
                        e.log_id,
                        str(i),
                        cpt,
                        mod,
                        "",
                        "BILATERAL" if e.code.service == "GYN" and i == 1 else "N/A",
                        "Y" if i == 1 else "N",
                        e.surgeon,
                    ]
                )
            )

        staff.append(f"{e.log_id}|{e.surgeon}|Primary Surgeon|{e.code.specialty}|1")
        extras = EXTRA_SPECIALTIES[e.code.service]
        for i in range(e.assistants):
            spec = extras[i] if i < e.specialties - 1 else e.code.specialty
            # Python's str hash is salted per process, so it cannot be used here without
            # breaking the byte-identical regeneration this file promises; crc32 is a
            # plain deterministic function of the bytes, not of the process.
            offset = zlib.crc32(e.log_id.encode()) % 90
            staff.append(
                f"{e.log_id}|S{9000 + i * 37 + offset:05d}|Assistant Surgeon|{spec}|{i + 2}"
            )
        staff.append(f"{e.log_id}|A{rng.randint(400, 599):05d}|Anesthesiologist|Anesthesiology|")
        staff.append(f"{e.log_id}|N{rng.randint(1000, 1199):05d}|Circulating Nurse|Nursing|")

        for i, code in enumerate(e.dx, start=1):
            dx.append(f"{e.csn}|{code}|{i}|Y")

        zip5, county = rng.choice(COUNTIES)
        sex = "F" if e.code.service == "GYN" else rng.choice(["F", "M"])
        if e.code.cpt in {"55866", "52601"}:
            sex = "M"
        birth = date(e.svc_date.year - rng.randint(24, 71), rng.randint(1, 12), 1)
        pat.append(f"{e.pat_id}|{birth.strftime('%m/%Y')}|{sex}|{zip5}|{county}")

    for name, rows in [
        ("or_log.txt", log),
        ("or_log_proc.txt", proc),
        ("or_staff.txt", staff),
        ("encounter_dx.txt", dx),
        ("patient_lds.txt", pat),
    ]:
        (out / name).write_text("\n".join(rows) + "\n", encoding="utf-8")


def assign_payments(encs: list[Encounter], scores: dict[str, int], rng: random.Random) -> None:
    """Set each encounter's allowed amount from the fee schedule and its payer's multiple.

    Comparator encounters get the payer's multiple of the real PFS amount for
    their code at the vintage in force on the service date. Study codes get
    GYN_VALUATION_FACTOR of what the schedule's fitted complexity relation pays
    at the code's mean score, times the same multiple; within a code the amount
    does not vary with the individual case, that is the whole phenomenon.
    """
    from worth_complexity.curve import fit
    from worth_fees import expected_allowed
    from worth_fees.sources import vintage_for_date

    def pfs(cpt: str, year: int, quarter: int) -> Decimal:
        return expected_allowed(cpt, [], LOCALITY, SETTING, year, quarter).amount

    at_date: dict[str, Decimal] = {}
    points: list[tuple[Decimal, Decimal]] = []
    for e in encs:
        if e.code.cohort != "comparator":
            continue
        v = vintage_for_date(e.svc_date)
        at_date[e.log_id] = pfs(e.code.cpt, v.rule_year, v.quarter)
        points.append((Decimal(scores[e.log_id]), pfs(e.code.cpt, *REFERENCE_VINTAGE)))
    curve = fit("generator/schedule", tuple(points))

    by_code: dict[str, list[int]] = {}
    for e in encs:
        if e.code.cohort == "study":
            by_code.setdefault(e.code.cpt, []).append(scores[e.log_id])
    study_base: dict[str, Decimal] = {}
    for cpt, values in by_code.items():
        mean = Decimal(sum(values)) / Decimal(len(values))
        amount = curve.predict(mean, allow_extrapolation=True) * GYN_VALUATION_FACTOR
        study_base[cpt] = amount.quantize(Decimal("0.01"))

    for e in encs:
        base = at_date[e.log_id] if e.code.cohort == "comparator" else study_base[e.code.cpt]
        noise = Decimal(str(round(rng.uniform(0.985, 1.015), 4)))
        allowed = (base * e.payer.multiplier * noise).quantize(Decimal("0.01"))
        e.allowed[e.code.cpt] = allowed
        for cpt, _ in e.cpts[1:]:
            # Add-on codes on the hardest cases are submitted and bundled away.
            e.allowed[cpt] = Decimal("0.00")


def write_remittance(out: Path, encs: list[Encounter]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    groups: dict[tuple[str, str], list[Encounter]] = {}
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


def write_claims(out: Path, encs: list[Encounter]) -> None:
    """Write the ANSI X12 837P professional claims behind each 835 remittance.

    Grouped exactly the way ``write_remittance`` groups its claims -- one file
    per payer per month -- so every CLP in a remittance has exactly one CLM
    here and vice versa, joined on ``CLM01 == CLP01 == or_log.billing_account_id``.
    Every fact on the claim is one already established elsewhere in the
    generator: the service lines, CPT codes and modifiers are
    ``or_log_proc.txt``'s; the per-line and claim-total charges are the exact
    ``Decimal`` values captured in ``Encounter.billed`` when the matching 835
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
    groups: dict[tuple[str, str], list[Encounter]] = {}
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


def main() -> None:
    import sys

    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("mssm-synthetic")

    # Clear what this script owns before writing. Filenames encode payer, month
    # and control number, so a smaller regeneration leaves the larger one's files
    # behind and every account number in the overlap appears on two claims. The
    # linkage then joins one encounter to another encounter's money, and nothing
    # downstream can tell.
    for owned in ("clinical", "remittance", "claims"):
        shutil.rmtree(root / owned, ignore_errors=True)
    study_scale = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    comparator_scale = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    rng = random.Random(SEED)
    encs = build_encounters(rng, study_scale, comparator_scale)

    clinical = root / "clinical"
    write_clinical(clinical, encs, rng)
    write_notes(clinical, encs, {e.log_id: e.difficulty for e in encs})

    # Score with the real scorer, so the payment assumptions are expressed in
    # terms of the same instrument the pipeline will use.
    from worth_complexity import cases, markers, rulepack, scoring
    from worth_complexity.models import LAYER_A

    pack = rulepack.load()
    extract = cases.read_extract(clinical)
    encounters = cases.encounters(extract)
    marker_sets = markers.extract(extract, encounters, pack)
    scores = {
        e.encounter_id: scoring.score(e, marker_sets[e.encounter_id], pack, LAYER_A).score.value
        for e in encounters
    }

    assign_payments(encs, scores, rng)
    write_remittance(root / "remittance", encs)
    write_claims(root / "claims", encs)

    notes = len(list((clinical / "notes").glob("*.txt")))
    remits = len(list((root / "remittance").glob("*.edi")))
    claims = len(list((root / "claims").glob("*.edi")))
    study = [e for e in encs if e.code.cohort == "study"]
    print(f"{len(encs)} surgeries, {notes} notes -> {root}")
    print(f"  study      {len(study)} across {len({e.code.cpt for e in study})} codes")
    print(f"  comparator {len(encs) - len(study)}")
    print(f"  remittance {remits} 835 files, claims {claims} 837 files")
    print(f"  scores     {min(scores.values())}-{max(scores.values())}")


# ---------------------------------------------------------------------------
# The note dataset
#
# Layer A reads narrative as well as structured fields, so the dataset has to
# contain narrative worth reading. Each operative note states the same case the
# OR log describes, in prose, and carries two things a plain keyword search
# would get wrong:
#
#   * an INDICATION section naming organs from the patient's history, which the
#     rules must not count as work performed today, and
#   * a negated finding in FINDINGS, which the rules must not count at all.
#
# Both appear in every note, so section scope and negation are exercised by the
# whole dataset rather than by one hand-written test case.
# ---------------------------------------------------------------------------

ORGAN_SENTENCES = {
    "GYN": [
        "The rectosigmoid colon was involved by infiltrating disease along the anterior serosa.",
        "The left ureter was dissected free along its pelvic course and ureterolysis was "
        "performed.",
        "Disease was noted overlying the bladder peritoneum at the vesicouterine fold.",
        "Nodular implants were identified on the right pelvic sidewall.",
        "Omental tissue was mobilised off the anterior abdominal wall.",
        "Nodular disease was seen on the right hemidiaphragm.",
    ],
    "GENSURG": [
        "The small bowel was mobilised to expose the operative field.",
        "Omental tissue was taken down from the anterior abdominal wall.",
        "The appendix was inspected and found to be involved.",
        "The rectosigmoid colon was retracted to expose the pelvic brim.",
    ],
    "ORTHO": [
        "The joint capsule was opened and the compartment inspected.",
        "Periarticular osteophytes were resected sharply.",
        "The extensor mechanism was mobilised for exposure.",
        "The posterior capsule was released to correct the deformity.",
    ],
    "URO": [
        "The left ureter was identified and dissected free to the level of the crossing vessels.",
        "The bladder was mobilised at the vesical neck.",
        "The rectosigmoid colon was reflected medially to expose the retroperitoneum.",
        "Omental tissue was interposed over the repair.",
    ],
}

ADHESIONS = {
    3: "Dense pelvic adhesions were encountered, with an obliterated posterior cul-de-sac "
    "requiring extensive adhesiolysis.",
    2: "Moderate fibrous adhesions were encountered and adhesiolysis was performed sharply.",
    1: "Filmy adhesions were noted in the right adnexal region.",
}

EVENTS = [
    (
        0.60,
        "A second surgical service was consulted intraoperatively and assisted with the "
        "dissection.",
    ),
    (0.78, "A small serosal tear was identified and repaired primarily in two layers."),
    (0.90, "The patient was transfused two units of packed red blood cells."),
    (0.95, "The procedure was converted to an open laparotomy for improved exposure."),
]

INDICATION_DECOY = (
    "She has a history of appendectomy and a prior cystotomy repair, both without "
    "complication. There is no prior operative diagnosis on file."
)
FINDINGS_DECOY = "There was no evidence of diaphragmatic involvement."

PROCEDURE_TITLE = {
    "58558": "Hysteroscopy with dilation and curettage",
    "58563": "Hysteroscopy with endometrial ablation",
    "58570": "Laparoscopic total hysterectomy",
    "58661": "Laparoscopic removal of adnexal structures",
    "58662": "Laparoscopic excision of pelvic lesions",
    "29881": "Arthroscopic partial meniscectomy",
    "49505": "Open inguinal hernia repair",
    "44970": "Laparoscopic appendectomy",
    "47562": "Laparoscopic cholecystectomy",
    "52601": "Transurethral resection of the prostate",
    "27447": "Total knee arthroplasty",
    "50543": "Laparoscopic partial nephrectomy",
    "55866": "Laparoscopic radical prostatectomy",
}


def operative_note(e: Encounter, d: float) -> str:
    """One operative note for an encounter, at difficulty ``d``."""
    pool = ORGAN_SENTENCES[e.code.service]
    n_organs = sum(1 for cut in (0.34, 0.54, 0.71, 0.87) if d > cut)
    organs = pool[:n_organs]

    grade = 3 if d > 0.70 else 2 if d > 0.45 else 1 if d > 0.22 else 0
    adhesion = [ADHESIONS[grade]] if grade else []

    events = [text for cut, text in EVENTS if d > cut]

    findings = [
        f"{PROCEDURE_TITLE[e.code.cpt]} was undertaken.",
        *organs,
        *adhesion,
        FINDINGS_DECOY,
    ]
    steps = [
        "The patient was placed in the standard position and prepped and draped in the "
        "usual sterile fashion.",
        "Access was obtained and the abdomen inspected systematically.",
        *events,
        "Haemostasis was confirmed and instruments were removed under direct vision.",
    ]

    return "\n".join(
        [
            "MOUNT SINAI HEALTH SYSTEM",
            "OPERATIVE NOTE",
            "",
            f"PATIENT: {e.pat_id}",
            f"DATE OF SERVICE: {e.svc_date.strftime('%m/%d/%Y')}",
            f"SURGEON: {e.surgeon}",
            f"SERVICE: {e.code.service}",
            f"ASA CLASS: {e.asa}",
            "",
            "PREOPERATIVE DIAGNOSIS:",
            f"{e.dx[0]}",
            "",
            "PROCEDURE PERFORMED:",
            f"{PROCEDURE_TITLE[e.code.cpt]}.",
            "",
            "INDICATION:",
            INDICATION_DECOY,
            "",
            "FINDINGS:",
            " ".join(findings),
            "",
            "DESCRIPTION OF PROCEDURE:",
            " ".join(steps),
            "",
            f"ESTIMATED BLOOD LOSS: {e.ebl} mL",
            f"TOTAL OPERATIVE TIME: {e.minutes} minutes",
            f"COMPLICATIONS: {'As described above.' if events else 'None.'}",
            "SPECIMENS: Sent to pathology.",
            "DISPOSITION: The patient was taken to recovery in stable condition.",
            "",
        ]
    )


def discharge_summary(e: Encounter) -> str:
    """A short discharge summary. Inpatients only.

    Present so the dataset carries more than one document type and so the note-type
    scoping in the rule pack has something to exclude: these mention organs too,
    and no Layer A rule reads them.
    """
    return "\n".join(
        [
            "MOUNT SINAI HEALTH SYSTEM",
            "DISCHARGE SUMMARY",
            "",
            f"PATIENT: {e.pat_id}",
            f"DATE OF SERVICE: {e.svc_date.strftime('%m/%d/%Y')}",
            "",
            "HOSPITAL COURSE:",
            "The patient tolerated the procedure well. Bowel function returned on the first "
            "postoperative day and the bladder catheter was removed without difficulty.",
            "",
            "DISCHARGE DIAGNOSIS:",
            f"{e.dx[0]}",
            "",
            "DISCHARGE INSTRUCTIONS:",
            "Return for fever, worsening pain, or inability to tolerate oral intake.",
            "",
        ]
    )


def write_notes(out: Path, encs: list[Encounter], draws: dict[str, float]) -> None:
    """Write the note dataset and its index."""
    folder = out / "notes"
    folder.mkdir(parents=True, exist_ok=True)
    index = ["note_id|log_id|csn|note_type|service_date|author_role|filename"]

    for e in encs:
        for kind, body in (
            ("operative", operative_note(e, draws[e.log_id])),
            *((("discharge", discharge_summary(e)),) if e.inpatient else ()),
        ):
            note_id = f"N{e.log_id[2:]}{'O' if kind == 'operative' else 'D'}"
            filename = f"{note_id}.txt"
            (folder / filename).write_text(body, encoding="utf-8")
            index.append(
                "|".join(
                    [
                        note_id,
                        e.log_id,
                        e.csn,
                        kind,
                        f"{e.svc_date.strftime('%m/%d/%Y')} 00:00:00",
                        "Attending Surgeon" if kind == "operative" else "Attending Physician",
                        filename,
                    ]
                )
            )

    (out / "notes.txt").write_text("\n".join(index) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
