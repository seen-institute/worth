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
from typing import TYPE_CHECKING

from worth_complexity.synthetic.edi import (
    FACILITY_NPI,
    PAYERS,
    Payer,
    write_claims,
    write_remittance,
)

if TYPE_CHECKING:
    from worth_complexity.synthetic.scenarios import Scenario

SEED = 20260902

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
    site_npi: str = FACILITY_NPI
    """Which of the three facility NPIs this case was performed at
    (CONTRACT-SEEDS.md's ``multi-site`` scenario). Defaults to the single
    site every non-multi-site build writes, so ``build_fixture`` -- which
    never touches this field -- keeps writing exactly what it always has."""


def lerp(lo: float, hi: float, t: float) -> float:
    return lo + (hi - lo) * t


PERIOD_START = date(2026, 1, 5)
PERIOD_DAYS = 360
"""One calendar year, CY2026, so every service date falls inside a pinned CMS
vintage. The partner brief asks for two years; that needs CY2024 and CY2025
releases pinned in worth-fees, which waits on a real extract's dates."""

POLICY_PERIOD_START = date(2026, 1, 5)
POLICY_PERIOD_DAYS = 725
POLICY_MONTHS = 24
"""24 months, 2026-01 through 2027-12 (CONTRACT-SEEDS.md's ``policy-change``
scenario): service dates fall on both sides of ``Scenario.policy_date``
(2027-01-01). CY2027 has no CMS archive pinned in ``worth-fees`` yet -- see
:func:`worth_complexity.synthetic.pricing.vintage_for_date_clamped`, which
every scenario-aware pricing helper uses instead of ``vintage_for_date``
directly so a 2027 service date still prices rather than raising."""


def build_encounters(
    rng: random.Random,
    study_scale: int = 1,
    comparator_scale: int = 1,
    *,
    period_start: date = PERIOD_START,
    period_days: int = PERIOD_DAYS,
    comparator_period_start: date | None = None,
    comparator_period_days: int | None = None,
) -> list[Encounter]:
    """Draw the cohort.

    The two cohorts scale independently. Method 0 needs a fitted line per code
    per payer and the suppression floor is eleven encounters, so a study cohort
    that can demonstrate it is several times the size of the comparator cohort
    that only has to anchor one curve per payer.

    ``period_start``/``period_days``: the service-date window every *study*
    case's date is drawn from (and every comparator's too, when
    ``comparator_period_start``/``comparator_period_days`` are left
    ``None``). Default to the module's own one-year CY2026 window, so
    ``build_fixture`` (which never passes any of these) is unaffected; the
    ``policy-change`` scenario passes a 24-month window for the study
    cohort instead (CONTRACT-SEEDS.md's catalog), straddling its own policy
    date, but keeps the comparator cohort in the original one-year window
    (``comparator_period_start``/``comparator_period_days`` set explicitly)
    -- :func:`worth_complexity.adequacy.price_comparators` prices every
    comparator at the real, unclamped vintage in force on its own service
    date, which has no CY2027 fallback, so a comparator dated in 2027 would
    make a real ``pipeline.run`` over the fixture fail at ``price``; the
    curve Method 0/3 fits only needs comparator points, which do not need
    to span the policy date the way the study cohort does.
    """
    out: list[Encounter] = []
    seq = 0
    surgeons = {
        "GYN": ["S00817", "S01244", "S01402"],
        "GENSURG": ["S02901", "S02955"],
        "ORTHO": ["S04120", "S04188"],
        "URO": ["S03318", "S03370"],
    }
    comp_start = comparator_period_start if comparator_period_start is not None else period_start
    comp_days = comparator_period_days if comparator_period_days is not None else period_days
    for code in ALL_CODES:
        scale = study_scale if code.cohort == "study" else comparator_scale
        cohort_start = period_start if code.cohort == "study" else comp_start
        cohort_days = period_days if code.cohort == "study" else comp_days
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
            day = cohort_start + timedelta(days=rng.randrange(cohort_days))
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


