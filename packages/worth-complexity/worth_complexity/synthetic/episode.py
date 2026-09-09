"""Generate the episode-rpm synthetic dataset.

Companion to ``surgical.py`` (the surgical generator): same
partner (Mount Sinai), same four payers and multiples, same 835/837 shapes,
same "read this file before trusting the numbers" discipline. This one writes
a fabricated but internally consistent 30-day remote-physiologic-monitoring
extract instead of an operative log: ``episode.txt`` and its eight companion
tables (CONTRACT-PACKS.md's episode section), no notes dataset (the
methodology: "from device and alert logs alone").

The EDI writers (``write_remittance``, ``write_claims``) are adapted, not
imported, from ``edi.py`` (``surgical.py`` and ``visit.py``'s shared writers): those versions are
written against its own ``Encounter``/``Code``/``Payer`` dataclasses (CPT
panels keyed one way, diagnoses attached another), tightly enough coupled to
that shape that importing them would mean constructing fake surgical
``Encounter`` objects just to satisfy their field access. Copying and
retargeting the segment-writing logic keeps the two generators' 835/837
byte-shapes identical -- the same ISA/GS/ST/CLP/SVC/CAS and
ISA/GS/ST/CLM/SV1/DTP segments in the same order -- without a fragile import
across a module boundary neither file owns.

Read this file before trusting any number the episode fixture produces. The
generative assumptions, stated explicitly because they are the reason the
demo produces the result it does:

  * Every payer pays a fixed multiple of the Medicare Physician Fee Schedule,
    the same convention and the same four payers as the surgical generator.
  * One continuous "intensity" draw, ``t`` in [0, 1], drives every marker
    input for every episode, study and comparator alike, through the same
    roughly-linear map the pack's own weights imply (see the module docstring
    below, "score(t)"). Each of the three comparator programs occupies its
    own band of ``t`` -- cardiac-device low, CGM mid, home-dialysis high --
    chosen so their primary codes' real PFS amounts rise with ``t`` too, which
    is what gives Method 0's pooled comparator slope a real, positive trend to
    find.
  * The study code (POSTPARTUM_HTN, always billed CPT 99453 first) is paid a
    realized amount that does **not** move with the individual episode's
    score: a fixed fraction (``VALUATION_FACTOR``) of what a curve fitted on
    the comparator cohort's (score, reference-release PFS) pairs predicts at
    the study cohort's own median score, times the payer's multiple. That is
    the phenomenon under study: a code paid flat while the work underneath it
    is not.
  * 99454 (the device-supply code) is billed whenever an episode's own
    ``reading_days`` clears the real CMS threshold (16 of the 30 days), with
    one deliberate exception: episodes in the top slice of the study
    intensity range (``t`` above ``_MISSED_DEVICE_SUPPLY_T``) clear the
    threshold but do not bill it, a genuine documentation-capture gap for
    Method 1's ``device-supply-threshold`` rule to find. 99457/99458 are
    billed exactly at the interactive-minutes thresholds the pack's own
    ``management-increment-*`` rules check, so those two rules produce no
    flags from the generated fixture (the unit tests exercise them directly,
    on hand-built rows) -- only the 99454 gap does, alongside the
    ``sub-threshold-exception-handling`` and ``autonomous-oversight`` rules,
    which fire wherever an episode's own exception mix happens to match their
    conditions.

Because those assumptions are *put in*, the adequacy ratio and signature
pattern that come out are properties of this file, not evidence about remote
patient monitoring. What the fixture validates is the pipeline.

Deterministic: a fixed seed, so regenerating produces byte-identical output.
"""

from __future__ import annotations

import random
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from worth_complexity.synthetic.edi import FACILITY_NPI, PAYEE_NAME, PAYEE_TIN, PAYERS, Payer

if TYPE_CHECKING:
    from worth_complexity.synthetic.scenarios import Scenario

SEED = 20260909

# CONTRACT-PACKS.md: episodes are always priced non-facility. Passed
# explicitly everywhere below rather than relied on as a pipeline default.
LOCALITY = "NY01"
SETTING = "non-facility"

REFERENCE_VINTAGE = (2026, 4)

# What POSTPARTUM_HTN is paid, as a fraction of what the schedule's fitted
# complexity relation pays at the study cohort's own median score. Tuned so
# the *realized* ratio (Method 3 band-expected, not this curve fit) lands
# near the doc's worked example D, 0.48; see the fixture README for the
# actually-achieved figure.
VALUATION_FACTOR = Decimal("0.34")

PERIOD_START = date(2026, 1, 5)
EPISODE_LENGTH_DAYS = 30
"""Every episode is a 30-day monitoring window: ``end_date = start_date + 29``."""
PERIOD_SPREAD_DAYS = 300
"""Start dates spread across this many days of CY2026, so every episode's
30-day window (and every payer's remittance, 21 days after its last day)
still lands inside the pinned CMS release."""

