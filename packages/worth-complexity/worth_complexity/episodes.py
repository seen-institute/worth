"""Reading a partner's 30-day remote-monitoring episode extract.

The episode class's whole premise, per the methodology, is that a monitoring
program's complexity is legible "from device and alert logs alone": there is
no operative note to read the way ``markers.py`` reads one, and there is no
E/M time attestation to read the way the visit class's threshold facts will.
Every marker below is ``structured``, computed from the nine delimited tables
CONTRACT-PACKS.md's episode section names, and a narrative note dataset is
never required, only tolerated when a partner happens to deliver one (kept
around for whatever future rule pack wants it, never read by this one).

Method 1 for this class is not the surgical pack's note-phrase cross-check
either: it is the same threshold-rules-over-structured-facts mechanism the
visit class uses (``method1.evaluate_work_rules``), so :meth:`EpisodeExtract.facts`
is the flat mapping that block reads against, not an empty dict the way the
surgical adapter's is.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from worth_complexity import cases
from worth_complexity.cases import (
    ExtractError,
    Table,
    parse_date,
    parse_decimal,
    parse_dttm,
    parse_int,
)
from worth_complexity.models import Cohort, Encounter, Marker, SourceRef
from worth_complexity.money import money_context

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from worth_complexity.notes import Note
    from worth_complexity.rulepack import RulePack

__all__ = [
    "EXTRACTOR_VERSION",
    "EpisodeExtract",
    "encounters",
    "markers",
    "read_episode_extract",
    "read_extract",
]

EXTRACTOR_VERSION = "0.1.0"

_HANDLED_CLINICIAN = "CLINICIAN"
_HANDLED_AUTONOMOUS = "AUTONOMOUS"
_YES = "Y"

_TABLE_NAMES = (
    "episode.txt",
    "episode_proc.txt",
    "device_readings.txt",
    "alerts.txt",
    "interventions.txt",
    "time_log.txt",
    "problem_list.txt",
    "outcomes.txt",
    "patient_lds.txt",
)


@dataclass(frozen=True, slots=True)
class EpisodeExtract:
    """The nine episode tables and the (optional) note dataset, as delivered.

    Implements the :class:`worth_complexity.extracts.ClinicalExtract`
    protocol directly -- unlike :class:`worth_complexity.extracts.SurgicalExtract`,
    there is no pre-existing ``episodes.py`` to adapt, so this class both holds
    the raw tables and answers every question :mod:`pipeline` asks of an
    extract.
    """

    episode: Table
    episode_proc: Table
    device_readings: Table
    alerts: Table
    interventions: Table
    time_log: Table
    problem_list: Table
    outcomes: Table
    patient_lds: Table
    notes: tuple[Note, ...]

    @property
    def encounter_class(self) -> str:
        return "episode"

    @property
    def tables(self) -> tuple[Table, ...]:
        return (
            self.episode,
            self.episode_proc,
            self.device_readings,
            self.alerts,
            self.interventions,
            self.time_log,
            self.problem_list,
            self.outcomes,
            self.patient_lds,
        )

    def notes_for(self, encounter_id: str, note_types: frozenset[str]) -> tuple[Note, ...]:
        return tuple(
            n for n in self.notes if n.encounter_id == encounter_id and n.note_type in note_types
        )

    def file_hashes(self) -> tuple[tuple[str, str], ...]:
        pairs = {(t.name, t.sha256) for t in self.tables}
        pairs |= {(n.filename, n.sha256) for n in self.notes}
        return tuple(sorted(pairs))

    def encounters(self) -> tuple[Encounter, ...]:
        return encounters(self)

    def markers(
        self,
        encounters_: tuple[Encounter, ...],
        pack: RulePack | None = None,  # noqa: ARG002
    ) -> dict[str, tuple[Marker, ...]]:
        """Every structured marker for every episode.

        ``pack`` is accepted only to satisfy the protocol: this class has no
        notes dataset to read narrative rules against (the methodology: "from
        device and alert logs alone"), so every marker is structured and
        computed the same way whether or not a pack is given.
        """
        return markers(self, encounters_)

    def facts(self, encounter_id: str) -> Mapping[str, Decimal | str | bool]:
        return _facts_by_episode(self)[encounter_id]


def read_episode_extract(directory: Path) -> EpisodeExtract:
    """Read the whole episode extract from a directory.

    ``notes/`` is optional (decision: "no notes required" -- the methodology
    reads this class from device and alert logs alone); :func:`cases.read_notes`
    already returns ``()`` when ``notes.txt`` is absent, so reusing it gives
    that behaviour for free.
    """
    return EpisodeExtract(
        episode=cases.read_table(
            directory / "episode.txt",
            required=frozenset(
                {
                    "episode_id",
                    "patient_id",
                    "billing_account_id",
                    "start_date",
                    "end_date",
                    "service_line",
                    "specialty",
                    "cohort",
                    "site_npi",
                    "clinician_id",
                }
            ),
        ),
        episode_proc=cases.read_table(directory / "episode_proc.txt"),
        device_readings=cases.read_table(directory / "device_readings.txt"),
        alerts=cases.read_table(directory / "alerts.txt"),
        interventions=cases.read_table(directory / "interventions.txt"),
        time_log=cases.read_table(directory / "time_log.txt"),
        problem_list=cases.read_table(directory / "problem_list.txt"),
        outcomes=cases.read_table(directory / "outcomes.txt"),
        patient_lds=cases.read_table(directory / "patient_lds.txt"),
        notes=cases.read_notes(directory),
    )


# Kept as ``read_extract`` too: every other reader module in this package
# (``cases.read_extract``) is spelled this way, and ``extracts.READERS``
# names the callable it registers, not this module's own name, so either
# spelling would work there -- but a reader importing ``episodes`` directly
# (the way ``tools/make_episode_dataset.py`` does, to score with the real
# scorer while it writes the fixture) should find the same name it would in
# ``cases``.
read_extract = read_episode_extract


def encounters(extract: EpisodeExtract) -> tuple[Encounter, ...]:
    """Build one :class:`Encounter` per 30-day episode."""
    panels: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    for row in extract.episode_proc.rows:
        panels[row["episode_id"]].append(
            (parse_int(row["sequence"], "episode_proc.sequence"), row["cpt"], row["modifier"])
        )

    out: list[Encounter] = []
    for row in extract.episode.rows:
        episode_id = row["episode_id"]
        panel = sorted(panels.get(episode_id, []))
        if not panel:
            msg = f"episode {episode_id}: no procedure lines"
            raise ExtractError(msg)
        start = parse_date(row["start_date"], f"episode {episode_id}.start_date")
        end = parse_date(row["end_date"], f"episode {episode_id}.end_date")
        out.append(
            Encounter(
                encounter_id=episode_id,
                csn=episode_id,
                patient_id=row["patient_id"],
                account_id=row["billing_account_id"],
                service_date=start,
                service_line=row["service_line"],
                specialty=row["specialty"],
                cohort=Cohort(row["cohort"].strip().lower()),
                facility_npi=row["site_npi"],
                primary_cpt=panel[0][1],
                procedures=tuple((cpt, mod) for _, cpt, mod in panel),
                inpatient=False,
                encounter_class="episode",
                clinician_id=row["clinician_id"] or None,
                window_start=start,
                window_end=end,
            )
        )
    return tuple(out)


def _index_by(table: Table, key: str) -> dict[str, list[int]]:
    """Row indices grouped by one column, in table order."""
    out: dict[str, list[int]] = defaultdict(list)
    for i, row in enumerate(table.rows):
        out[row[key]].append(i)
    return out


def _no_rows_ref(table: Table, note: str) -> SourceRef:
    """Where a marker looked for this episode's rows in a table and found
    none. Row 1 is the header; there is no row of this episode's own to
    point at, the same convention ``markers.py`` uses for an unmatched
    narrative rule (``_unmatched``)."""
    return SourceRef(table.name, table.sha256, 1, note)


def _marker(
    encounter: Encounter,
    marker_id: str,
    value: Decimal,
    ref: SourceRef,
    extractor_id: str,
) -> Marker:
    return Marker(
        encounter_id=encounter.encounter_id,
        marker_id=marker_id,
        value=value,
        provenance="structured",
        source_ref=ref,
        extractor_id=extractor_id,
        extractor_version=EXTRACTOR_VERSION,
    )


def _window_days(encounter: Encounter) -> int:
    assert encounter.window_start is not None
    assert encounter.window_end is not None
    return (encounter.window_end - encounter.window_start).days + 1


def _monitoring_intensity(extract: EpisodeExtract, enc: Encounter, rows: list[int]) -> Marker:
    table = extract.device_readings
    count = Decimal(len(rows))
    days = Decimal(_window_days(enc))
    with money_context():
        rate = count / days
    ref = table.ref(rows[0], "reading_dttm") if rows else _no_rows_ref(table, "no readings")
    return _marker(enc, "monitoring_intensity", rate, ref, "device_readings_count")


def _exception_burden(extract: EpisodeExtract, enc: Encounter, rows: list[int]) -> Marker:
    table = extract.alerts
    total = Decimal(0)
    for i in rows:
        total += Decimal(parse_int(table.rows[i]["severity"], f"alerts row {i}.severity"))
    ref = table.ref(rows[0], "severity") if rows else _no_rows_ref(table, "no alerts")
    return _marker(enc, "exception_burden", total, ref, "alerts_severity_sum")


def _clinician_interventions(extract: EpisodeExtract, enc: Encounter, rows: list[int]) -> Marker:
    table = extract.interventions
    ref = table.ref(rows[0], "kind") if rows else _no_rows_ref(table, "no interventions")
    return _marker(enc, "clinician_interventions", Decimal(len(rows)), ref, "interventions_count")


def _oversight_minutes(extract: EpisodeExtract, enc: Encounter, rows: list[int]) -> Marker | None:
    """``None`` when the episode carries no ``time_log.txt`` rows at all --
    the multi-site scenario's site-C documentation gap (CONTRACT-SEEDS.md's
    catalog): a genuinely missing log, not a legitimate zero minutes of
    oversight, so the marker is omitted rather than reported at zero
    (decision 6's "never imputed upward" cuts both ways -- a real absence is
    not a real zero either)."""
    if not rows:
        return None
    table = extract.time_log
    total = Decimal(0)
    for i in rows:
        total += parse_decimal(table.rows[i]["minutes"], f"time_log row {i}.minutes")
    return _marker(
        enc, "oversight_minutes", total, table.ref(rows[0], "minutes"), "time_log_minutes_sum"
    )


def _patient_risk_tier(extract: EpisodeExtract, enc: Encounter, rows: list[int]) -> Marker:
    table = extract.problem_list
    if not rows:
        return _marker(
            enc,
            "patient_risk_tier",
            Decimal(0),
            _no_rows_ref(table, "no active problem list flags"),
            "problem_list_max_tier",
        )
    best_i, best_tier = rows[0], -1
    for i in rows:
        tier = parse_int(table.rows[i]["tier"], f"problem_list row {i}.tier")
        if tier > best_tier:
            best_tier, best_i = tier, i
    with money_context():
        value = Decimal(best_tier) / Decimal(3)
    return _marker(
        enc, "patient_risk_tier", value, table.ref(best_i, "tier"), "problem_list_max_tier"
    )


def _outcome_delivered(extract: EpisodeExtract, enc: Encounter, row_i: int | None) -> Marker:
    table = extract.outcomes
    if row_i is None:
        msg = f"episode {enc.encounter_id}: no outcomes.txt row"
        raise ExtractError(msg)
    row = table.rows[row_i]
    controlled = row["day30_controlled"].strip().upper() == _YES
    ed_visit = row["ed_visit"].strip().upper() == _YES
    ed_averted = row["ed_visit_averted"].strip().upper() == _YES
    readmission = row["readmission"].strip().upper() == _YES
    clean_ed = (not ed_visit) or ed_averted
    with money_context():
        value = (
            Decimal("0.5") * Decimal(1 if controlled else 0)
            + Decimal("0.25") * Decimal(1 if clean_ed else 0)
            + Decimal("0.25") * Decimal(0 if readmission else 1)
        )
    return _marker(
        enc,
        "outcome_delivered",
        value,
        table.ref(row_i, "day30_controlled..ed_visit_averted"),
        "outcomes_composite",
    )


def markers(
    extract: EpisodeExtract, encounters_: tuple[Encounter, ...]
) -> dict[str, tuple[Marker, ...]]:
    """Every Layer A marker this extract produces, keyed by encounter id.

    Every marker is ``structured``: the episode class carries no note dataset
    to read a narrative lane out of (CONTRACT-PACKS.md: "from device and
    alert logs alone"), so unlike the surgical extractor there is no
    ``pack``-gated second pass here at all.
    """
    readings = _index_by(extract.device_readings, "episode_id")
    alerts = _index_by(extract.alerts, "episode_id")
    interventions = _index_by(extract.interventions, "episode_id")
    time_log = _index_by(extract.time_log, "episode_id")
    problems = _index_by(extract.problem_list, "patient_id")
    outcomes: dict[str, int] = {}
    for i, row in enumerate(extract.outcomes.rows):
        outcomes[row["episode_id"]] = i

    out: dict[str, tuple[Marker, ...]] = {}
    for enc in encounters_:
        eid = enc.encounter_id
        row_markers = (
            _monitoring_intensity(extract, enc, readings.get(eid, [])),
            _exception_burden(extract, enc, alerts.get(eid, [])),
            _clinician_interventions(extract, enc, interventions.get(eid, [])),
            _oversight_minutes(extract, enc, time_log.get(eid, [])),
            _patient_risk_tier(extract, enc, problems.get(enc.patient_id, [])),
            _outcome_delivered(extract, enc, outcomes.get(eid)),
        )
        out[eid] = tuple(m for m in row_markers if m is not None)
    return out


def _facts_for(
    extract: EpisodeExtract,
    enc: Encounter,
    reading_rows: list[int],
    time_log_rows: list[int],
    alert_rows: list[int],
) -> dict[str, Decimal | str | bool]:
    reading_days = len(
        {
            parse_dttm(
                extract.device_readings.rows[i]["reading_dttm"], "device_readings.reading_dttm"
            ).date()
            for i in reading_rows
        }
    )
    interactive_minutes = Decimal(0)
    oversight_minutes = Decimal(0)
    for i in time_log_rows:
        row = extract.time_log.rows[i]
        minutes = parse_decimal(row["minutes"], f"time_log row {i}.minutes")
        oversight_minutes += minutes
        if row["interactive"].strip().upper() == _YES:
            interactive_minutes += minutes

    exceptions_handled = sum(
        1
        for i in alert_rows
        if extract.alerts.rows[i]["handled_by"].strip().upper() == _HANDLED_CLINICIAN
    )
    autonomous_exceptions = sum(
        1
        for i in alert_rows
        if extract.alerts.rows[i]["handled_by"].strip().upper() == _HANDLED_AUTONOMOUS
    )
    billed_codes = ",".join(cpt for cpt, _ in enc.procedures)

    return {
        "reading_days": Decimal(reading_days),
        "interactive_minutes": interactive_minutes,
        "oversight_minutes": oversight_minutes,
        "exceptions_handled": Decimal(exceptions_handled),
        "autonomous_exceptions": Decimal(autonomous_exceptions),
        "billed_codes": billed_codes,
    }


def _facts_by_episode(
    extract: EpisodeExtract,
) -> dict[str, Mapping[str, Decimal | str | bool]]:
    readings = _index_by(extract.device_readings, "episode_id")
    time_log = _index_by(extract.time_log, "episode_id")
    alerts = _index_by(extract.alerts, "episode_id")
    out: dict[str, Mapping[str, Decimal | str | bool]] = {}
    for enc in encounters(extract):
        eid = enc.encounter_id
        out[eid] = _facts_for(
            extract, enc, readings.get(eid, []), time_log.get(eid, []), alerts.get(eid, [])
        )
    return out
