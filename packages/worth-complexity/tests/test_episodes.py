"""The episode class (CONTRACT-PACKS.md): ``episodes.py``'s reader, its six
structured markers, and every ``episode-rpm-v1`` work rule in every Method 1
bucket.

Seven hand-built 30-day episodes, written to a temporary directory as the
nine pipe-delimited tables (no ``notes/`` -- the methodology reads this class
"from device and alert logs alone", and that absence is itself one of the
things tested): E1 bills every RPM code its facts would support, so none of
the work rules fire on it (the "present" branch); E2 clears the 16-day
device-supply threshold but never bills 99454, a genuine capture gap; E3
bills both management-increment codes it qualifies for; E4 and E5 isolate
each management-increment rule's own ``missed`` bucket; E6 isolates
``sub-threshold-exception-handling``'s ``mismatched`` bucket; E7 isolates
``autonomous-oversight``'s ``no_code`` bucket. A separate, minimal fixture
with a ``notes/`` directory confirms the reader tolerates one without using
it.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from worth_complexity import method1, rulepack
from worth_complexity.episodes import EpisodeExtract, read_episode_extract
from worth_complexity.extracts import read_extract
from worth_complexity.models import Cohort, Encounter

PACK = rulepack.load("episode-rpm-v1")

START = date(2026, 2, 1)
END = START + timedelta(days=29)  # 30-day window, Feb 1 - Mar 2 (2026 is not a leap year)


def _stamp(d: date, hour: int = 9, minute: int = 0) -> str:
    return f"{d.strftime('%m/%d/%Y')} {hour:02d}:{minute:02d}:00"


def _write(directory: Path, *, with_notes: bool = False) -> None:
    directory.mkdir(parents=True, exist_ok=True)

    episode = [
        "episode_id|patient_id|billing_account_id|start_date|end_date|service_line|specialty|"
        "cohort|site_npi|clinician_id|program_id|escalation_protocol_version",
        f"E1|PT1|A1|{_stamp(START)}|{_stamp(END)}|POSTPARTUM_HTN|Obstetrics & Gynecology|"
        "study|1447890215|CLIN1|PRG-PPH|v1",
        f"E2|PT2|A2|{_stamp(START)}|{_stamp(END)}|POSTPARTUM_HTN|Obstetrics & Gynecology|"
        "study|1447890215|CLIN1|PRG-PPH|v1",
        f"E3|PT3|A3|{_stamp(START)}|{_stamp(END)}|POSTPARTUM_HTN|Obstetrics & Gynecology|"
        "study|1447890215|CLIN1|PRG-PPH|v1",
        f"E4|PT4|A4|{_stamp(START)}|{_stamp(END)}|POSTPARTUM_HTN|Obstetrics & Gynecology|"
        "study|1447890215|CLIN1|PRG-PPH|v1",
        f"E5|PT5|A5|{_stamp(START)}|{_stamp(END)}|POSTPARTUM_HTN|Obstetrics & Gynecology|"
        "study|1447890215|CLIN1|PRG-PPH|v1",
        f"E6|PT6|A6|{_stamp(START)}|{_stamp(END)}|POSTPARTUM_HTN|Obstetrics & Gynecology|"
        "study|1447890215|CLIN1|PRG-PPH|v1",
        f"E7|PT7|A7|{_stamp(START)}|{_stamp(END)}|POSTPARTUM_HTN|Obstetrics & Gynecology|"
        "study|1447890215|CLIN1|PRG-PPH|v1",
    ]
    (directory / "episode.txt").write_text("\n".join(episode) + "\n", encoding="utf-8")

    # E1: 99453+99454+99457 (device supply and first increment both earned
    #     and billed -- the "already submitted" branch of both rules).
    # E2: 99453 only, despite clearing the 16-day device-supply threshold.
    # E3: 99453+99457+99458 (both increments earned and billed).
    # E4: 99453 only, despite 25 interactive minutes (>=20).
    # E5: 99453+99457 only, despite 45 interactive minutes (>=40).
    # E6, E7: 99453 only.
    proc = [
        "episode_id|cpt|modifier|sequence",
        "E1|99453||1",
        "E1|99454||2",
        "E1|99457||3",
        "E2|99453||1",
        "E3|99453||1",
        "E3|99457||2",
        "E3|99458||3",
        "E4|99453||1",
        "E5|99453||1",
        "E5|99457||2",
        "E6|99453||1",
        "E7|99453||1",
    ]
    (directory / "episode_proc.txt").write_text("\n".join(proc) + "\n", encoding="utf-8")

    readings: list[str] = ["episode_id|reading_dttm|metric|value"]
    for i in range(17):  # E1: 17 distinct reading days -> reading_days=17, mi=17/30
        readings.append(f"E1|{_stamp(START + timedelta(days=i))}|SBP|128")
    for i in range(18):  # E2: 18 distinct reading days -> reading_days=18, mi=18/30
        readings.append(f"E2|{_stamp(START + timedelta(days=i))}|SBP|132")
    for i in range(5):
        readings.append(f"E3|{_stamp(START + timedelta(days=i))}|SBP|120")
    for i in range(3):
        readings.append(f"E4|{_stamp(START + timedelta(days=i))}|SBP|118")
    for i in range(2):
        readings.append(f"E5|{_stamp(START + timedelta(days=i))}|SBP|122")
    readings.append(f"E6|{_stamp(START)}|SBP|130")
    readings.append(f"E7|{_stamp(START)}|SBP|130")
    (directory / "device_readings.txt").write_text("\n".join(readings) + "\n", encoding="utf-8")

    alerts = [
        "episode_id|alert_dttm|severity|rule_id|handled_by",
        f"E1|{_stamp(START, 10)}|2|SBP_HIGH|CLINICIAN",
        f"E1|{_stamp(START, 11)}|3|SBP_HIGH|CLINICIAN",
        f"E6|{_stamp(START, 10)}|2|SBP_HIGH|CLINICIAN",
        f"E7|{_stamp(START, 10)}|1|SBP_HIGH|AUTONOMOUS",
    ]
    (directory / "alerts.txt").write_text("\n".join(alerts) + "\n", encoding="utf-8")

    interventions = [
        "episode_id|intervention_dttm|kind",
        f"E1|{_stamp(START, 10, 15)}|MED_TITRATION",
        f"E6|{_stamp(START, 10, 15)}|MESSAGE",
    ]
    (directory / "interventions.txt").write_text("\n".join(interventions) + "\n", encoding="utf-8")

    # (episode_id, interactive_minutes, extra_minutes)
    time_log_plan = [
        ("E1", 25, 5),
        ("E2", 15, 5),
        ("E3", 45, 5),
        ("E4", 25, 5),
        ("E5", 45, 5),
        ("E6", 10, 3),
        ("E7", 5, 2),
    ]
    time_log = ["episode_id|log_dttm|minutes|interactive|clinician_id"]
    for episode_id, interactive, extra in time_log_plan:
        time_log.append(f"{episode_id}|{_stamp(START, 14)}|{interactive}|Y|CLIN1")
        time_log.append(f"{episode_id}|{_stamp(START, 15)}|{extra}|N|CLIN1")
    (directory / "time_log.txt").write_text("\n".join(time_log) + "\n", encoding="utf-8")

    problem_list = [
        "patient_id|flag|tier",
        "PT1|PREECLAMPSIA_RISK|2",
        "PT1|CHRONIC_HTN|1",  # lower tier, present to prove the marker takes the max
        "PT4|PREECLAMPSIA_RISK|3",
    ]
    (directory / "problem_list.txt").write_text("\n".join(problem_list) + "\n", encoding="utf-8")

    # (episode_id, controlled, ed_visit, readmission, ed_visit_averted)
    outcomes_plan = [
        ("E1", "Y", "N", "N", "N"),  # 0.5 + 0.25 + 0.25 = 1.00
        ("E2", "N", "Y", "N", "Y"),  # 0 + 0.25 (averted counts as clean ED) + 0.25 = 0.50
        ("E3", "Y", "N", "Y", "N"),  # 0.5 + 0.25 + 0 = 0.75
        ("E4", "Y", "N", "N", "N"),
        ("E5", "Y", "N", "N", "N"),
        ("E6", "Y", "N", "N", "N"),
        ("E7", "Y", "N", "N", "N"),
    ]
    outcomes = ["episode_id|day30_controlled|ed_visit|readmission|ed_visit_averted"]
    for episode_id, controlled, ed_visit, readmission, averted in outcomes_plan:
        outcomes.append(f"{episode_id}|{controlled}|{ed_visit}|{readmission}|{averted}")
    (directory / "outcomes.txt").write_text("\n".join(outcomes) + "\n", encoding="utf-8")

    patient_lds = ["pat_id|birth_date|sex|zip5|county"]
    for pid in ("PT1", "PT2", "PT3", "PT4", "PT5", "PT6", "PT7"):
        patient_lds.append(f"{pid}|04/1992|F|10029|New York")
    (directory / "patient_lds.txt").write_text("\n".join(patient_lds) + "\n", encoding="utf-8")

    if with_notes:
        (directory / "notes").mkdir(parents=True, exist_ok=True)
        note_text = "MONITORING PROGRAM SUMMARY\n\nNothing this class's pack reads.\n"
        (directory / "notes" / "N_E1.txt").write_text(note_text, encoding="utf-8")
        notes_index = [
            "note_id|log_id|csn|note_type|service_date|author_role|filename",
            f"N_E1|E1|E1|program_summary|{_stamp(START)}|Care Coordinator|N_E1.txt",
        ]
        (directory / "notes.txt").write_text("\n".join(notes_index) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def extract(tmp_path_factory: pytest.TempPathFactory) -> EpisodeExtract:
    directory = tmp_path_factory.mktemp("episode_extract") / "clinical"
    _write(directory)
    return read_episode_extract(directory)


@pytest.fixture(scope="module")
def encounters(extract: EpisodeExtract) -> tuple[Encounter, ...]:
    return extract.encounters()


def _by_id(encounters: tuple[Encounter, ...], encounter_id: str) -> Encounter:
    return next(e for e in encounters if e.encounter_id == encounter_id)


# --------------------------------------------------------------------- reader


def test_episode_txt_dispatches_to_the_episode_reader(extract: EpisodeExtract) -> None:
    assert extract.episode.name == "episode.txt"
    assert extract.encounter_class == "episode"


def test_read_extract_dispatches_the_same_way(tmp_path: Path) -> None:
    _write(tmp_path)
    dispatched = read_extract(tmp_path)
    assert dispatched.encounter_class == "episode"
    assert isinstance(dispatched, EpisodeExtract)


def test_notes_are_optional_and_absent_here(extract: EpisodeExtract) -> None:
    """CONTRACT-PACKS.md: "no notes required (the doc: 'from device and alert
    logs alone')"; ``notes/`` is absent from this fixture entirely."""
    assert extract.notes == ()


