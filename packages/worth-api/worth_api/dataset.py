"""The dataset as delivered: a manifest, and each file on request.

The console used to carry every table, note and 835 inside its bundle. Now it
asks for a manifest (names, sizes, hashes, counts) and fetches one file's
contents when somebody opens its card. The shapes returned here are the ones
the console already renders, so the port changes where the bytes come from and
nothing about what they look like.

Everything is read from the dataset directory as received. Nothing is redacted:
the raw 835 still names its payer, because that is the partner's own remittance
shown back to them. Blinding happens in the computed output, not in the input.
"""

from __future__ import annotations

import io
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from worth_complexity import cases
from worth_complexity.models import Cohort
from worth_complexity.provenance import sha256_file


class Model(BaseModel):
    """camelCase on the wire, snake_case in Python, no extra keys either way."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


# --------------------------------------------------------------------------
# What the partner delivers, and what each file is for
# --------------------------------------------------------------------------

TABLE_BLURBS: dict[str, str] = {
    "or_log.txt": "One row per surgical case. The operative timestamps, ASA class, blood "
    "loss and patient class live here, and so does the billing account "
    "number that joins to the remittance.",
    "or_log_proc.txt": "One row per procedure code on the case. A case can carry several.",
    "or_staff.txt": "One row per person in the room, with their role and specialty. Team "
    "composition is read from this file.",
    "encounter_dx.txt": "One row per diagnosis on the visit. Comorbidity count is read "
    "from this file.",
    "patient_lds.txt": "One row per patient. Limited Data Set: a pseudonymous identifier, "
    "birth month, ZIP and county. No name, no medical record number.",
}

NOTES_NAME = "notes/"
NOTES_BLURB = (
    "Operative notes and discharge summaries as native free text, direct from "
    "Epic Clarity. The markers that have no discrete field live here: which "
    "structures were involved, how dense the adhesions were, what happened "
    "during the case. Claims and the exchange cannot see any of it."
)

EDI_NAME = "remittance/"
EDI_BLURB = (
    "ANSI X12 835 remittance advice, one file per payer per month. This is "
    "the only form the data exists in. The allowed amount is AMT*B6; the "
    "adjustments and their reason codes are the CAS segments."
)


# --------------------------------------------------------------------------
# Full file payloads: what a card shows when opened
# --------------------------------------------------------------------------


class TableFile(Model):
    kind: Literal["table"]
    name: str
    blurb: str
    bytes: int
    sha256: str
    columns: list[str]
    rows: list[list[str]]


class NoteFile(Model):
    name: str
    encounter: str
    note_type: str
    author: str
    sha256: str
    bytes: int
    text: str


class NoteBundle(Model):
    kind: Literal["notes"]
    name: str
    blurb: str
    count: int
    bytes: int
    files: list[NoteFile]


class EdiFile(Model):
    name: str
    bytes: int
    claims: int
    lines: int
    segments: list[str]


class EdiBundle(Model):
    kind: Literal["edi"]
    name: str
    blurb: str
    count: int
    bytes: int
    files: list[EdiFile]


type DeliveredFile = TableFile | NoteBundle | EdiBundle


# --------------------------------------------------------------------------
# The manifest: enough to draw every card closed
# --------------------------------------------------------------------------


class TableSummary(Model):
    kind: Literal["table"]
    name: str
    blurb: str
    bytes: int
    sha256: str
    rows: int
    columns: list[str]


class BundleSummary(Model):
    kind: Literal["notes", "edi"]
    name: str
    blurb: str
    count: int
    bytes: int


type FileSummary = TableSummary | BundleSummary


class Dataset(Model):
    """The manifest, plus the cohort counts every page needs before any run."""

    files: list[FileSummary]
    encounters: int
    study: int
    comparator: int
    codes: list[str]
    """Study codes, sorted. The unit the registry accumulates."""
    period_start: str | None
    period_end: str | None
    """Earliest and latest service date in the extract."""
    content_hash: str
    """One hash over every delivered file's hash. A run is cached against it."""


class UnknownFileError(LookupError):
    """The manifest has no file by that name."""


def _table_file(clinical: Path, name: str) -> TableFile:
    table = cases.read_table(clinical / name)
    return TableFile(
        kind="table",
        name=name,
        blurb=TABLE_BLURBS[name],
        bytes=(clinical / name).stat().st_size,
        sha256=table.sha256,
        columns=list(table.columns),
        rows=[[row[c] for c in table.columns] for row in table.rows],
    )


def _note_bundle(clinical: Path) -> NoteBundle:
    notes = cases.read_notes(clinical)
    files = [
        NoteFile(
            name=n.filename,
            encounter=n.encounter_id,
            note_type=n.note_type,
            author=n.author_role,
            sha256=n.sha256,
            bytes=len(n.text.encode()),
            text=n.text,
        )
        for n in notes
    ]
    return NoteBundle(
        kind="notes",
        name=NOTES_NAME,
        blurb=NOTES_BLURB,
        count=len(files),
        bytes=sum(f.bytes for f in files),
        files=files,
    )