POLICY_PERIOD_START = date(2026, 1, 5)
POLICY_PERIOD_SPREAD_DAYS = 700
POLICY_MONTHS = 24
"""24 months, 2026-01 through 2027-12 (CONTRACT-SEEDS.md's ``policy-change``
scenario): start dates spread across both sides of ``Scenario.policy_date``
(2027-01-01). CY2027 has no CMS archive pinned yet -- see
:func:`worth_complexity.synthetic.pricing.vintage_for_date_clamped`, which
:func:`_assign_payments_scenario` uses instead of ``vintage_for_date``
directly."""

_MISSED_DEVICE_SUPPLY_T = Decimal("0.80")
"""Study episodes at or above this intensity clear the 16-reading-day
device-supply threshold but do not bill 99454 -- see the module docstring."""


@dataclass(frozen=True)
class Program:
    """One of the four service lines: the study line or a comparator program."""

    service_line: str
    specialty: str
    cohort: str  # "study" or "comparator"
    n_episodes: int
    t_range: tuple[float, float]
    """The intensity band this program draws from -- see the module docstring's
    "score(t)" discussion: cardiac-device low, CGM mid, home-dialysis high,
    postpartum-HTN overlapping the upper half of cardiac-device and the lower
    half of CGM."""
    risk_flag: str
    dx: tuple[str, ...]


PROGRAMS = (
    Program(
        "POSTPARTUM_HTN",
        "Obstetrics & Gynecology",
        "study",
        60,
        (0.45, 0.85),
        "PREECLAMPSIA_RISK",
        ("O90.89", "O10.92"),
    ),
    Program(
        "CARDIAC_DEVICE", "Cardiology", "comparator", 36, (0.05, 0.40), "HF", ("I50.9", "Z95.810")
    ),
    Program("CGM", "Endocrinology", "comparator", 36, (0.45, 0.80), "T2DM", ("E11.9", "E11.65")),
    Program(
        "HOME_DIALYSIS", "Nephrology", "comparator", 36, (0.70, 0.98), "CKD", ("N18.6", "Z99.2")
    ),
)

CLINICIANS = {
    "POSTPARTUM_HTN": ["C00901", "C00944"],
    "CARDIAC_DEVICE": ["C02210", "C02255"],
    "CGM": ["C03310", "C03344"],
    "HOME_DIALYSIS": ["C04410", "C04466"],
}


def lerp(lo: float, hi: float, t: float) -> float:
    return lo + (hi - lo) * t


def _primary_cpt(service_line: str, t: float) -> str:
    """The billed code that carries this episode's intensity tier.

    Chosen so real PFS amounts rise with ``t`` within each program: the
    cardiac-device tiers are not in CPT numeric order (93296 prices below
    93295), so the mapping is by price, not by code number.
    """
    if service_line == "POSTPARTUM_HTN":
        return "99453"
    if service_line == "CARDIAC_DEVICE":
        if t < 0.15:
            return "93294"  # $32.79
        if t < 0.28:
            return "93296"  # $37.01
        return "93295"  # $40.27
    if service_line == "CGM":
        return "95251" if t < 0.62 else "95250"  # $39.23 / $178.08
    if service_line == "HOME_DIALYSIS":
        return "90966" if t < 0.84 else "90960"  # $347.59 / $417.07
    msg = f"unknown service line: {service_line}"
    raise ValueError(msg)


@dataclass
class Episode:
    episode_id: str
    patient_id: str
    account_id: str
    start: date
    end: date
    program: Program
    payer: Payer
    clinician_id: str
    t: float

    reading_dttms: list[tuple[datetime, str, str]] = field(
        default_factory=list
    )  # dttm, metric, value
    reading_days: int = 0
    alerts: list[tuple[datetime, int, str, str]] = field(
        default_factory=list
    )  # dttm, severity, rule_id, handled_by
    interventions: list[tuple[datetime, str]] = field(default_factory=list)  # dttm, kind
    time_log: list[tuple[datetime, Decimal, bool]] = field(
        default_factory=list
    )  # dttm, minutes, interactive
    problem_tier: int = 0
    outcomes: tuple[bool, bool, bool, bool] = (
        True,
        False,
        False,
        False,
    )  # controlled, ed_visit, readmission, ed_visit_averted

    cpts: list[tuple[str, str]] = field(default_factory=list)  # (cpt, modifier), billing order
    allowed: dict[str, Decimal] = field(default_factory=dict)
    billed: dict[str, Decimal] = field(default_factory=dict)
    site_npi: str = FACILITY_NPI
    """Which of the three facility NPIs this episode was run out of
    (CONTRACT-SEEDS.md's ``multi-site`` scenario). Defaults to the single
    site every non-multi-site build writes, so ``build_fixture`` -- which
    never touches this field -- keeps writing exactly what it always has."""

    @property
    def primary_cpt(self) -> str:
        return self.cpts[0][0]

    @property
    def interactive_minutes(self) -> Decimal:
        return sum((m for _, m, interactive in self.time_log if interactive), start=Decimal(0))

    @property
    def reading_count(self) -> int:
        return len(self.reading_dttms)


def _alert_rule(metric_bias: float, rng: random.Random) -> str:
    pool = [
        "SBP_HIGH",
        "SBP_LOW",
        "DBP_HIGH",
        "GLUCOSE_HIGH",
        "GLUCOSE_LOW",
        "WEIGHT_DELTA",
        "DEVICE_OFFLINE",
    ]
    idx = min(len(pool) - 1, int(metric_bias * len(pool)))
    return rng.choice(pool[: idx + 1]) if idx else pool[0]