def test_a_directory_with_notes_still_reads_and_still_scores(tmp_path: Path) -> None:
    """An episode extract *may* carry a notes dataset (a partner delivering
    one anyway); this class's pack never reads it, but the reader must not
    choke on it, and scoring must come out identical to the no-notes case."""
    _write(tmp_path, with_notes=True)
    with_notes = read_episode_extract(tmp_path)
    assert len(with_notes.notes) == 1
    assert with_notes.notes[0].note_type == "program_summary"

    encs = with_notes.encounters()
    markers_with = with_notes.markers(encs)

    without_dir = tmp_path.parent / "clinical_no_notes"
    _write(without_dir, with_notes=False)
    without_notes = read_episode_extract(without_dir)
    markers_without = without_notes.markers(without_notes.encounters())

    assert markers_with["E1"] == markers_without["E1"]


def test_file_hashes_cover_every_table_and_nothing_else(extract: EpisodeExtract) -> None:
    hashes = extract.file_hashes()
    names = {name for name, _ in hashes}
    assert names == {t.name for t in extract.tables}
    assert len(hashes) == len(extract.tables)
    assert all(len(sha) == 64 for _, sha in hashes)


def test_encounters_carry_the_window_and_the_sequence_one_code(
    encounters: tuple[Encounter, ...],
) -> None:
    e1 = _by_id(encounters, "E1")
    assert e1.primary_cpt == "99453"
    assert e1.procedures == (("99453", ""), ("99454", ""), ("99457", ""))
    assert e1.service_line == "POSTPARTUM_HTN"
    assert e1.cohort is Cohort.STUDY
    assert e1.inpatient is False
    assert e1.encounter_class == "episode"
    assert e1.clinician_id == "CLIN1"
    assert e1.csn == "E1"
    assert e1.account_id == "A1"
    assert e1.service_date == START
    assert e1.window_start == START
    assert e1.window_end == END