def write_clinical(
    out: Path,
    encs: list[Encounter],
    rng: random.Random,
    *,
    blank_ebl: frozenset[str] = frozenset(),
) -> None:
    """``blank_ebl``: log ids whose ``ebl_ml`` is written blank instead of
    the drawn value -- site C's multi-site documentation gap
    (CONTRACT-SEEDS.md's catalog); ``markers.py`` already reads a blank
    ``ebl_ml`` as a missing ``estimated_blood_loss_ml`` marker rather than
    an extraction failure. Empty by default, so ``build_fixture`` (which
    never passes it) writes every case's real EBL, unchanged."""
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
                    e.site_npi,
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
                    "" if e.log_id in blank_ebl else str(e.ebl),
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
    from worth_fees import expected_allowed
    from worth_fees.sources import vintage_for_date

    from worth_complexity.curve import fit

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


def build_fixture(
    root: Path, study_scale: int = 1, comparator_scale: int = 1, *, seed: int = SEED
) -> None:
    """Build the committed mssm-synthetic fixture (or a scaled variant of its
    exact shape) into ``root``. This is the original generator's ``main()``
    body, parameterized instead of reading ``sys.argv`` -- the RNG call
    order is untouched, which is what keeps ``tests/test_synthetic_baseline.py``
    byte-identical to the committed fixture at ``study_scale=comparator_scale=1``,
    ``seed=SEED``. See :func:`build` for the scenario-aware entry point
    (CONTRACT-SEEDS.md's ``worth-cli synth``), which does not reuse this
    function's cohort-count constants."""
    # Clear what this script owns before writing. Filenames encode payer, month
    # and control number, so a smaller regeneration leaves the larger one's files
    # behind and every account number in the overlap appears on two claims. The
    # linkage then joins one encounter to another encounter's money, and nothing
    # downstream can tell.
    for owned in ("clinical", "remittance", "claims"):
        shutil.rmtree(root / owned, ignore_errors=True)
    rng = random.Random(seed)
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


def operative_note(e: Encounter, d: float, *, blank_ebl: bool = False) -> str:
    """One operative note for an encounter, at difficulty ``d``.

    ``blank_ebl``: write the ESTIMATED BLOOD LOSS line without a number --
    site C's multi-site documentation gap (CONTRACT-SEEDS.md's catalog).
    The rule pack's ``estimated_blood_loss_ml`` marker has a narrative
    fallback of its own (the numeric pattern this same line would otherwise
    match, ``"estimated blood loss"``/``"ebl"`` section), so blanking only
    ``or_log.txt``'s ``ebl_ml`` column (:func:`write_clinical`'s
    ``blank_ebl``) is not enough to make the marker genuinely missing --
    the note would still state it. Both sides have to be blank together for
    the same reason a real missing OR-log field usually means the note
    never captured it either."""
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
            "ESTIMATED BLOOD LOSS: not recorded"
            if blank_ebl
            else f"ESTIMATED BLOOD LOSS: {e.ebl} mL",
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


