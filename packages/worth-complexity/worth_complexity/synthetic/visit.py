"""Generate the visit-synthetic dataset (CONTRACT-PACKS.md, "Visit class").

Writes a fabricated but internally consistent office-visit extract: eight
pipe-delimited tables in the shape ``visits.py`` reads, a note per visit, the
ANSI X12 835 remittances that pay for it, and the ANSI X12 837P professional
claims behind them. It exercises the ``visit-em-v1`` rule pack end to end:
the five structured markers, the ``patient_context`` marker's decision 2
(pregnancy tier vs. the shared decision-making note rule), and all six
``work_rules`` families across all three Method 1 buckets.

The 835/837 writers (``write_remittance``, ``write_claims``) are **imported**
from :mod:`worth_complexity.synthetic.edi`, not copied: both functions read their
encounter argument by attribute only (``svc_date``, ``payer``, ``cpts``,
``account``, ``pat_id``, ``csn``, ``dx``, ``allowed``, ``billed``) and never
touch anything surgical-specific (there is no ``e.code`` or ``e.minutes`` in
either function), so :class:`VisitRecord` below, which carries exactly those
fields, satisfies them without a single line of EDI-writing code duplicated
here. Both generators now depend on one writer; a bug fixed in one is fixed
for both.

Payment is far simpler here than in the surgical generator, which fits its
own schedule curve to place the five study codes at a deliberate fraction of
it. This dataset does not need that: every code, study or comparator, is
allowed at ``payer.multiplier x PFS(billed CPT, locality, non-facility, the
vintage in force on the service date)`` directly (CONTRACT-PACKS.md's Visit
class section). The flatness the study is built to demonstrate falls out for
free: MENOPAUSE and MATERNITY always bill the single flat code 99214, so
their realized payment can never move with the complexity score no matter
how the score is computed, while the comparator cohort's primary code climbs
with intensity (99213 -> 99214 -> 99215 -> 99215+G2212), so its realized
payment climbs too.

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
    PAYERS,
    Payer,
    write_claims,
    write_remittance,
)

if TYPE_CHECKING:
    from worth_complexity.synthetic.scenarios import Scenario

SEED = 20260909
SITE_NPI = "1447890215"
LOCALITY = "NY01"
SETTING = "non-facility"
REFERENCE_VINTAGE = (2026, 4)
"""The vintage the scenario-aware entry point's schedule curve is fitted at
(see :func:`_assign_payments_scenario`) -- the same convention as
``surgical.py``'s ``REFERENCE_VINTAGE``. :func:`build_fixture`'s own
``assign_payments`` does not use this: it prices every line at the vintage
in force on its own service date directly, with no curve fit."""

PERIOD_START = date(2026, 1, 5)
PERIOD_DAYS = 360

POLICY_PERIOD_START = date(2026, 1, 5)
POLICY_PERIOD_DAYS = 725
"""24 months, 2026-01 through 2027-12 (CONTRACT-SEEDS.md's ``policy-change``
scenario). MENOPAUSE and the comparator service lines are drawn from this
whole window unchanged; MATERNITY is not -- see :func:`build_pregnancy` and
:func:`build`'s scenario branch."""

STUDY_N = 60
COMPARATOR_N = 36

_DX_POOL = {
    "MENOPAUSE": ["N95.1", "N95.0", "N92.4", "E28.310", "N95.2", "R23.4"],
    "MATERNITY": ["O09.90", "Z34.90", "O26.849", "O24.419", "O99.019", "Z3A.28"],
    "ENDOCRINOLOGY": ["E11.9", "E03.9", "E05.90", "E11.22", "E66.01"],
    "CARDIOLOGY": ["I10", "I25.10", "I48.91", "I50.9", "I25.119"],
    "NEPHROLOGY": ["N18.3", "N18.4", "N04.9", "N17.9", "I12.9"],
}

_CHRONIC_FLAG_POOL = {
    "ENDOCRINOLOGY": ["T2DM", "CHRONIC_HTN"],
    "CARDIOLOGY": ["HF", "CHRONIC_HTN"],
    "NEPHROLOGY": ["CKD", "CHRONIC_HTN"],
}

_SPECIALTY = {
    "MENOPAUSE": "Obstetrics & Gynecology",
    "MATERNITY": "Obstetrics & Gynecology",
    "ENDOCRINOLOGY": "Endocrinology",
    "CARDIOLOGY": "Cardiology",
    "NEPHROLOGY": "Nephrology",
}

_CLINICIANS = {
    "MENOPAUSE": ["C10817", "C11244"],
    "MATERNITY": ["C10817", "C11402"],
    "ENDOCRINOLOGY": ["C20901", "C20955"],
    "CARDIOLOGY": ["C21120", "C21188"],
    "NEPHROLOGY": ["C23318", "C23370"],
}

_COUNSELING_PHRASES = [
    "Shared decision-making was used to weigh hormone therapy against non-hormonal options.",
    "Risks and benefits were discussed at length and the patient elected to proceed with therapy.",
    "Treatment options reviewed with the patient; she elected to continue with observation.",
    "The patient elected to defer treatment after a shared decision conversation.",
]
_SUBJECTIVE_DECOY = (
    "No shared decision conversation occurred at today's visit; that discussion is deferred to "
    "the next appointment."
)


def lerp(lo: float, hi: float, t: float) -> float:
    return lo + (hi - lo) * t