def build_episodes(
    rng: random.Random,
    study_n: int,
    comparator_scale: int = 1,
    *,
    period_start: date = PERIOD_START,
    period_spread_days: int = PERIOD_SPREAD_DAYS,
    comparator_period_start: date | None = None,
    comparator_period_spread_days: int | None = None,
) -> list[Episode]:
    """Draw the cohort. ``study_n`` is the exact count for the sole study
    program (POSTPARTUM_HTN); the three comparator programs scale by
    ``comparator_scale`` off their own :data:`Program.n_episodes`. The two
    cohorts scale independently, the same convention as ``surgical.
    build_encounters``'s ``study_scale``/``comparator_scale``.

    ``period_start``/``period_spread_days``: the window *study* episode
    start dates are drawn from (and comparator episodes too, when
    ``comparator_period_start``/``comparator_period_spread_days`` are left
    ``None``). Default to the module's own one-year CY2026 window, so
    ``build_fixture`` (which never passes any of these) is unaffected; the
    ``policy-change`` scenario passes a 24-month window for the study
    program instead, but keeps the comparator programs in the original
    one-year window -- the same reasoning ``surgical.build_encounters``
    gives for keeping its own comparator cohort out of CY2027 (see that
    function's docstring)."""
    out: list[Episode] = []
    seq = 0
    comp_start = comparator_period_start if comparator_period_start is not None else period_start
    comp_spread = (
        comparator_period_spread_days
        if comparator_period_spread_days is not None
        else period_spread_days
    )
    for program in PROGRAMS:
        clinicians = CLINICIANS[program.service_line]
        n = study_n if program.cohort == "study" else program.n_episodes * comparator_scale
        prog_start = period_start if program.cohort == "study" else comp_start
        prog_spread = period_spread_days if program.cohort == "study" else comp_spread
        for _ in range(n):
            seq += 1
            t = rng.uniform(*program.t_range)
            start = prog_start + timedelta(days=rng.randrange(prog_spread))
            end = start + timedelta(days=EPISODE_LENGTH_DAYS - 1)

            ep = Episode(
                episode_id=f"E{300000 + seq * 149:07d}",
                patient_id=f"P{5000 + seq * 11:07d}",
                account_id=f"A9{200000 + seq * 337:06d}",
                start=start,
                end=end,
                program=program,
                payer=rng.choice(PAYERS),
                clinician_id=rng.choice(clinicians),
                t=t,
            )

            # -- device_readings: reading_days independent of total count, so
            # the 16-day device-supply threshold has real variance to cross.
            reading_days = max(1, round(lerp(2, 29, t)))
            total_readings = max(reading_days, round(120 * t))
            per_day = total_readings // reading_days
            extra = total_readings - per_day * reading_days
            day_offsets = sorted(rng.sample(range(EPISODE_LENGTH_DAYS), k=reading_days))
            metrics = (
                ["SBP", "DBP", "GLUCOSE", "WEIGHT"]
                if program.service_line != "HOME_DIALYSIS"
                else ["WEIGHT", "DEVICE_CHECK"]
            )
            for day_i, offset in enumerate(day_offsets):
                n_today = per_day + (1 if day_i < extra else 0)
                for k in range(max(1, n_today)):
                    hour = 7 + (k * 3) % 15
                    dttm = datetime(start.year, start.month, start.day) + timedelta(
                        days=offset, hours=hour, minutes=rng.randrange(0, 59)
                    )
                    metric = metrics[k % len(metrics)]
                    value = str(rng.randint(60, 180))
                    ep.reading_dttms.append((dttm, metric, value))
            ep.reading_days = reading_days

            # -- alerts
            n_alerts = max(1, round(lerp(1, 6, t)))
            clinician_prob = 0.25 + 0.55 * t
            for _i in range(n_alerts):
                dttm = datetime(start.year, start.month, start.day) + timedelta(
                    days=rng.randrange(EPISODE_LENGTH_DAYS), hours=rng.randrange(6, 20)
                )
                severity = rng.choices([1, 2, 3], weights=[max(0.05, 1.1 - t), 0.55, max(0.05, t)])[
                    0
                ]
                handled = "CLINICIAN" if rng.random() < clinician_prob else "AUTONOMOUS"
                ep.alerts.append((dttm, severity, _alert_rule(t, rng), handled))
                if handled == "CLINICIAN" and rng.random() < 0.7:
                    kind = rng.choice(["MED_TITRATION", "EVALUATION", "ORDER", "MESSAGE"])
                    ep.interventions.append((dttm + timedelta(minutes=15), kind))

            # -- extra interventions beyond the alert-triggered ones
            extra_interventions = max(0, round(lerp(0, 2, t)))
            for _ in range(extra_interventions):
                dttm = datetime(start.year, start.month, start.day) + timedelta(
                    days=rng.randrange(EPISODE_LENGTH_DAYS), hours=rng.randrange(6, 20)
                )
                ep.interventions.append(
                    (dttm, rng.choice(["MED_TITRATION", "EVALUATION", "ORDER", "MESSAGE"]))
                )

            # -- time_log: one interactive entry, one non-interactive. Real
            # per-episode jitter on top of the ``t``-driven base, wide enough
            # that some episodes dip under the 20-minute treatment-management
            # threshold even at moderate ``t`` -- without it every study
            # episode clears 20 minutes and the pack's
            # ``sub-threshold-exception-handling`` rule (which needs at least
            # one clinician-handled exception *and* fewer than 20 interactive
            # minutes) never has anything to fire on.
            interactive_minutes = Decimal(max(0, round(lerp(2, 55, t) + rng.uniform(-18, 18))))
            extra_minutes = Decimal(round(lerp(2, 15, t)))
            log_day1 = start + timedelta(days=rng.randrange(5, 25))
            log_day2 = start + timedelta(days=rng.randrange(5, 25))
            ep.time_log.append(
                (
                    datetime(log_day1.year, log_day1.month, log_day1.day, 10, 0),
                    interactive_minutes,
                    True,
                )
            )
            ep.time_log.append(
                (datetime(log_day2.year, log_day2.month, log_day2.day, 14, 0), extra_minutes, False)
            )

            # -- problem list
            ep.problem_tier = max(0, min(3, round(3 * t + rng.uniform(-0.4, 0.4))))

            # -- outcomes
            controlled = rng.random() < 0.88
            ed_visit = rng.random() < 0.12
            ed_averted = ed_visit and rng.random() < 0.5
            readmission = rng.random() < 0.08
            ep.outcomes = (controlled, ed_visit, readmission, ed_averted)

            # -- billing
            cpt = _primary_cpt(program.service_line, t)
            if program.service_line == "POSTPARTUM_HTN":
                cpts = [("99453", "")]
                bill_device_supply = (
                    reading_days >= 16 and Decimal(str(t)) < _MISSED_DEVICE_SUPPLY_T
                )
                if bill_device_supply:
                    cpts.append(("99454", ""))
                if interactive_minutes >= 20:
                    cpts.append(("99457", ""))
                    if interactive_minutes >= 40:
                        cpts.append(("99458", ""))
            else:
                cpts = [(cpt, "")]
            ep.cpts = cpts

            out.append(ep)
    out.sort(key=lambda e: (e.start, e.episode_id))
    return out


