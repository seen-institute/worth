"""Reading a partner's operative extract.

Everything partner-specific is meant to live behind this module. The five files
read here are the shape Mount Sinai's Clarity extract arrives in. A second
partner with a different shape gets a second reader, and everything downstream
stays unchanged: markers, scoring, linkage, the curve, the ratio. If
partner-specific handling ever leaks past this boundary the flywheel stops, and
partner two costs as much as partner one.

The physical encoding is pipe-delimited text because that is what a hospital
analyst produces most reliably. It is not load-bearing. Parquet or a direct
warehouse read would land in the same records. Only this file would change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from worth_complexity.models import Cohort, Encounter, SourceRef, WorthComplexityError
from worth_complexity.notes import Note
from worth_complexity.provenance import sha256_file

DELIMITER = "|"

_COMPARATOR_SERVICES = frozenset({"GENSURG", "ORTHO", "URO"})


class ExtractError(WorthComplexityError):
    """The partner extract is missing a file, a column, or a required value."""


@dataclass(frozen=True, slots=True)
class Table:
    """One delimited file, read whole, with the hash of the bytes read."""

    name: str
    sha256: str
    columns: tuple[str, ...]
    rows: tuple[dict[str, str], ...]
    row_numbers: tuple[int, ...]
    """Physical line number of each row, header counted as line 1."""

    def ref(self, index: int, column: str) -> SourceRef:
        return SourceRef(self.name, self.sha256, self.row_numbers[index], column)


def read_table(path: Path) -> Table:
    """Read a delimited extract file."""
    if not path.is_file():
        msg = f"missing extract file: {path.name}"
        raise ExtractError(msg)
    text = path.read_text(encoding="utf-8-sig")
    lines = [ln for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()]
    if not lines:
        msg = f"empty extract file: {path.name}"
        raise ExtractError(msg)
    columns = tuple(c.strip() for c in lines[0].split(DELIMITER))
    rows: list[dict[str, str]] = []
    numbers: list[int] = []
    for offset, line in enumerate(lines[1:], start=2):
        cells = [c.strip() for c in line.split(DELIMITER)]
        if len(cells) != len(columns):
            msg = f"{path.name}:{offset}: expected {len(columns)} fields, found {len(cells)}"
            raise ExtractError(msg)
        rows.append(dict(zip(columns, cells, strict=True)))
        numbers.append(offset)
    return Table(path.name, sha256_file(path), columns, tuple(rows), tuple(numbers))


@dataclass(frozen=True, slots=True)
class ClinicalExtract:
    """The operative-log tables and the note dataset, as delivered."""

    or_log: Table
    or_log_proc: Table
    or_staff: Table
    encounter_dx: Table
    patient_lds: Table
    notes: tuple[Note, ...]

    @property
    def tables(self) -> tuple[Table, ...]:
        return (
            self.or_log,
            self.or_log_proc,
            self.or_staff,
            self.encounter_dx,
            self.patient_lds,
        )

    def notes_for(self, encounter_id: str, note_types: frozenset[str]) -> tuple[Note, ...]:
        """This encounter's notes of the given types, in delivery order."""
        return tuple(
            n for n in self.notes if n.encounter_id == encounter_id and n.note_type in note_types
        )


def read_notes(directory: Path) -> tuple[Note, ...]:
    """Read the note dataset: an index table plus one file per document.

    Notes arrive as files rather than as a column because clinical narrative
    contains every delimiter a delimited file could use. The index carries what
    rules scope on, which encounter, which kind of document, and each note
    keeps its own hash, so a marker read out of narrative points at a specific
    document and a specific character range inside it.
    """
    index_path = directory / "notes.txt"
    if not index_path.is_file():
        return ()
    index = read_table(index_path)
    out: list[Note] = []
    for row in index.rows:
        path = directory / "notes" / row["filename"]
        if not path.is_file():
            msg = f"notes.txt names a file that was not delivered: {row['filename']}"
            raise ExtractError(msg)
        out.append(
            Note(
                note_id=row["note_id"],
                encounter_id=row["log_id"],
                note_type=row["note_type"].strip().lower(),
                service_date=parse_date(row["service_date"], f"notes {row['note_id']}"),
                author_role=row["author_role"],
                filename=row["filename"],
                sha256=sha256_file(path),
                text=path.read_text(encoding="utf-8"),
            )
        )
    return tuple(out)


def read_extract(directory: Path) -> ClinicalExtract:
    """Read the whole clinical extract from a directory."""
    return ClinicalExtract(
        or_log=read_table(directory / "or_log.txt"),
        or_log_proc=read_table(directory / "or_log_proc.txt"),
        or_staff=read_table(directory / "or_staff.txt"),
        encounter_dx=read_table(directory / "encounter_dx.txt"),
        patient_lds=read_table(directory / "patient_lds.txt"),
        notes=read_notes(directory),
    )


def parse_dttm(value: str, where: str) -> datetime:
    """Parse Epic's default timestamp rendering."""
    try:
        return datetime.strptime(value, "%m/%d/%Y %H:%M:%S")
    except ValueError as exc:
        msg = f"{where}: not a timestamp: {value!r}"
        raise ExtractError(msg) from exc


def parse_date(value: str, where: str) -> date:
    return parse_dttm(value, where).date()


def parse_int(value: str, where: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        msg = f"{where}: not an integer: {value!r}"
        raise ExtractError(msg) from exc


def parse_decimal(value: str, where: str) -> Decimal:
    try:
        return Decimal(value)
    except ArithmeticError as exc:
        msg = f"{where}: not a number: {value!r}"
        raise ExtractError(msg) from exc


def encounters(extract: ClinicalExtract) -> tuple[Encounter, ...]:
    """Build one :class:`Encounter` per operative log."""
    panels: dict[str, list[tuple[int, str, str]]] = {}
    for row in extract.or_log_proc.rows:
        panels.setdefault(row["log_id"], []).append(
            (parse_int(row["line"], "or_log_proc.line"), row["cpt"], row["mod1"])
        )

    out: list[Encounter] = []
    for row in extract.or_log.rows:
        log_id = row["log_id"]
        panel = sorted(panels.get(log_id, []))
        if not panel:
            msg = f"or_log {log_id}: no procedure lines"
            raise ExtractError(msg)
        service = row["service"].upper()
        out.append(
            Encounter(
                encounter_id=log_id,
                csn=row["csn"],
                patient_id=row["pat_id"],
                account_id=row["billing_account_id"],
                service_date=parse_date(row["surgery_date"], f"or_log {log_id}.surgery_date"),
                service_line=service,
                specialty=row["specialty"],
                cohort=Cohort.COMPARATOR if service in _COMPARATOR_SERVICES else Cohort.STUDY,
                facility_npi=row["facility_npi"],
                primary_cpt=panel[0][1],
                procedures=tuple((cpt, mod) for _, cpt, mod in panel),
                inpatient=row["patient_class"].strip().lower() == "inpatient",
            )
        )
    return tuple(out)