@dataclass
class VisitRecord:
    """One office visit. Carries both the clinical-table fields ``visits.py``
    reads and the exact attribute set ``write_remittance``/``write_claims``
    (imported from :mod:`worth_complexity.synthetic.edi`) read on their encounter
    argument: ``svc_date``, ``payer``, ``cpts``, ``account``, ``pat_id``,
    ``csn``, ``dx``, ``allowed``, ``billed``."""

    visit_id: str
    csn: str
    pat_id: str
    account: str
    svc_date: date
    service_line: str
    cohort: str
    clinician_id: str
    total_documented_minutes: int
    cpts: list[tuple[str, str]]
    dx: list[str]
    dx_addressed: list[str]
    orders: list[tuple[str, str, int, int]]  # order_type, action, hour, minute
    med_orders: list[tuple[str, str]]
    window: list[tuple[str, int, int, str, str]]
    """(encounter_type, minutes, day_offset, patient_initiated, clinician_id)."""
    problem_flags: list[tuple[str, int]]
    payer: Payer
    has_shared_decision_phrase: bool
    allowed: dict[str, Decimal] = field(default_factory=dict)
    billed: dict[str, Decimal] = field(default_factory=dict)
    site_npi: str = SITE_NPI
    """Which of the three facility NPIs this visit was seen at
    (CONTRACT-SEEDS.md's ``multi-site`` scenario). Defaults to the single
    site every non-multi-site build writes, so ``build_fixture`` -- which
    never touches this field -- keeps writing exactly what it always has."""


def _dx_rows(pool: list[str], rng: random.Random, n: int) -> list[str]:
    n = max(1, min(n, len(pool)))
    return [pool[0], *rng.sample(pool[1:], k=n - 1)] if n > 1 else [pool[0]]


def build_comparator(rng: random.Random, seq: int, service_line: str, day: date) -> VisitRecord:
    d = rng.uniform(0.0, 1.0)
    if d < 0.25:
        cpts = [("99213", "")]
    elif d < 0.55:
        cpts = [("99214", "")]
    elif d < 0.85:
        cpts = [("99215", "")]
    else:
        cpts = [("99215", ""), ("G2212", "")]

    minutes = round(lerp(14, 62, d) * rng.uniform(0.92, 1.08))
    n_dx = round(lerp(1, 5, d))
    orders_n = round(lerp(0, 5, d))
    coordination_min = round(lerp(0, 46, d))
    portal_patient_min = round(lerp(0, 24, d) * rng.uniform(0.7, 1.3))

    cpts.append(("G2211", ""))  # ongoing complex chronic care, always priceable here
    if coordination_min >= 20:
        cpts.append(("99490", ""))

    orders = [
        ("LAB" if i % 2 == 0 else "IMAGING", "ORDERED" if i % 3 else "REVIEWED", 9 + i, 5 * i)
        for i in range(orders_n)
    ]
    med_orders = [("11636", "ADJUST" if d > 0.3 else "CONTINUE")] if rng.random() < 0.8 else []

    window: list[tuple[str, int, int, str, str]] = []
    if coordination_min > 0:
        portal_c = round(coordination_min * 0.6)
        phone_c = coordination_min - portal_c
        if portal_c:
            window.append(("PORTAL", portal_c, rng.randint(3, 18), "N", ""))
        if phone_c:
            window.append(("TELEPHONE", phone_c, rng.randint(3, 20), "N", ""))
    if portal_patient_min > 0:
        window.append(("PORTAL", round(portal_patient_min), rng.randint(1, 25), "Y", ""))
    if rng.random() < 0.3:
        window.append(("LAB_REVIEW", rng.randint(5, 14), rng.randint(2, 28), "N", ""))

    flags = _CHRONIC_FLAG_POOL[service_line]
    problem_flags = [(flags[0], rng.randint(1, 3)), (flags[1], rng.randint(1, 2))]

    visit_id = f"V{300000 + seq * 173:06d}"
    return VisitRecord(
        visit_id=visit_id,
        csn=f"20{54000000 + seq * 4703:08d}",
        pat_id=f"W{6000 + seq * 11:07d}",
        account=f"B8{200000 + seq * 331:06d}",
        svc_date=day,
        service_line=service_line,
        cohort="comparator",
        clinician_id=rng.choice(_CLINICIANS[service_line]),
        total_documented_minutes=minutes,
        cpts=cpts,
        dx=_dx_rows(_DX_POOL[service_line], rng, n_dx),
        dx_addressed=["Y"] * n_dx if n_dx else ["Y"],
        orders=orders,
        med_orders=med_orders,
        window=window,
        problem_flags=problem_flags,
        payer=rng.choice(PAYERS),
        has_shared_decision_phrase=False,
    )