def stamp(dt: datetime) -> str:
    return dt.strftime("%m/%d/%Y %H:%M:%S")


def write_clinical(
    out: Path, episodes: list[Episode], *, drop_time_log: frozenset[str] = frozenset()
) -> None:
    """``drop_time_log``: episode ids to write no ``time_log.txt`` rows for
    at all -- site C's multi-site documentation gap (CONTRACT-SEEDS.md's
    catalog). Withholding the whole log, rather than thinning it, is what
    makes ``episodes.py``'s ``oversight_minutes`` marker genuinely absent
    for that episode (decision 6's default-to-zero) rather than a real,
    smaller total. Empty by default, so ``build_fixture`` writes every
    episode's full time log, unchanged."""
    out.mkdir(parents=True, exist_ok=True)

    episode_hdr = (
        "episode_id|patient_id|billing_account_id|start_date|end_date|service_line|specialty|"
        "cohort|site_npi|clinician_id|program_id|escalation_protocol_version"
    )
    proc_hdr = "episode_id|cpt|modifier|sequence"
    readings_hdr = "episode_id|reading_dttm|metric|value"
    alerts_hdr = "episode_id|alert_dttm|severity|rule_id|handled_by"
    interventions_hdr = "episode_id|intervention_dttm|kind"
    time_log_hdr = "episode_id|log_dttm|minutes|interactive|clinician_id"
    problem_hdr = "patient_id|flag|tier"
    outcomes_hdr = "episode_id|day30_controlled|ed_visit|readmission|ed_visit_averted"
    pat_hdr = "pat_id|birth_date|sex|zip5|county"

    episode_rows = [episode_hdr]
    proc_rows = [proc_hdr]
    readings_rows = [readings_hdr]
    alert_rows = [alerts_hdr]
    intervention_rows = [interventions_hdr]
    time_log_rows = [time_log_hdr]
    problem_rows = [problem_hdr]
    outcomes_rows = [outcomes_hdr]
    pat_rows = [pat_hdr]

    counties = [("10029", "New York"), ("11215", "Kings"), ("10456", "Bronx"), ("11106", "Queens")]

    for i, e in enumerate(episodes):
        p = e.program
        episode_rows.append(
            "|".join(
                [
                    e.episode_id,
                    e.patient_id,
                    e.account_id,
                    f"{e.start.strftime('%m/%d/%Y')} 00:00:00",
                    f"{e.end.strftime('%m/%d/%Y')} 00:00:00",
                    p.service_line,
                    p.specialty,
                    p.cohort,
                    e.site_npi,
                    e.clinician_id,
                    f"PRG-{p.service_line}",
                    "v2.3",
                ]
            )
        )
        for seq, (cpt, mod) in enumerate(e.cpts, start=1):
            proc_rows.append("|".join([e.episode_id, cpt, mod, str(seq)]))
        for dttm, metric, value in e.reading_dttms:
            readings_rows.append("|".join([e.episode_id, stamp(dttm), metric, value]))
        for dttm, severity, rule_id, handled in e.alerts:
            alert_rows.append(
                "|".join([e.episode_id, stamp(dttm), str(severity), rule_id, handled])
            )
        for dttm, kind in e.interventions:
            intervention_rows.append("|".join([e.episode_id, stamp(dttm), kind]))
        for dttm, minutes, interactive in () if e.episode_id in drop_time_log else e.time_log:
            time_log_rows.append(
                "|".join(
                    [
                        e.episode_id,
                        stamp(dttm),
                        f"{minutes:.0f}",
                        "Y" if interactive else "N",
                        e.clinician_id,
                    ]
                )
            )
        if e.problem_tier > 0:
            problem_rows.append("|".join([e.patient_id, p.risk_flag, str(e.problem_tier)]))
        controlled, ed_visit, readmission, ed_averted = e.outcomes
        outcomes_rows.append(
            "|".join(
                [
                    e.episode_id,
                    "Y" if controlled else "N",
                    "Y" if ed_visit else "N",
                    "Y" if readmission else "N",
                    "Y" if ed_averted else "N",
                ]
            )
        )
        zip5, county = counties[i % len(counties)]
        sex = "F" if p.service_line == "POSTPARTUM_HTN" else ("M" if i % 3 == 0 else "F")
        birth = date(e.start.year - (28 + i % 45), 1 + i % 12, 1)
        pat_rows.append(f"{e.patient_id}|{birth.strftime('%m/%Y')}|{sex}|{zip5}|{county}")

    for name, rows in [
        ("episode.txt", episode_rows),
        ("episode_proc.txt", proc_rows),
        ("device_readings.txt", readings_rows),
        ("alerts.txt", alert_rows),
        ("interventions.txt", intervention_rows),
        ("time_log.txt", time_log_rows),
        ("problem_list.txt", problem_rows),
        ("outcomes.txt", outcomes_rows),
        ("patient_lds.txt", pat_rows),
    ]:
        (out / name).write_text("\n".join(rows) + "\n", encoding="utf-8")