# -------------------------------------------------------------------- markers


def test_monitoring_intensity_is_reading_count_over_window_days(
    extract: EpisodeExtract, encounters: tuple[Encounter, ...]
) -> None:
    markers = extract.markers(encounters)
    e1 = next(x for x in markers["E1"] if x.marker_id == "monitoring_intensity")
    assert e1.value == (Decimal(17) / Decimal(30))
    assert e1.provenance == "structured"

    e2 = next(x for x in markers["E2"] if x.marker_id == "monitoring_intensity")
    assert e2.value == (Decimal(18) / Decimal(30))


def test_exception_burden_sums_severity_over_every_alert(
    extract: EpisodeExtract, encounters: tuple[Encounter, ...]
) -> None:
    markers = extract.markers(encounters)
    e1 = next(x for x in markers["E1"] if x.marker_id == "exception_burden")
    assert e1.value == Decimal("5")  # severities 2 + 3

    e3 = next(x for x in markers["E3"] if x.marker_id == "exception_burden")
    assert e3.value == Decimal("0")  # no alerts logged for E3


def test_clinician_interventions_counts_rows(
    extract: EpisodeExtract, encounters: tuple[Encounter, ...]
) -> None:
    markers = extract.markers(encounters)
    e1 = next(x for x in markers["E1"] if x.marker_id == "clinician_interventions")
    assert e1.value == Decimal("1")
    e2 = next(x for x in markers["E2"] if x.marker_id == "clinician_interventions")
    assert e2.value == Decimal("0")