def build_study(rng: random.Random, seq: int, service_line: str, day: date) -> VisitRecord:
    is_maternity = service_line == "MATERNITY"
    d = rng.uniform(0.0, 1.0)

    if is_maternity:
        minutes = round(rng.uniform(42, 58))
        n_dx = round(lerp(2, 5, d))
        orders_n = round(lerp(1, 5, d))
        portal_patient_min = round(lerp(0, 16, d))
        mfm_min = round(lerp(0, 26, d)) if rng.random() < 0.35 else 0
        tier = 3 if d > 0.75 else (2 if d > 0.4 else 1)
        problem_flags = [("PREGNANCY_HIGH_RISK" if tier == 3 else "PREGNANCY", tier)]
        if rng.random() < 0.2:
            problem_flags.append(("CHRONIC_HTN", 1))
        med_action = "START" if rng.random() < 0.45 else "CONTINUE"
        has_phrase = False  # decision 2: pregnancy present, narrative branch never consulted
    else:
        minutes = round(lerp(32, 60, d) * rng.uniform(0.92, 1.08))
        n_dx = round(lerp(3, 6, d))
        orders_n = round(lerp(2, 5, d))
        portal_patient_min = round(lerp(4, 26, d))
        mfm_min = 0
        problem_flags = [("OSTEOPOROSIS", rng.randint(1, 2))] if rng.random() < 0.4 else []
        med_action = "ADJUST" if rng.random() < 0.4 else "CONTINUE"
        has_phrase = rng.random() < 0.5

    orders_types = (
        ["FETAL_SURVEILLANCE", "LAB", "GLUCOSE_LOG"] if is_maternity else ["LAB", "DEXA", "IMAGING"]
    )
    orders = [
        (orders_types[i % len(orders_types)], "ORDERED" if i % 2 == 0 else "REVIEWED", 9 + i, 5 * i)
        for i in range(orders_n)
    ]
    med_orders = [("41126" if is_maternity else "83367", med_action)]

    window: list[tuple[str, int, int, str, str]] = []
    if portal_patient_min > 0:
        window.append(("PORTAL", round(portal_patient_min), rng.randint(1, 26), "Y", ""))
    if mfm_min > 0:
        window.append(("MFM_COMANAGEMENT", mfm_min, rng.randint(3, 24), "N", ""))
    if rng.random() < 0.25:
        window.append(("PRIOR_AUTH", rng.randint(5, 12), rng.randint(2, 27), "N", ""))

    visit_id = f"V{100000 + seq * 149:06d}" if is_maternity else f"V{200000 + seq * 151:06d}"
    return VisitRecord(
        visit_id=visit_id,
        csn=f"20{51000000 + seq * 4409:08d}" if is_maternity else f"20{52500000 + seq * 4507:08d}",
        pat_id=f"W{2000 + seq * 7:07d}" if is_maternity else f"W{4000 + seq * 13:07d}",
        account=f"B7{100000 + seq * 293:06d}",
        svc_date=day,
        service_line=service_line,
        cohort="study",
        clinician_id=rng.choice(_CLINICIANS[service_line]),
        total_documented_minutes=minutes,
        cpts=[("99214", "")],
        dx=_dx_rows(_DX_POOL[service_line], rng, n_dx),
        dx_addressed=["Y"] * n_dx if n_dx else ["Y"],
        orders=orders,
        med_orders=med_orders,
        window=window,
        problem_flags=problem_flags,
        payer=rng.choice(PAYERS),
        has_shared_decision_phrase=has_phrase,
    )


def build_records(rng: random.Random, study_n: int, comparator_scale: int = 1) -> list[VisitRecord]:
    """Draw the cohort. ``study_n`` is the exact count for *each* study
    service line (MENOPAUSE, MATERNITY independently); the comparator lines
    scale by ``comparator_scale`` off their own :data:`COMPARATOR_N`. The two
    cohorts scale independently, the same convention as ``surgical.
    build_encounters``'s ``study_scale``/``comparator_scale``."""
    out: list[VisitRecord] = []
    seq = 0
    for service_line, builder, n in (
        ("MENOPAUSE", build_study, study_n),
        ("MATERNITY", build_study, study_n),
        ("ENDOCRINOLOGY", build_comparator, COMPARATOR_N * comparator_scale),
        ("CARDIOLOGY", build_comparator, COMPARATOR_N * comparator_scale),
        ("NEPHROLOGY", build_comparator, COMPARATOR_N * comparator_scale),
    ):
        for _ in range(n):
            seq += 1
            day = PERIOD_START + timedelta(days=rng.randrange(PERIOD_DAYS))
            out.append(builder(rng, seq, service_line, day))
    out.sort(key=lambda r: (r.svc_date, r.visit_id))
    return out


def _bundle_cpt(n_visits: int) -> str:
    """The antepartum-care global-package CPT for a pregnancy of
    ``n_visits`` prenatal visits (CONTRACT-SEEDS.md's ``policy-change``
    catalog row): ``59425`` (4 to 6 visits) or ``59426`` (7 or more) -- real
    CPT codes for "antepartum care only", billed once per pregnancy rather
    than once per visit."""
    return "59425" if n_visits <= 6 else "59426"