def _edi_bundle(remittance: Path) -> EdiBundle:
    files = []
    for path in sorted(remittance.glob("*.edi")):
        text = path.read_text()
        files.append(
            EdiFile(
                name=path.name,
                bytes=path.stat().st_size,
                claims=text.count("CLP*"),
                lines=text.count("SVC*"),
                segments=[s for s in text.strip().split("\n") if s],
            )
        )
    return EdiBundle(
        kind="edi",
        name=EDI_NAME,
        blurb=EDI_BLURB,
        count=len(files),
        bytes=sum(f.bytes for f in files),
        files=files,
    )


def delivered_files(dataset_dir: Path) -> list[DeliveredFile]:
    """Every file in full, in the order the console shows them."""
    clinical = dataset_dir / "clinical"
    tables: list[DeliveredFile] = [_table_file(clinical, name) for name in TABLE_BLURBS]
    return [*tables, _note_bundle(clinical), _edi_bundle(dataset_dir / "remittance")]


def file_payload(dataset_dir: Path, name: str) -> DeliveredFile:
    """One file by its manifest name. ``notes`` and ``notes/`` both mean the bundle."""
    key = name.rstrip("/") + "/" if name.rstrip("/") in {"notes", "remittance"} else name
    if key in TABLE_BLURBS:
        return _table_file(dataset_dir / "clinical", key)
    if key == NOTES_NAME:
        return _note_bundle(dataset_dir / "clinical")
    if key == EDI_NAME:
        return _edi_bundle(dataset_dir / "remittance")
    raise UnknownFileError(name)


def _summary(f: DeliveredFile) -> FileSummary:
    if isinstance(f, TableFile):
        return TableSummary(
            kind="table",
            name=f.name,
            blurb=f.blurb,
            bytes=f.bytes,
            sha256=f.sha256,
            rows=len(f.rows),
            columns=f.columns,
        )
    return BundleSummary(kind=f.kind, name=f.name, blurb=f.blurb, count=f.count, bytes=f.bytes)


@lru_cache(maxsize=4)
def manifest(dataset_dir: Path) -> Dataset:
    """The manifest, computed once per dataset directory.

    Hashing five tables and five hundred notes is cheap, but not so cheap that
    every page load should do it. The cache is keyed on the directory, so a
    server pointed at a different dataset gets a different manifest; a dataset
    edited in place under a running server does not, and should not be.
    """
    files = delivered_files(dataset_dir)
    extract = cases.read_extract(dataset_dir / "clinical")
    encounters = cases.encounters(extract)
    study = [e for e in encounters if e.cohort is Cohort.STUDY]
    dates = sorted(e.service_date for e in encounters)
    hashes = [f.sha256 for f in files if isinstance(f, TableFile)]
    hashes += [n.sha256 for f in files if isinstance(f, NoteBundle) for n in f.files]
    hashes += [sha256_file(p) for p in sorted((dataset_dir / "remittance").glob("*.edi"))]
    return Dataset(
        files=[_summary(f) for f in files],
        encounters=len(encounters),
        study=len(study),
        comparator=len(encounters) - len(study),
        codes=sorted({e.primary_cpt for e in study}),
        period_start=dates[0].isoformat() if dates else None,
        period_end=dates[-1].isoformat() if dates else None,
        content_hash=_hash_of(hashes),
    )


def _hash_of(hashes: list[str]) -> str:
    import hashlib

    return hashlib.sha256("\n".join(hashes).encode()).hexdigest()


def raw_path(dataset_dir: Path, relative: str) -> Path | None:
    """Resolve a request path inside the dataset root, or refuse.

    The dataset directory is the only thing this server will ever read from
    disk on a caller's say-so. Anything that resolves outside it, symlink or
    ``..`` or absolute, is not found rather than forbidden: the response should
    not confirm that a path exists.
    """
    root = dataset_dir.resolve()
    candidate = (root / relative).resolve()
    if candidate == root or root not in candidate.parents:
        return None
    if not candidate.is_file():
        return None
    return candidate


ARCHIVE_ROOTS = ("clinical", "remittance")


def archive_members(dataset_dir: Path) -> list[Path]:
    """Every file the pipeline reads, plus the README, in a stable order."""
    members = [dataset_dir / "README.md"] if (dataset_dir / "README.md").is_file() else []
    for root in ARCHIVE_ROOTS:
        members.extend(sorted(p for p in (dataset_dir / root).rglob("*") if p.is_file()))
    return members


def archive(dataset_dir: Path) -> bytes:
    """The dataset as one zip: the files as delivered, and a checksum list.

    For whoever wants to read the inputs from first principles, or change them
    and hand them back. ``SHA256SUMS`` is generated so a modified copy can be
    told from the original file by file, and so the server's own hashes can be
    checked against the download.
    """
    buffer = io.BytesIO()
    sums: list[str] = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in archive_members(dataset_dir):
            name = path.relative_to(dataset_dir).as_posix()
            bundle.write(path, name)
            sums.append(f"{sha256_file(path)}  {name}")
        bundle.writestr("SHA256SUMS", "\n".join(sums) + "\n")
    return buffer.getvalue()