def test_oversight_minutes_sums_every_time_log_row(
    extract: EpisodeExtract, encounters: tuple[Encounter, ...]
) -> None:
    markers = extract.markers(encounters)
    e1 = next(x for x in markers["E1"] if x.marker_id == "oversight_minutes")
    assert e1.value == Decimal("30")  # 25 interactive + 5 non-interactive


def test_patient_risk_tier_takes_the_max_active_flag(
    extract: EpisodeExtract, encounters: tuple[Encounter, ...]
) -> None:
    """PT1 carries PREECLAMPSIA_RISK tier 2 *and* CHRONIC_HTN tier 1; the
    marker is the max, not the first row or the PREECLAMPSIA_RISK-only one."""
    markers = extract.markers(encounters)
    e1 = next(x for x in markers["E1"] if x.marker_id == "patient_risk_tier")
    assert e1.value == (Decimal(2) / Decimal(3))
    assert e1.provenance == "structured"


def test_patient_risk_tier_is_zero_with_no_active_flags(
    extract: EpisodeExtract, encounters: tuple[Encounter, ...]
) -> None:
    markers = extract.markers(encounters)
    e2 = next(x for x in markers["E2"] if x.marker_id == "patient_risk_tier")
    assert e2.value == Decimal("0")


def test_outcome_delivered_composite(
    extract: EpisodeExtract, encounters: tuple[Encounter, ...]
) -> None:
    markers = extract.markers(encounters)
    e1 = next(x for x in markers["E1"] if x.marker_id == "outcome_delivered")
    assert e1.value == Decimal("1.00")

    e3 = next(x for x in markers["E3"] if x.marker_id == "outcome_delivered")
    assert e3.value == Decimal("0.75")  # controlled + clean ED, but readmitted