def build_pregnancy(
    rng: random.Random, pregnancy_seq: int, last_visit: date
) -> tuple[list[VisitRecord], VisitRecord]:
    """One pre-policy-date pregnancy: 6 to 10 prenatal visits sharing one
    billing account, ending on ``last_visit`` (the delivery visit or the
    last prenatal encounter before the policy date), each carrying the
    pregnancy's own bundle CPT (:func:`_bundle_cpt`) on ``visit_proc.txt``
    so every one of them still extracts and scores as its own
    :class:`~worth_complexity.models.Encounter`. Field draws mirror
    :func:`build_study`'s own ``MATERNITY`` branch (time, orders, problem
    tier, medication action), independently per visit.

    Returns ``(clinical_visits, billing_record)``: ``clinical_visits`` are
    written to the clinical tables and notes like any other visit;
    ``billing_record`` is never written to the clinical tables at all --
    it exists only to hand ``write_remittance``/``write_claims`` the
    pregnancy's single 837/835 claim, dated on ``last_visit`` (submitted
    after every visit it covers, as a real bundle claim would be) and
    sharing every clinical visit's own billing account, so ``linkage.link``
    joins all of them to it (:data:`worth_complexity.linkage.BUNDLE_CODES`).
    """
    n_visits = rng.randint(6, 10)
    bundle_cpt = _bundle_cpt(n_visits)
    payer = rng.choice(PAYERS)
    clinician = rng.choice(_CLINICIANS["MATERNITY"])
    account = f"B9{100000 + pregnancy_seq * 293:06d}"
    pat_id = f"W{2500 + pregnancy_seq * 7:07d}"

    spacing_days = rng.randint(21, 28)
    visits: list[VisitRecord] = []
    for i in range(n_visits):
        offset = (n_visits - 1 - i) * spacing_days
        day = last_visit - timedelta(days=offset)
        d = rng.uniform(0.0, 1.0)
        minutes = round(rng.uniform(42, 58))
        n_dx = round(lerp(2, 5, d))
        orders_n = round(lerp(1, 5, d))
        portal_patient_min = round(lerp(0, 16, d))
        mfm_min = round(lerp(0, 26, d)) if rng.random() < 0.35 else 0
        tier = 3 if d > 0.75 else (2 if d > 0.4 else 1)
        problem_flags = [("PREGNANCY_HIGH_RISK" if tier == 3 else "PREGNANCY", tier)]
        if rng.random() < 0.2:
            problem_flags.append(("CHRONIC_HTN", 1))
        med_action = "START" if rng.random() < 0.45 else "CONTINUE"

        orders_types = ["FETAL_SURVEILLANCE", "LAB", "GLUCOSE_LOG"]
        orders = [
            (
                orders_types[j % len(orders_types)],
                "ORDERED" if j % 2 == 0 else "REVIEWED",
                9 + j,
                5 * j,
            )
            for j in range(orders_n)
        ]
        med_orders = [("41126", med_action)]

        window: list[tuple[str, int, int, str, str]] = []
        if portal_patient_min > 0:
            window.append(("PORTAL", round(portal_patient_min), rng.randint(1, 26), "Y", ""))
        if mfm_min > 0:
            window.append(("MFM_COMANAGEMENT", mfm_min, rng.randint(3, 24), "N", ""))
        if rng.random() < 0.25:
            window.append(("PRIOR_AUTH", rng.randint(5, 12), rng.randint(2, 27), "N", ""))

        visit_seq = pregnancy_seq * 20 + i
        visits.append(
            VisitRecord(
                visit_id=f"V{900000 + visit_seq * 149:06d}",
                csn=f"20{59000000 + visit_seq * 4409:08d}",
                pat_id=pat_id,
                account=account,
                svc_date=day,
                service_line="MATERNITY",
                cohort="study",
                clinician_id=clinician,
                total_documented_minutes=minutes,
                cpts=[(bundle_cpt, "")],
                dx=_dx_rows(_DX_POOL["MATERNITY"], rng, n_dx),
                dx_addressed=["Y"] * n_dx if n_dx else ["Y"],
                orders=orders,
                med_orders=med_orders,
                window=window,
                problem_flags=problem_flags,
                payer=payer,
                has_shared_decision_phrase=False,
            )
        )

    last = visits[-1]
    billing = VisitRecord(
        visit_id=f"V{950000 + pregnancy_seq * 149:06d}",
        csn=last.csn,
        pat_id=pat_id,
        account=account,
        svc_date=last.svc_date,
        service_line="MATERNITY",
        cohort="study",
        clinician_id=clinician,
        total_documented_minutes=last.total_documented_minutes,
        cpts=[(bundle_cpt, "")],
        dx=last.dx,
        dx_addressed=last.dx_addressed,
        orders=[],
        med_orders=[],
        window=[],
        problem_flags=[],
        payer=payer,
        has_shared_decision_phrase=False,
    )
    return visits, billing