def assign_payments(episodes: list[Episode], scores: dict[str, int], rng: random.Random) -> None:
    """Set each episode's allowed amount.

    Comparator episodes get the payer's multiple of the real PFS amount for
    their own primary code, at the vintage in force on the service date --
    the same convention the surgical and visit generators use, so the
    payer-multiplier recovery test works identically here. The study code is
    paid ``VALUATION_FACTOR`` of what a curve fitted on the comparator
    cohort's (score, reference-release PFS) pairs predicts at the study
    cohort's own median score: one number, reused for every study episode
    (times the payer's multiple and a little noise), which is what makes
    Method 0 flat on it regardless of the individual episode's score.
    """
    from worth_fees import expected_allowed
    from worth_fees.sources import vintage_for_date

    from worth_complexity.curve import fit

    def pfs(cpt: str, year: int, quarter: int) -> Decimal:
        return expected_allowed(cpt, [], LOCALITY, SETTING, year, quarter).amount

    at_date: dict[str, Decimal] = {}
    points: list[tuple[Decimal, Decimal]] = []
    for e in episodes:
        if e.program.cohort != "comparator":
            continue
        v = vintage_for_date(e.start)
        at_date[e.episode_id] = pfs(e.primary_cpt, v.rule_year, v.quarter)
        points.append((Decimal(scores[e.episode_id]), pfs(e.primary_cpt, *REFERENCE_VINTAGE)))
    curve = fit("generator/episode-schedule", tuple(points))

    study_scores = sorted(scores[e.episode_id] for e in episodes if e.program.cohort == "study")
    median_score = Decimal(study_scores[len(study_scores) // 2])
    study_base = (
        curve.predict(median_score, allow_extrapolation=True) * VALUATION_FACTOR
    ).quantize(Decimal("0.01"))

    for e in episodes:
        base = at_date[e.episode_id] if e.program.cohort == "comparator" else study_base
        noise = Decimal(str(round(rng.uniform(0.985, 1.015), 4)))
        primary_allowed = (base * e.payer.multiplier * noise).quantize(Decimal("0.01"))
        e.allowed[e.primary_cpt] = primary_allowed
        for cpt, _ in e.cpts[1:]:
            # Secondary RPM codes (99454/99457/99458) are billed and paid, but
            # never feed the adequacy ratio (only the primary line does --
            # ``LinkedEncounter.realized``); a modest, plausible fraction of
            # the primary line's amount is enough for a consistent claim.
            secondary_noise = Decimal(str(round(rng.uniform(0.9, 1.1), 4)))
            e.allowed[cpt] = (primary_allowed * Decimal("0.55") * secondary_noise).quantize(
                Decimal("0.01")
            )


def write_remittance(out: Path, episodes: list[Episode]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    groups: dict[tuple[str, str], list[Episode]] = {}
    for e in episodes:
        groups.setdefault((e.payer.payer_id, e.start.strftime("%Y%m")), []).append(e)

    for control, (_key, members) in enumerate(sorted(groups.items()), start=101):
        payer = members[0].payer
        pay_date = max(e.start for e in members) + timedelta(days=21 + EPISODE_LENGTH_DAYS)
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
        for seq, e in enumerate(sorted(members, key=lambda x: x.account_id), start=1):
            claim_allowed = sum(e.allowed[cpt] for cpt, _ in e.cpts)
            patient = (claim_allowed * payer.coinsurance).quantize(Decimal("0.01"))
            claim_paid = claim_allowed - patient
            billed_total = Decimal(0)
            lines: list[str] = []
            for cpt, mod in e.cpts:
                allowed = e.allowed[cpt]
                billed = (max(allowed, Decimal("25.00")) * Decimal("3.2")).quantize(Decimal("0.01"))
                billed_total += billed
                e.billed[cpt] = billed
                composite = f"HC:{cpt}" + (f":{mod}" if mod else "")
                line_patient = (allowed * payer.coinsurance).quantize(Decimal("0.01"))
                lines.append(f"SVC*{composite}*{billed:.2f}*{allowed - line_patient:.2f}**1~")
                lines.append(f"DTM*472*{e.end.strftime('%Y%m%d')}~")
                lines.append(f"AMT*B6*{allowed:.2f}~")
                if allowed == 0:
                    lines.append(f"CAS*CO*97*{billed:.2f}~")
                    lines.append("LQ*HE*N19~")
                else:
                    lines.append(f"CAS*CO*45*{billed - allowed:.2f}~")
                    if line_patient > 0:
                        lines.append(f"CAS*PR*2*{line_patient:.2f}~")
            body.append(
                f"CLP*{e.account_id}*1*{billed_total:.2f}*{claim_paid:.2f}*{patient:.2f}*"
                f"{payer.claim_filing}*{pay_date.strftime('%Y')}{control}{seq:05d}*11*1~"
            )
            body.append(f"NM1*QC*1*PATIENT*SAMPLE****MI*{e.patient_id}~")
            body.append(f"REF*EA*{e.episode_id}~")
            body.append(f"DTM*232*{e.end.strftime('%Y%m%d')}~")
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
    return "*".join(fields) + "~"


def write_claims(out: Path, episodes: list[Episode]) -> None:
    """The ANSI X12 837P professional claims behind each 835 remittance.

    Same shape and the same join (``CLM01 == CLP01 == episode.billing_account_id``)
    as the surgical generator's claims -- see ``write_remittance`` above and
    the module docstring for why this is a retargeted copy rather than an
    import.
    """
    out.mkdir(parents=True, exist_ok=True)
    groups: dict[tuple[str, str], list[Episode]] = {}
    for e in episodes:
        groups.setdefault((e.payer.payer_id, e.start.strftime("%Y%m")), []).append(e)

    seg = _edi_seg
    for control, (_key, members) in enumerate(sorted(groups.items()), start=101):
        payer = members[0].payer
        pay_date = max(e.start for e in members) + timedelta(days=21 + EPISODE_LENGTH_DAYS)
        submit_date = max(e.end for e in members) + timedelta(days=5)
        assert submit_date < pay_date

        body: list[str] = [
            seg("ST", "837", f"{control:04d}", "005010X222A1"),
            seg(
                "BHT", "0019", "00", f"{control:04d}", submit_date.strftime("%Y%m%d"), "0800", "CH"
            ),
            seg("NM1", "41", "2", PAYEE_NAME, "", "", "", "", "46", PAYEE_TIN),
            seg("NM1", "40", "2", payer.name, "", "", "", "", "46", payer.payer_id),
            seg("HL", "1", "", "20", "1"),
            seg("NM1", "85", "2", PAYEE_NAME, "", "", "", "", "XX", FACILITY_NPI),
            seg("REF", "EI", PAYEE_TIN),
        ]

        for n, e in enumerate(sorted(members, key=lambda x: x.account_id), start=2):
            body.append(seg("HL", str(n), "1", "22", "0"))
            body.append(seg("SBR", "P", "18", "", "", "", "", "", "", payer.claim_filing))
            body.append(seg("NM1", "IL", "1", "PATIENT", "SAMPLE", "", "", "", "MI", e.patient_id))
            body.append(seg("NM1", "PR", "2", payer.name, "", "", "", "", "PI", payer.payer_id))

            total_charge = sum((e.billed[cpt] for cpt, _ in e.cpts), start=Decimal(0))
            body.append(
                seg(
                    "CLM", e.account_id, f"{total_charge:.2f}", "", "", "12:B:1", "Y", "A", "Y", "Y"
                )
            )

            dx = e.program.dx
            dx_composites = [f"ABK:{dx[0]}", *(f"ABF:{code}" for code in dx[1:])]
            body.append(seg("HI", *dx_composites))

            for line, (cpt, mod) in enumerate(e.cpts, start=1):
                composite = ":".join(["HC", cpt, *([mod] if mod else [])])
                body.append(seg("LX", str(line)))
                body.append(seg("SV1", composite, f"{e.billed[cpt]:.2f}", "UN", "1", "", "", "1"))
                body.append(seg("DTP", "472", "D8", e.end.strftime("%Y%m%d")))

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


def build_fixture(root: Path, scale: int = 1, *, seed: int = SEED) -> None:
    """Build the committed episode-synthetic fixture (or a scaled variant of
    its exact shape) into ``root``. The original generator's ``main()`` body,
    parameterized -- RNG call order untouched, so ``scale=1, seed=SEED``
    stays byte-identical to the committed fixture. See :func:`build` for the
    scenario-aware entry point."""
    for owned in ("clinical", "remittance", "claims"):
        shutil.rmtree(root / owned, ignore_errors=True)

    rng = random.Random(seed)
    episodes_ = build_episodes(rng, study_n=PROGRAMS[0].n_episodes * scale, comparator_scale=scale)

    clinical = root / "clinical"

    from worth_complexity import episodes as episodes_mod
    from worth_complexity import rulepack, scoring
    from worth_complexity.models import LAYER_A

    pack = rulepack.load("episode-rpm-v1")

    def score_all() -> dict[str, int]:
        write_clinical(clinical, episodes_)
        extract = episodes_mod.read_extract(clinical)
        encounters = extract.encounters()
        marker_sets = extract.markers(encounters)
        return {
            e.encounter_id: scoring.score(e, marker_sets[e.encounter_id], pack, LAYER_A).score.value
            for e in encounters
        }

    scores = score_all()

    assign_payments(episodes_, scores, rng)
    write_remittance(root / "remittance", episodes_)
    write_claims(root / "claims", episodes_)

    remits = len(list((root / "remittance").glob("*.edi")))
    claims = len(list((root / "claims").glob("*.edi")))
    study = [e for e in episodes_ if e.program.cohort == "study"]
    comparator = [e for e in episodes_ if e.program.cohort == "comparator"]
    n_comparator_programs = len({p.service_line for p in PROGRAMS if p.cohort == "comparator"})
    study_codes = sorted({e.primary_cpt for e in study})
    study_scores = [scores[e.episode_id] for e in study]
    comparator_scores = [scores[e.episode_id] for e in comparator]

    print(f"{len(episodes_)} episodes -> {root}")
    print(f"  study      {len(study)} POSTPARTUM_HTN, primary code(s) {study_codes}")
    print(f"  comparator {len(comparator)} across {n_comparator_programs} programs")
    print(f"  remittance {remits} 835 files, claims {claims} 837 files")
    print(
        f"  scores     study {min(study_scores)}-{max(study_scores)}, "
        f"comparator {min(comparator_scores)}-{max(comparator_scores)}"
    )


# ---------------------------------------------------------------------------
# Scenario-aware entry point (CONTRACT-SEEDS.md, "worth track S1")
# ---------------------------------------------------------------------------


def _assign_payments_scenario(
    episodes: list[Episode], scores: dict[str, int], rng: random.Random, scenario: Scenario
) -> None:
    """:func:`assign_payments`, generalized to every payment rule the
    catalog names (:mod:`worth_complexity.synthetic.pricing`) instead of
    always ``flat-at-median-share``'s exact historical formula. Comparator
    pricing, the curve fit and the secondary-line convention are unchanged
    from :func:`assign_payments`, except that the fee-schedule lookup is
    clamped for any service date past CY2026 (see :func:`worth_complexity.
    synthetic.pricing.vintage_for_date_clamped`), and ``policy-change``'s own
    4% fee-schedule step is applied to every episode's own base amount once
    its start date reaches ``scenario.policy_date`` (the same treatment
    ``surgical._assign_payments_scenario`` gives its own base amount)."""
    from worth_fees import expected_allowed

    from worth_complexity.curve import fit
    from worth_complexity.synthetic import pricing

    def pfs(cpt: str, year: int, quarter: int) -> Decimal:
        return expected_allowed(cpt, [], LOCALITY, SETTING, year, quarter).amount

    at_date: dict[str, Decimal] = {}
    points: list[tuple[Decimal, Decimal]] = []
    for e in episodes:
        if e.program.cohort != "comparator":
            continue
        v = pricing.vintage_for_date_clamped(e.start)
        at_date[e.episode_id] = pfs(e.primary_cpt, v.rule_year, v.quarter)
        points.append((Decimal(scores[e.episode_id]), pfs(e.primary_cpt, *REFERENCE_VINTAGE)))
    curve = fit("generator/episode-schedule", tuple(points))

    study_scores = sorted(scores[e.episode_id] for e in episodes if e.program.cohort == "study")
    median_score = Decimal(study_scores[len(study_scores) // 2]) if study_scores else Decimal(0)
    mean_score = (
        Decimal(sum(study_scores)) / Decimal(len(study_scores)) if study_scores else Decimal(0)
    )

    policy_date = scenario.policy_date
    for e in episodes:
        if e.program.cohort == "comparator":
            base = at_date[e.episode_id]
        else:
            base = pricing.price_study(
                scenario.payment_rule,
                scenario.payment_share,
                own_score=Decimal(scores[e.episode_id]),
                mean_code_score=mean_score,
                median_study_score=median_score,
                curve=curve,
            )
        if policy_date is not None and e.start >= policy_date:
            base = base * pricing.FEE_SCHEDULE_STEP
        noise = Decimal(str(round(rng.uniform(0.985, 1.015), 4)))
        primary_allowed = (base * e.payer.multiplier * noise).quantize(Decimal("0.01"))
        e.allowed[e.primary_cpt] = primary_allowed
        for cpt, _mod in e.cpts[1:]:
            secondary_noise = Decimal(str(round(rng.uniform(0.9, 1.1), 4)))
            e.allowed[cpt] = (primary_allowed * Decimal("0.55") * secondary_noise).quantize(
                Decimal("0.01")
            )


def _thin_cohorts(episodes: list[Episode]) -> list[Episode]:
    """8 study episodes (POSTPARTUM_HTN's only billed code); one comparator
    code down to 5 episodes; one payer (the first, UHC) capped at 4
    comparator episodes total -- the episode-class reading of ``surgical.
    _thin_cohorts``, grouped by the actually-billed primary code rather than
    by program, since a program's own primary code can vary by intensity
    (``_primary_cpt``)."""
    by_code: dict[str, list[Episode]] = {}
    for e in episodes:
        by_code.setdefault(e.primary_cpt, []).append(e)
    study_codes = sorted(c for c in by_code if by_code[c][0].program.cohort == "study")
    comparator_codes = sorted(c for c in by_code if by_code[c][0].program.cohort == "comparator")

    thinned: list[Episode] = []
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

    thinned.sort(key=lambda e: (e.start, e.episode_id))
    return thinned


def build(out_dir: Path, *, scenario: Scenario, seed: int | None = None, scale: int = 1) -> None:
    """The scenario-aware entry point ``worth-cli synth`` calls
    (CONTRACT-SEEDS.md's "worth track S1"), the episode-class counterpart of
    ``surgical.build``. Targets ``scenarios.SCENARIO_STUDY_PER_CODE`` study
    episodes (POSTPARTUM_HTN's only billed code) and the generator's own
    normal (unscaled) comparator counts, except when ``scenario.thin_cohorts``
    overrides both."""
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
        study_n = 12  # 12 episodes, thinned to 8 below
    if scenario.policy_date is not None:
        episodes_ = build_episodes(
            rng,
            study_n=study_n,
            comparator_scale=1,
            period_start=POLICY_PERIOD_START,
            period_spread_days=POLICY_PERIOD_SPREAD_DAYS,
            comparator_period_start=PERIOD_START,
            comparator_period_spread_days=PERIOD_SPREAD_DAYS,
        )
        from worth_complexity.synthetic import knobs

        study_episodes = [e for e in episodes_ if e.program.cohort == "study"]
        knobs.stratify_study_months(
            study_episodes,
            period_start=POLICY_PERIOD_START,
            months=POLICY_MONTHS,
            rng=rng,
            date_attr="start",
        )
        for e in study_episodes:
            e.end = e.start + timedelta(days=EPISODE_LENGTH_DAYS - 1)
        episodes_.sort(key=lambda e: (e.start, e.episode_id))
    else:
        episodes_ = build_episodes(rng, study_n=study_n, comparator_scale=1)
    if scenario.thin_cohorts:
        episodes_ = _thin_cohorts(episodes_)

    m1_flags: dict[str, int] | None = None
    if scenario.m1_omission_rate:
        from worth_complexity.synthetic import knobs

        dropped = knobs.omit_secondary_codes(episodes_, rng=rng, rate=scenario.m1_omission_rate)
        study_n = sum(1 for e in episodes_ if e.program.cohort == "study")
        m1_flags = {"missed": dropped, "mismatched": 0, "no_code": 0, "n": study_n}

    drop_time_log: frozenset[str] = frozenset()
    if scenario.multi_site:
        sites.assign_sites(episodes_, rng=rng)
        drop_time_log = sites.sample_ids(
            episodes_,
            rng=rng,
            site=sites.SITE_C,
            rate=sites.SITE_C_TIME_LOG_DROP_RATE,
            id_of=lambda e: e.episode_id,
        )

    clinical = out_dir / "clinical"

    from worth_complexity import episodes as episodes_mod
    from worth_complexity import rulepack, scoring
    from worth_complexity.models import LAYER_A

    pack = rulepack.load("episode-rpm-v1")
    write_clinical(clinical, episodes_, drop_time_log=drop_time_log)
    extract = episodes_mod.read_extract(clinical)
    encounters = extract.encounters()
    marker_sets = extract.markers(encounters)
    scores = {
        e.encounter_id: scoring.score(e, marker_sets[e.encounter_id], pack, LAYER_A).score.value
        for e in encounters
    }

    _assign_payments_scenario(episodes_, scores, rng, scenario)
    write_remittance(out_dir / "remittance", episodes_)
    write_claims(out_dir / "claims", episodes_)

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
            step = float(pricing.FEE_SCHEDULE_STEP)
            ratio_bands = {
                "study_pre": ratio_band,
                "study_post": [round(v * step, 4) for v in ratio_band],
            }
        if scenario.dirt or scenario.multi_site:
            missingness = planted.missingness_max(clinical, "episode")
        if scenario.multi_site:
            site_missingness = planted.missingness_by_site(clinical, "episode")
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
            encounter_class="episode",
            seed=seed_value,
            generator_version=__version__,
            planted=planted_truth,
            purpose=truth_mod.render_purpose(scenario.name, planted_truth),
            notes=scenario.notes,
        ),
    )