def test_ed_visit_averted_counts_as_a_clean_ed_leg(
    extract: EpisodeExtract, encounters: tuple[Encounter, ...]
) -> None:
    """E2: not controlled, an ED visit occurred, but it was averted (caught
    by the escalation protocol) and there was no readmission: 0 + 0.25 +
    0.25 = 0.50, not 0.25 -- ``ed_visit_averted`` must override ``ed_visit``
    for the clean-ED-leg component."""
    markers = extract.markers(encounters)
    e2 = next(x for x in markers["E2"] if x.marker_id == "outcome_delivered")
    assert e2.value == Decimal("0.50")


def test_markers_ignore_the_pack_argument(
    extract: EpisodeExtract, encounters: tuple[Encounter, ...]
) -> None:
    """Every marker is structured; there is no narrative lane to gate on a
    pack (CONTRACT-PACKS.md: "from device and alert logs alone")."""
    with_pack = extract.markers(encounters, PACK)
    without_pack = extract.markers(encounters, None)
    assert with_pack == without_pack


# ----------------------------------------------------------------------- facts


def test_facts_for_the_episode_that_bills_everything_it_earns(
    extract: EpisodeExtract,
) -> None:
    facts = extract.facts("E1")
    assert facts["reading_days"] == Decimal("17")
    assert facts["interactive_minutes"] == Decimal("25")
    assert facts["oversight_minutes"] == Decimal("30")
    assert facts["exceptions_handled"] == Decimal("2")
    assert facts["autonomous_exceptions"] == Decimal("0")
    assert facts["billed_codes"] == "99453,99454,99457"


def test_facts_for_the_device_supply_gap(extract: EpisodeExtract) -> None:
    facts = extract.facts("E2")
    assert facts["reading_days"] == Decimal("18")
    assert facts["billed_codes"] == "99453"


# ------------------------------------------------------- work rules, all buckets


def test_device_supply_threshold_present_produces_no_flag(
    extract: EpisodeExtract, encounters: tuple[Encounter, ...]
) -> None:
    """E1 clears 16 reading days and already bills 99454: the rule's
    ``bucket_if_present`` is null, so it produces nothing."""
    e1 = _by_id(encounters, "E1")
    flags = {
        f.rule_id: f
        for f in method1.evaluate_work_rules(e1, extract.facts("E1"), None, PACK.work_rules)
    }
    assert "device-supply-threshold" not in flags


def test_device_supply_threshold_missed(
    extract: EpisodeExtract, encounters: tuple[Encounter, ...]
) -> None:
    e2 = _by_id(encounters, "E2")
    flags = {
        f.rule_id: f
        for f in method1.evaluate_work_rules(e2, extract.facts("E2"), None, PACK.work_rules)
    }
    flag = flags["device-supply-threshold"]
    assert flag.bucket == "missed"
    assert flag.candidate_codes == ("99454",)
    assert flag.vehicle_exists is True
    assert "18" in flag.statement


def test_management_increments_present_produce_no_flags(
    extract: EpisodeExtract, encounters: tuple[Encounter, ...]
) -> None:
    e3 = _by_id(encounters, "E3")
    flags = {
        f.rule_id: f
        for f in method1.evaluate_work_rules(e3, extract.facts("E3"), None, PACK.work_rules)
    }
    assert "management-increment-first-20" not in flags
    assert "management-increment-additional-20" not in flags