def write_notes(
    out: Path,
    encs: list[Encounter],
    draws: dict[str, float],
    *,
    skip_operative: frozenset[str] = frozenset(),
    blank_ebl: frozenset[str] = frozenset(),
) -> None:
    """Write the note dataset and its index.

    ``skip_operative``: log ids to write *no* operative note for at all --
    site B's multi-site documentation gap (CONTRACT-SEEDS.md's catalog).
    Withholding the whole note, rather than editing its language, is what
    makes the operative-note-only narrative markers (``adhesion_severity``,
    ``anatomic_extent``, and incidentally ``intraoperative_events``, which
    reads the same note type) genuinely absent for that encounter -- decision
    6's default-to-zero -- rather than a note that legitimately never
    mentions them. Empty by default, so ``build_fixture`` writes every
    encounter's operative note, unchanged.

    ``blank_ebl``: log ids whose operative note states no numeric estimated
    blood loss either -- site C's multi-site documentation gap. Should be
    the same set :func:`write_clinical`'s own ``blank_ebl`` is given: see
    :func:`operative_note`'s own docstring for why both sides need it.
    """
    folder = out / "notes"
    folder.mkdir(parents=True, exist_ok=True)
    index = ["note_id|log_id|csn|note_type|service_date|author_role|filename"]

    for e in encs:
        for kind, body in (
            *(
                (
                    (
                        "operative",
                        operative_note(e, draws[e.log_id], blank_ebl=e.log_id in blank_ebl),
                    ),
                )
                if e.log_id not in skip_operative
                else ()
            ),
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


# ---------------------------------------------------------------------------
# Scenario-aware entry point (CONTRACT-SEEDS.md, "worth track S1")
# ---------------------------------------------------------------------------


def _assign_payments_scenario(
    encs: list[Encounter], scores: dict[str, int], rng: random.Random, scenario: Scenario
) -> None:
    """:func:`assign_payments`, generalized to every payment rule the
    catalog names (:mod:`worth_complexity.synthetic.pricing`) instead of
    always ``flat-at-median-share``. Comparator pricing, the curve fit and
    the noise are unchanged from :func:`assign_payments`, except that the
    fee-schedule lookup is clamped for any service date past CY2026 (see
    :func:`worth_complexity.synthetic.pricing.vintage_for_date_clamped`),
    which the ``policy-change`` scenario's CY2027 half of its window needs.

    ``policy-change``'s own 4% fee-schedule step (CONTRACT-SEEDS.md's
    catalog: "payer multiples unchanged, PFS x1.04 after") is applied here
    too, uniformly, to every encounter's own base amount -- comparator or
    study -- once its service date reaches ``scenario.policy_date``: the
    whole fee schedule stepped, not one code's own price.
    """
    from worth_fees import expected_allowed

    from worth_complexity.curve import fit
    from worth_complexity.synthetic import pricing

    def pfs(cpt: str, year: int, quarter: int) -> Decimal:
        return expected_allowed(cpt, [], LOCALITY, SETTING, year, quarter).amount

    at_date: dict[str, Decimal] = {}
    points: list[tuple[Decimal, Decimal]] = []
    for e in encs:
        if e.code.cohort != "comparator":
            continue
        v = pricing.vintage_for_date_clamped(e.svc_date)
        at_date[e.log_id] = pfs(e.code.cpt, v.rule_year, v.quarter)
        points.append((Decimal(scores[e.log_id]), pfs(e.code.cpt, *REFERENCE_VINTAGE)))
    curve = fit("generator/schedule", tuple(points))

    by_code: dict[str, list[int]] = {}
    study_all_scores: list[int] = []
    for e in encs:
        if e.code.cohort == "study":
            by_code.setdefault(e.code.cpt, []).append(scores[e.log_id])
            study_all_scores.append(scores[e.log_id])
    mean_by_code = {cpt: Decimal(sum(v)) / Decimal(len(v)) for cpt, v in by_code.items()}
    study_all_scores.sort()
    median_study = (
        Decimal(study_all_scores[len(study_all_scores) // 2]) if study_all_scores else Decimal(0)
    )

    policy_date = scenario.policy_date
    for e in encs:
        if e.code.cohort == "comparator":
            base = at_date[e.log_id]
        else:
            base = pricing.price_study(
                scenario.payment_rule,
                scenario.payment_share,
                own_score=Decimal(scores[e.log_id]),
                mean_code_score=mean_by_code[e.code.cpt],
                median_study_score=median_study,
                curve=curve,
            )
        if policy_date is not None and e.svc_date >= policy_date:
            base = base * pricing.FEE_SCHEDULE_STEP
        noise = Decimal(str(round(rng.uniform(0.985, 1.015), 4)))
        allowed = (base * e.payer.multiplier * noise).quantize(Decimal("0.01"))
        e.allowed[e.code.cpt] = allowed
        for cpt, _ in e.cpts[1:]:
            e.allowed[cpt] = Decimal("0.00")


def _thin_cohorts(encs: list[Encounter]) -> list[Encounter]:
    """8 study cases per code; one comparator code down to 5; one payer
    (the first, UHC) capped at 4 comparator encounters total."""
    by_code: dict[str, list[Encounter]] = {}
    for e in encs:
        by_code.setdefault(e.code.cpt, []).append(e)
    study_codes = sorted(c for c in by_code if by_code[c][0].code.cohort == "study")
    comparator_codes = sorted(c for c in by_code if by_code[c][0].code.cohort == "comparator")

    thinned: list[Encounter] = []
    for c in study_codes:
        thinned.extend(by_code[c][:8])

    thin_payer_id = PAYERS[0].payer_id
    thin_payer_seen = 0
    for i, c in enumerate(comparator_codes):
        group = by_code[c][:5] if i == 0 else by_code[c]
        for e in group:
            if e.payer.payer_id == thin_payer_id:
                if thin_payer_seen >= 4:
                    continue
                thin_payer_seen += 1
            thinned.append(e)

    thinned.sort(key=lambda e: (e.svc_date, e.log_id))
    return thinned


def build(out_dir: Path, *, scenario: Scenario, seed: int | None = None, scale: int = 1) -> None:
    """The scenario-aware entry point ``worth-cli synth`` calls
    (CONTRACT-SEEDS.md's "worth track S1"). Unlike :func:`build_fixture`,
    which exists only to reproduce the committed fixture byte-for-byte,
    this targets ``scenarios.SCENARIO_STUDY_PER_CODE`` study encounters per
    code and the generator's own normal (unscaled) comparator counts,
    except when ``scenario.thin_cohorts`` overrides both."""
    from worth_complexity.synthetic import dirt, friction, planted, pricing, sites
    from worth_complexity.synthetic import truth as truth_mod
    from worth_complexity.synthetic.scenarios import SCENARIO_STUDY_PER_CODE
    from worth_complexity.version import __version__

    seed_value = seed if seed is not None else SEED
    rng = random.Random(seed_value)

    for owned in ("clinical", "remittance", "claims"):
        shutil.rmtree(out_dir / owned, ignore_errors=True)

    base_study_n = STUDY[0].n_cases  # uniform across every study code (6)
    study_scale = max(1, round(SCENARIO_STUDY_PER_CODE / base_study_n * scale))
    if scenario.thin_cohorts:
        study_scale = 2  # 12/code, thinned to 8 below
    if scenario.policy_date is not None:
        encs = build_encounters(
            rng,
            study_scale,
            comparator_scale=1,
            period_start=POLICY_PERIOD_START,
            period_days=POLICY_PERIOD_DAYS,
            comparator_period_start=PERIOD_START,
            comparator_period_days=PERIOD_DAYS,
        )
        from worth_complexity.synthetic import knobs

        by_study_code: dict[str, list[Encounter]] = {}
        for e in encs:
            if e.code.cohort == "study":
                by_study_code.setdefault(e.code.cpt, []).append(e)
        for group in by_study_code.values():
            knobs.stratify_study_months(
                group,
                period_start=POLICY_PERIOD_START,
                months=POLICY_MONTHS,
                rng=rng,
                date_attr="svc_date",
            )
        encs.sort(key=lambda e: (e.svc_date, e.log_id))
    else:
        encs = build_encounters(rng, study_scale, comparator_scale=1)
    if scenario.thin_cohorts:
        encs = _thin_cohorts(encs)

    m1_flags: dict[str, int] | None = None
    if scenario.m1_omission_rate:
        from worth_complexity.synthetic import knobs

        dropped = knobs.omit_secondary_codes(encs, rng=rng, rate=scenario.m1_omission_rate)
        study_n = sum(1 for e in encs if e.code.cohort == "study")
        # Every dropped secondary code here is a plain omission (never a
        # code NCCI-bundles into another submitted one -- this generator
        # has no bundling knob), so it reads as Method 1's own "missed"
        # bucket, not "mismatched".
        m1_flags = {"missed": dropped, "mismatched": 0, "no_code": 0, "n": study_n}

    blank_ebl: frozenset[str] = frozenset()
    skip_operative: frozenset[str] = frozenset()
    if scenario.multi_site:
        sites.assign_sites(encs, rng=rng)
        blank_ebl = sites.sample_ids(
            encs,
            rng=rng,
            site=sites.SITE_C,
            rate=sites.SITE_C_EBL_BLANK_RATE,
            id_of=lambda e: e.log_id,
        )
        skip_operative = sites.sample_ids(
            encs,
            rng=rng,
            site=sites.SITE_B,
            rate=sites.SITE_B_NOTE_OMISSION_RATE,
            id_of=lambda e: e.log_id,
        )

    clinical = out_dir / "clinical"
    write_clinical(clinical, encs, rng, blank_ebl=blank_ebl)
    write_notes(
        clinical,
        encs,
        {e.log_id: e.difficulty for e in encs},
        skip_operative=skip_operative,
        blank_ebl=blank_ebl,
    )

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

    _assign_payments_scenario(encs, scores, rng, scenario)
    write_remittance(out_dir / "remittance", encs)
    write_claims(out_dir / "claims", encs)

    friction_summary = friction.apply(out_dir, scenario, rng=rng)
    if scenario.dirt:
        dirt.apply(out_dir, scenario.dirt, rng=rng)
    if scenario.broken:
        dirt.apply_broken(out_dir, scenario.broken, rng=rng)

    ratio_band = None
    ratio_bands = None
    missingness = 0.0
    site_missingness = None
    if not scenario.broken:
        ratio_band = pricing.ratio_band(scenario.payment_rule, scenario.payment_share)
        if scenario.policy_date is not None:
            # The fee-schedule step (CONTRACT-SEEDS.md's catalog) moves
            # ``realized`` alone: Method 3's curve is fit once, from the
            # comparator cohort's (score, reference-vintage PFS) pairs, so
            # ``expected`` never sees it. The post-date ratio is therefore
            # the pre-date band times the same step.
            step = float(pricing.FEE_SCHEDULE_STEP)
            ratio_bands = {
                "study_pre": ratio_band,
                "study_post": [round(v * step, 4) for v in ratio_band],
            }
        if scenario.dirt or scenario.multi_site:
            missingness = planted.missingness_max(clinical, "surgical")
        if scenario.multi_site:
            site_missingness = planted.missingness_by_site(clinical, "surgical")
    planted_truth = planted.build_planted(
        scenario,
        root=out_dir,
        friction_summary=friction_summary,
        ratio_band=ratio_band,
        ratio_bands=ratio_bands,
        missingness_max=missingness,
        method0_verdict="flat" if scenario.payment_rule != "on-curve" else "rising",
        sites=len(sites.SITE_NPIS) if scenario.multi_site else None,
        site_missingness=site_missingness,
        periods=2 if scenario.policy_date is not None else None,
        policy_date_iso=scenario.policy_date.isoformat() if scenario.policy_date else None,
        flags=m1_flags,
    )
    truth_mod.write(
        out_dir,
        truth_mod.Truth(
            scenario=scenario.name,
            encounter_class="surgical",
            seed=seed_value,
            generator_version=__version__,
            planted=planted_truth,
            purpose=truth_mod.render_purpose(scenario.name, planted_truth),
            notes=scenario.notes,
        ),
    )