def build_records_policy_change(
    rng: random.Random, study_n: int, comparator_scale: int, policy_date: date
) -> tuple[list[VisitRecord], list[VisitRecord]]:
    """The ``policy-change`` scenario's own cohort draw, replacing
    :func:`build_records`: MENOPAUSE is unaffected -- drawn across the whole
    24-month window exactly as :func:`build_records` draws it across its
    one-year one -- but MATERNITY is not one service line drawn the same
    way throughout. Every pregnancy whose visits would otherwise fall
    before ``policy_date`` is drawn as a :func:`build_pregnancy` bundle
    instead of individual visits; pregnancies from ``policy_date`` on are
    drawn exactly like :func:`build_study`'s own ``MATERNITY`` branch (one
    99214 per visit, as today) -- CONTRACT-SEEDS.md's catalog: "MATERNITY
    visits before the date are not billed per visit ... After the date,
    maternity visits bill 99214 as today".

    The three comparator service lines stay within the original one-year
    CY2026 window (:data:`PERIOD_START`/:data:`PERIOD_DAYS`), not the full
    24 months: Method 0/3's fitted curve only needs comparator points, and
    :func:`worth_complexity.adequacy.price_comparators` prices every
    comparator encounter at the vintage in force on *its own* service date
    with the real, unclamped ``worth_fees.sources.vintage_for_date`` --
    unlike this generator's own pricing helper
    (:func:`worth_complexity.synthetic.pricing.vintage_for_date_clamped`),
    that lookup has no CY2027 fallback, so a comparator dated in 2027 would
    make any real ``pipeline.run`` over this fixture fail at ``price``. The
    study cohort (MENOPAUSE, MATERNITY) is exactly what the scenario is
    about spanning the policy date; the comparator cohort anchoring the
    curve does not need to.

    Returns ``(clinical_records, billing_records)``: every clinical record
    also appears in ``billing_records`` *except* a bundle pregnancy's own
    prenatal visits, which are replaced there by that pregnancy's single
    billing-only record (:func:`build_pregnancy`) -- the split
    ``write_clinical``/``write_notes`` vs. ``write_remittance``/
    ``write_claims`` need to write a bundle's clinical rows without billing
    each one individually.
    """
    clinical: list[VisitRecord] = []
    billing: list[VisitRecord] = []
    seq = 0
    window_end = POLICY_PERIOD_START + timedelta(days=POLICY_PERIOD_DAYS)

    for _ in range(study_n):
        seq += 1
        day = POLICY_PERIOD_START + timedelta(days=rng.randrange(POLICY_PERIOD_DAYS))
        r = build_study(rng, seq, "MENOPAUSE", day)
        clinical.append(r)
        billing.append(r)

    for service_line, n in (
        ("ENDOCRINOLOGY", COMPARATOR_N * comparator_scale),
        ("CARDIOLOGY", COMPARATOR_N * comparator_scale),
        ("NEPHROLOGY", COMPARATOR_N * comparator_scale),
    ):
        for _ in range(n):
            seq += 1
            day = PERIOD_START + timedelta(days=rng.randrange(PERIOD_DAYS))
            r = build_comparator(rng, seq, service_line, day)
            clinical.append(r)
            billing.append(r)

    # MATERNITY: about half its target count post-date, drawn individually
    # (billed 99214 as today); the rest pre-date, drawn as bundled
    # pregnancies (billed once per pregnancy on the antepartum-care code).
    post_n = max(1, study_n // 2)
    post_days = max(1, (window_end - policy_date).days)
    for _ in range(post_n):
        seq += 1
        day = policy_date + timedelta(days=rng.randrange(post_days))
        r = build_study(rng, seq, "MATERNITY", day)
        clinical.append(r)
        billing.append(r)

    pre_target = max(1, study_n - post_n)
    pre_visits = 0
    pregnancy_seq = 0
    # A pregnancy's own span runs about 5 to 8 months (6-10 visits, 21-28
    # days apart); its last visit needs at least that much room before
    # ``policy_date`` for every one of its visits to land before it.
    latest_last_visit = policy_date - timedelta(days=14)
    earliest_last_visit = POLICY_PERIOD_START + timedelta(days=220)
    while pre_visits < pre_target and latest_last_visit > earliest_last_visit:
        pregnancy_seq += 1
        last_visit = earliest_last_visit + timedelta(
            days=rng.randrange((latest_last_visit - earliest_last_visit).days)
        )
        visits, billing_record = build_pregnancy(rng, pregnancy_seq, last_visit)
        clinical.extend(visits)
        billing.append(billing_record)
        pre_visits += len(visits)

    clinical.sort(key=lambda r: (r.svc_date, r.visit_id))
    billing.sort(key=lambda r: (r.svc_date, r.account, r.visit_id))
    return clinical, billing


def stamp(d: date, hour: int, minute: int) -> str:
    hour = hour % 24
    return f"{d.strftime('%m/%d/%Y')} {hour:02d}:{minute % 60:02d}:00"


def write_clinical(out: Path, records: list[VisitRecord]) -> None:
    out.mkdir(parents=True, exist_ok=True)

    visit = [
        "visit_id|csn|patient_id|billing_account_id|service_date|service_line|specialty|cohort|"
        "site_npi|clinician_id|total_documented_minutes|time_attested"
    ]
    visit_proc = ["visit_id|cpt|modifier|sequence"]
    encounter_dx = ["csn|icd10|sequence|addressed"]
    orders = ["visit_id|order_type|action|order_dttm"]
    med_orders = ["visit_id|rxnorm|action"]
    window_encounters = [
        "visit_id|encounter_type|contact_dttm|minutes|patient_initiated|clinician_id"
    ]
    problem_list = ["patient_id|flag|tier"]
    patient_lds = ["pat_id|birth_date|sex|zip5|county"]

    seen_patients: set[str] = set()
    for r in records:
        visit.append(
            "|".join(
                [
                    r.visit_id,
                    r.csn,
                    r.pat_id,
                    r.account,
                    f"{r.svc_date.strftime('%m/%d/%Y')} 00:00:00",
                    r.service_line,
                    _SPECIALTY[r.service_line],
                    r.cohort,
                    r.site_npi,
                    r.clinician_id,
                    str(r.total_documented_minutes),
                    "Y",
                ]
            )
        )
        for i, (cpt, mod) in enumerate(r.cpts, start=1):
            visit_proc.append("|".join([r.visit_id, cpt, mod, str(i)]))
        for i, (code, addressed) in enumerate(zip(r.dx, r.dx_addressed, strict=False), start=1):
            encounter_dx.append(f"{r.csn}|{code}|{i}|{addressed}")
        for order_type, action, hour, minute in r.orders:
            orders.append(f"{r.visit_id}|{order_type}|{action}|{stamp(r.svc_date, hour, minute)}")
        for rxnorm, action in r.med_orders:
            med_orders.append(f"{r.visit_id}|{rxnorm}|{action}")
        for enc_type, minutes, day_offset, patient_initiated, clinician in r.window:
            contact = r.svc_date + timedelta(days=day_offset)
            window_encounters.append(
                "|".join(
                    [
                        r.visit_id,
                        enc_type,
                        stamp(contact, 10, 15),
                        str(minutes),
                        patient_initiated,
                        clinician or r.clinician_id,
                    ]
                )
            )
        if r.pat_id not in seen_patients:
            seen_patients.add(r.pat_id)
            for flag, tier in r.problem_flags:
                problem_list.append(f"{r.pat_id}|{flag}|{tier}")
            birth = date(r.svc_date.year - 34, 6, 1)
            patient_lds.append(f"{r.pat_id}|{birth.strftime('%m/%Y')}|F|10029|New York")

    for name, rows in [
        ("visit.txt", visit),
        ("visit_proc.txt", visit_proc),
        ("encounter_dx.txt", encounter_dx),
        ("orders.txt", orders),
        ("med_orders.txt", med_orders),
        ("window_encounters.txt", window_encounters),
        ("problem_list.txt", problem_list),
        ("patient_lds.txt", patient_lds),
    ]:
        (out / name).write_text("\n".join(rows) + "\n", encoding="utf-8")


def visit_note(r: VisitRecord) -> str:
    lines = [
        "MOUNT SINAI HEALTH SYSTEM",
        "OFFICE VISIT NOTE",
        "",
        f"PATIENT: {r.pat_id}",
        f"DATE OF SERVICE: {r.svc_date.strftime('%m/%d/%Y')}",
        f"CLINICIAN: {r.clinician_id}",
        f"SERVICE LINE: {r.service_line}",
        "",
        "SUBJECTIVE:",
        f"Patient presents for a scheduled visit. {_SUBJECTIVE_DECOY}",
        "",
        "OBJECTIVE:",
        "Vital signs reviewed and within the patient's usual range. Examination as documented in "
        "the flowsheet.",
        "",
        "ASSESSMENT:",
        f"{r.dx[0]} addressed today, stable on the current plan.",
        "",
        "COUNSELING:",
    ]
    if r.has_shared_decision_phrase:
        # Python's str hash is salted per process, so it cannot be used here without
        # breaking the byte-identical regeneration this file promises; crc32 is a
        # plain deterministic function of the bytes, not of the process.
        pick = zlib.crc32(r.visit_id.encode()) % len(_COUNSELING_PHRASES)
        lines.append(_COUNSELING_PHRASES[pick])
    else:
        lines.append("Routine anticipatory guidance was provided; no decision point arose today.")
    lines += [
        "",
        "PLAN:",
        "Continue current management and follow up as scheduled.",
        "",
        f"TIME: {r.total_documented_minutes} minutes total documented for this encounter.",
        "",
    ]
    return "\n".join(lines)


def write_notes(
    out: Path, records: list[VisitRecord], *, skip: frozenset[str] = frozenset()
) -> None:
    """``skip``: visit ids to write no note file for at all -- site B's
    multi-site documentation gap (CONTRACT-SEEDS.md's catalog). Withholding
    the whole note, rather than editing its language, is what makes
    ``visits.py``'s narrative ``patient_context`` reading (the shared
    decision-making branch, decision 2 of CONTRACT-PACKS.md) genuinely
    absent for that visit -- decision 6's default-to-zero -- rather than a
    note that legitimately never uses the tracked phrases. Empty by
    default, so ``build_fixture`` writes every visit's note, unchanged."""
    folder = out / "notes"
    folder.mkdir(parents=True, exist_ok=True)
    index = ["note_id|log_id|csn|note_type|service_date|author_role|filename"]
    for r in records:
        if r.visit_id in skip:
            continue
        note_id = f"N{r.visit_id[1:]}"
        filename = f"{note_id}.txt"
        (folder / filename).write_text(visit_note(r), encoding="utf-8")
        index.append(
            "|".join(
                [
                    note_id,
                    r.visit_id,
                    r.csn,
                    "visit",
                    f"{r.svc_date.strftime('%m/%d/%Y')} 00:00:00",
                    "Attending Physician",
                    filename,
                ]
            )
        )
    (out / "notes.txt").write_text("\n".join(index) + "\n", encoding="utf-8")


def assign_payments(records: list[VisitRecord], rng: random.Random) -> None:
    """Realized = payer's multiple of the real Medicare PFS amount for the
    billed CPT, in NY01 non-facility, at the vintage in force on the service
    date (CONTRACT-PACKS.md's Visit class section) -- no schedule-curve fit
    needed, unlike the surgical generator, because payment here is never
    meant to diverge from a plain function of the billed code."""
    from worth_fees import expected_allowed
    from worth_fees.sources import vintage_for_date

    for r in records:
        v = vintage_for_date(r.svc_date)
        for cpt, _ in r.cpts:
            pfs = expected_allowed(cpt, [], LOCALITY, SETTING, v.rule_year, v.quarter).amount
            noise = Decimal(str(round(rng.uniform(0.985, 1.015), 4)))
            r.allowed[cpt] = (pfs * r.payer.multiplier * noise).quantize(Decimal("0.01"))


def build_fixture(root: Path, scale: int = 1, *, seed: int = SEED) -> None:
    """Build the committed visit-synthetic fixture (or a scaled variant of
    its exact shape) into ``root``. The original generator's ``main()`` body,
    parameterized -- RNG call order untouched, so ``scale=1, seed=SEED``
    stays byte-identical to the committed fixture. See :func:`build` for the
    scenario-aware entry point."""
    for owned in ("clinical", "remittance", "claims"):
        shutil.rmtree(root / owned, ignore_errors=True)

    rng = random.Random(seed)
    records = build_records(rng, study_n=STUDY_N * scale, comparator_scale=scale)

    clinical = root / "clinical"
    write_clinical(clinical, records)
    write_notes(clinical, records)

    assign_payments(records, rng)
    write_remittance(root / "remittance", records)
    write_claims(root / "claims", records)

    study = [r for r in records if r.cohort == "study"]
    comparator = [r for r in records if r.cohort == "comparator"]
    remits = len(list((root / "remittance").glob("*.edi")))
    claims_n = len(list((root / "claims").glob("*.edi")))
    notes_n = len(list((clinical / "notes").glob("*.txt")))
    print(f"{len(records)} visits, {notes_n} notes -> {root}")
    print(f"  study      {len(study)} across {len({r.service_line for r in study})} service lines")
    comparator_lines = len({r.service_line for r in comparator})
    print(f"  comparator {len(comparator)} across {comparator_lines} service lines")
    print(f"  remittance {remits} 835 files, claims {claims_n} 837 files")


# ---------------------------------------------------------------------------
# Scenario-aware entry point (CONTRACT-SEEDS.md, "worth track S1")
# ---------------------------------------------------------------------------


def _assign_payments_scenario(
    records: list[VisitRecord], scores: dict[str, int], rng: random.Random, scenario: Scenario
) -> None:
    """:func:`assign_payments`, generalized to every payment rule the
    catalog names (:mod:`worth_complexity.synthetic.pricing`) instead of
    always the plain PFS-of-the-billed-code convention. Only the study
    cohort's sole billed line (``build_study``'s ``cpts``, or a
    ``policy-change`` pregnancy's bundle line, :func:`build_pregnancy`) is
    repriced this way; every comparator line, and a study record's own
    secondary lines (none exist today, but the loop stays general), keeps
    :func:`assign_payments`'s plain-PFS convention -- the catalog's payment
    rules are about the study code only.

    The study cohort's mean score is computed **per billed code**, not
    pooled across every study line (unlike a first pass at this function):
    every non-``policy-change`` scenario only ever has one study code
    (``99214``), so this was never visible before, but ``policy-change``
    bills three different codes across its two service lines (``99214`` for
    MENOPAUSE and post-date MATERNITY, ``59425``/``59426`` for a pre-date
    pregnancy's bundle) and pooling their scores into one mean would price
    an antepartum bundle as if it were a single office visit. The fee
    schedule lookup is also clamped for any service date past CY2026 (see
    :func:`worth_complexity.synthetic.pricing.vintage_for_date_clamped`),
    which ``policy-change``'s CY2027 half needs.
    """
    from worth_fees import expected_allowed

    from worth_complexity.curve import fit
    from worth_complexity.synthetic import pricing

    def pfs(cpt: str, year: int, quarter: int) -> Decimal:
        return expected_allowed(cpt, [], LOCALITY, SETTING, year, quarter).amount

    points: list[tuple[Decimal, Decimal]] = []
    for r in records:
        if r.cohort != "comparator":
            continue
        points.append((Decimal(scores[r.visit_id]), pfs(r.cpts[0][0], *REFERENCE_VINTAGE)))
    curve = fit("generator/visit-schedule", tuple(points))

    study_scores = sorted(scores[r.visit_id] for r in records if r.cohort == "study")
    median_study = Decimal(study_scores[len(study_scores) // 2]) if study_scores else Decimal(0)

    by_code: dict[str, list[int]] = {}
    for r in records:
        if r.cohort == "study":
            by_code.setdefault(r.cpts[0][0], []).append(scores[r.visit_id])
    mean_by_code = {cpt: Decimal(sum(v)) / Decimal(len(v)) for cpt, v in by_code.items() if v}

    for r in records:
        v = pricing.vintage_for_date_clamped(r.svc_date)
        for i, (cpt, _mod) in enumerate(r.cpts):
            if r.cohort == "study" and i == 0 and cpt in ("59425", "59426"):
                # A pre-date pregnancy's antepartum bundle is paid what the fee
                # schedule pays for the bundle, once for the whole pregnancy;
                # the pipeline allocates that across the visits it covers.
                # Pricing it off the office-visit curve would pay a pregnancy
                # like one visit and every visit a few dollars.
                base = pfs(cpt, v.rule_year, v.quarter)
            elif r.cohort == "study" and i == 0:
                base = pricing.price_study(
                    scenario.payment_rule,
                    scenario.payment_share,
                    own_score=Decimal(scores[r.visit_id]),
                    mean_code_score=mean_by_code[cpt],
                    median_study_score=median_study,
                    curve=curve,
                )
            else:
                base = pfs(cpt, v.rule_year, v.quarter)
            noise = Decimal(str(round(rng.uniform(0.985, 1.015), 4)))
            r.allowed[cpt] = (base * r.payer.multiplier * noise).quantize(Decimal("0.01"))


def _thin_cohorts(records: list[VisitRecord]) -> list[VisitRecord]:
    """8 study cases per service line; one comparator service line down to 5
    encounters; one payer (the first, UHC) capped at 4 comparator encounters
    total -- the visit-class reading of ``surgical._thin_cohorts``."""
    by_line: dict[str, list[VisitRecord]] = {}
    for r in records:
        by_line.setdefault(r.service_line, []).append(r)
    study_lines = sorted(line for line in by_line if by_line[line][0].cohort == "study")
    comparator_lines = sorted(line for line in by_line if by_line[line][0].cohort == "comparator")

    thinned: list[VisitRecord] = []
    for line in study_lines:
        thinned.extend(by_line[line][:8])

    thin_payer_id = PAYERS[0].payer_id
    thin_payer_seen = 0
    for i, line in enumerate(comparator_lines):
        group = by_line[line][:5] if i == 0 else by_line[line]
        for r in group:
            if r.payer.payer_id == thin_payer_id:
                if thin_payer_seen >= 4:
                    continue
                thin_payer_seen += 1
            thinned.append(r)

    thinned.sort(key=lambda r: (r.svc_date, r.visit_id))
    return thinned


def _policy_change_ratio_bands(out_dir: Path, policy_date: date) -> dict[str, list[float]]:
    """Pre/post adequacy-ratio bands for the ``policy-change`` scenario's
    two MATERNITY billing shapes, read back from the just-generated
    directory with the real pipeline -- the same reasoning
    :mod:`worth_complexity.synthetic.planted` gives for reading linkage and
    missingness back from disk rather than tracking them knob-by-knob
    through generation. A pregnancy's realized share (the bundle's allowed
    amount divided across its visits, :mod:`worth_complexity.linkage`'s
    bundle carve-out) compared to each visit's own expected payment is not a
    fixed multiple of anything :mod:`worth_complexity.synthetic.pricing`
    already has a closed-form band for -- unlike ``surgical``'s and
    ``episode``'s own fee-step bands, which are exactly
    :func:`worth_complexity.synthetic.pricing.ratio_band`'s pre band times
    :data:`worth_complexity.synthetic.pricing.FEE_SCHEDULE_STEP`.
    """
    from worth_complexity import pipeline

    run = pipeline.run(
        out_dir / "clinical",
        out_dir / "remittance",
        locality=LOCALITY,
        claims_dir=out_dir / "claims",
        policy_date=policy_date,
        # ``pipeline.run``'s own default reference vintage is ``vintage_for_
        # date(max service date)``, unclamped -- a straight call would raise
        # on this scenario's own CY2027 service dates. Pinned explicitly to
        # the same newest vintage :func:`worth_complexity.synthetic.pricing.
        # vintage_for_date_clamped` falls back to, so this stays a read of
        # what generation already priced rather than a second opinion on it.
        reference=(2026, 4),
    )
    by_code = {series.code: series for series in run.trends}

    def band(codes: tuple[str, ...], side: str) -> list[float] | None:
        values: list[Decimal] = []
        for code in codes:
            series = by_code.get(code)
            if series is None:
                continue
            summary = series.pre if side == "pre" else series.post
            if summary is not None and summary.ratio is not None:
                values.append(summary.ratio.value)
        if not values:
            return None
        lo, hi = min(values), max(values)
        margin = Decimal("0.03")
        return [round(float(max(Decimal(0), lo - margin)), 4), round(float(hi + margin), 4)]

    bands: dict[str, list[float]] = {}
    maternity_pre = band(("59425", "59426"), "pre")
    if maternity_pre is not None:
        bands["maternity_pre"] = maternity_pre
    maternity_post = band(("99214",), "post")
    if maternity_post is not None:
        bands["maternity_post"] = maternity_post
    return bands


def build(out_dir: Path, *, scenario: Scenario, seed: int | None = None, scale: int = 1) -> None:
    """The scenario-aware entry point ``worth-cli synth`` calls
    (CONTRACT-SEEDS.md's "worth track S1"), the visit-class counterpart of
    ``surgical.build``. Targets ``scenarios.SCENARIO_STUDY_PER_CODE`` study
    encounters per service line and the generator's own normal (unscaled)
    comparator counts, except when ``scenario.thin_cohorts`` overrides both."""
    from worth_complexity.synthetic import dirt, friction, planted, pricing, sites
    from worth_complexity.synthetic import truth as truth_mod
    from worth_complexity.synthetic.scenarios import SCENARIO_STUDY_PER_CODE
    from worth_complexity.version import __version__

    seed_value = seed if seed is not None else SEED
    rng = random.Random(seed_value)

    for owned in ("clinical", "remittance", "claims"):
        shutil.rmtree(out_dir / owned, ignore_errors=True)

    study_n = max(1, round(SCENARIO_STUDY_PER_CODE * scale))
    if scenario.thin_cohorts:
        study_n = 12  # 12/line, thinned to 8 below

    if scenario.policy_date is not None:
        clinical_records, billing_records = build_records_policy_change(
            rng, study_n=study_n, comparator_scale=1, policy_date=scenario.policy_date
        )
    else:
        clinical_records = build_records(rng, study_n=study_n, comparator_scale=1)
        billing_records = clinical_records
    if scenario.thin_cohorts:
        clinical_records = _thin_cohorts(clinical_records)
        billing_records = clinical_records

    m1_flags: dict[str, int] | None = None
    if scenario.m1_omission_rate:
        from worth_complexity.synthetic import knobs

        dropped = knobs.omit_secondary_codes(
            clinical_records, rng=rng, rate=scenario.m1_omission_rate
        )
        study_n = sum(1 for r in clinical_records if r.cohort == "study")
        m1_flags = {"missed": dropped, "mismatched": 0, "no_code": 0, "n": study_n}

    if scenario.m1_underlevel:
        # Every study visit's documented time guaranteed past the
        # em-level-by-time-99214 threshold (40 minutes), regardless of the
        # difficulty draw -- baseline already crosses it on most study
        # visits by construction; this knob makes it every one.
        for r in clinical_records:
            if r.cohort == "study" and r.total_documented_minutes < 40:
                r.total_documented_minutes = 40 + rng.randint(1, 10)

    skip_notes: frozenset[str] = frozenset()
    if scenario.multi_site:
        sites.assign_sites(clinical_records, rng=rng)
        skip_notes = sites.sample_ids(
            clinical_records,
            rng=rng,
            site=sites.SITE_B,
            rate=sites.SITE_B_NOTE_OMISSION_RATE,
            id_of=lambda r: r.visit_id,
        )
        # A bundle's billing-only record is never written to the clinical
        # tables, but its own account's real visits carry whichever site
        # the pregnancy was seen at -- give it the same site for the 835/837
        # side to stay internally consistent, even though nothing reads it.
        site_by_account = {r.account: r.site_npi for r in clinical_records}
        for r in billing_records:
            if r.visit_id not in {c.visit_id for c in clinical_records}:
                r.site_npi = site_by_account.get(r.account, r.site_npi)

    clinical = out_dir / "clinical"
    write_clinical(clinical, clinical_records)
    write_notes(clinical, clinical_records, skip=skip_notes)

    from worth_complexity import extracts, rulepack, scoring
    from worth_complexity.models import LAYER_A

    pack = rulepack.load("visit-em-v1")
    extract = extracts.read_extract(clinical)
    encounters = extract.encounters()
    marker_sets = extract.markers(encounters, pack)
    scores = {
        e.encounter_id: scoring.score(e, marker_sets[e.encounter_id], pack, LAYER_A).score.value
        for e in encounters
    }
    if scenario.policy_date is not None:
        # A pregnancy's billing-only record was never a clinical encounter,
        # so it never went through the real scorer above -- price it at its
        # own pregnancy's mean visit score instead (see :func:`build_pregnancy`).
        clinical_ids = {r.visit_id for r in clinical_records}
        for r in billing_records:
            if r.visit_id not in clinical_ids:
                siblings = [scores[c.visit_id] for c in clinical_records if c.account == r.account]
                scores[r.visit_id] = round(sum(siblings) / len(siblings)) if siblings else 0

    _assign_payments_scenario(billing_records, scores, rng, scenario)
    write_remittance(out_dir / "remittance", billing_records)
    write_claims(out_dir / "claims", billing_records)

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
            ratio_bands = _policy_change_ratio_bands(out_dir, scenario.policy_date)
        if scenario.dirt or scenario.multi_site:
            missingness = planted.missingness_max(clinical, "visit")
        if scenario.multi_site:
            site_missingness = planted.missingness_by_site(clinical, "visit")
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
        distribution_shift=False if scenario.policy_date is not None else None,
        flags=m1_flags,
    )
    truth_mod.write(
        out_dir,
        truth_mod.Truth(
            scenario=scenario.name,
            encounter_class="visit",
            seed=seed_value,
            generator_version=__version__,
            planted=planted_truth,
            purpose=truth_mod.render_purpose(scenario.name, planted_truth),
            notes=scenario.notes,
        ),
    )