def test_management_increment_first_20_missed(
    extract: EpisodeExtract, encounters: tuple[Encounter, ...]
) -> None:
    e4 = _by_id(encounters, "E4")
    flags = {
        f.rule_id: f
        for f in method1.evaluate_work_rules(e4, extract.facts("E4"), None, PACK.work_rules)
    }
    flag = flags["management-increment-first-20"]
    assert flag.bucket == "missed"
    assert flag.candidate_codes == ("99457",)
    # 25 minutes clears 20 but not 40; the additional-20 rule needs
    # billed_any_of 99457, which E4 never billed, so it never fires either.
    assert "management-increment-additional-20" not in flags


def test_management_increment_additional_20_missed(
    extract: EpisodeExtract, encounters: tuple[Encounter, ...]
) -> None:
    e5 = _by_id(encounters, "E5")
    flags = {
        f.rule_id: f
        for f in method1.evaluate_work_rules(e5, extract.facts("E5"), None, PACK.work_rules)
    }
    assert "management-increment-first-20" not in flags  # 99457 already billed
    flag = flags["management-increment-additional-20"]
    assert flag.bucket == "missed"
    assert flag.candidate_codes == ("99458",)


def test_management_increment_additional_20_is_gated_on_99457_billed() -> None:
    """decision behind the rule's own ``billed_any_of``: an episode that
    clears 40 interactive minutes without ever billing 99457 gets no
    additional-20 flag -- that gap belongs to the first-20 rule instead."""
    enc = Encounter(
        encounter_id="X",
        csn="X",
        patient_id="PX",
        account_id="AX",
        service_date=START,
        service_line="POSTPARTUM_HTN",
        specialty="Obstetrics & Gynecology",
        cohort=Cohort.STUDY,
        facility_npi="1447890215",
        primary_cpt="99453",
        procedures=(("99453", ""),),
        inpatient=False,
        encounter_class="episode",
    )
    facts = {"interactive_minutes": Decimal("45")}
    flags = {f.rule_id: f for f in method1.evaluate_work_rules(enc, facts, None, PACK.work_rules)}
    assert "management-increment-additional-20" not in flags
    assert flags["management-increment-first-20"].candidate_codes == ("99457",)


def test_sub_threshold_exception_handling_mismatched(
    extract: EpisodeExtract, encounters: tuple[Encounter, ...]
) -> None:
    e6 = _by_id(encounters, "E6")
    flags = {
        f.rule_id: f
        for f in method1.evaluate_work_rules(e6, extract.facts("E6"), None, PACK.work_rules)
    }
    flag = flags["sub-threshold-exception-handling"]
    assert flag.bucket == "mismatched"
    assert flag.candidate_codes == ()
    assert flag.vehicle_exists is False


def test_autonomous_oversight_no_code(
    extract: EpisodeExtract, encounters: tuple[Encounter, ...]
) -> None:
    e7 = _by_id(encounters, "E7")
    flags = {
        f.rule_id: f
        for f in method1.evaluate_work_rules(e7, extract.facts("E7"), None, PACK.work_rules)
    }
    flag = flags["autonomous-oversight"]
    assert flag.bucket == "no_code"
    assert flag.candidate_codes == ()
    assert flag.vehicle_exists is False
    # E7 never had a clinician-handled exception, so the sub-threshold rule
    # (which needs exceptions_handled >= 1) does not also fire.
    assert "sub-threshold-exception-handling" not in flags


def test_all_three_method1_buckets_are_exercised(
    extract: EpisodeExtract, encounters: tuple[Encounter, ...]
) -> None:
    all_flags = [
        f
        for enc in encounters
        for f in method1.evaluate_work_rules(
            enc, extract.facts(enc.encounter_id), None, PACK.work_rules
        )
    ]
    buckets = {f.bucket for f in all_flags}
    assert buckets == {"missed", "mismatched", "no_code"}
