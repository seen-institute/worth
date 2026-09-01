"""Getting CMS fee-schedule data in, and knowing exactly what came in.

Two paths, one parser:

* **Online.** :func:`fetch_archive` downloads a pinned CMS release archive into
  a gitignored cache directory and hashes it on arrival. Nothing is parsed
  until the hash is known.
* **Offline.** :func:`load` reads the small stripped fixtures committed next to
  this module, verifying them against a manifest recorded when they were built.
  This is what ``just demo`` and CI use, so neither needs a network.

The fixtures are row subsets of the real CMS files in the real CMS layout, so
both paths run through the same parser and the fixture cannot silently drift
from the format it claims to be a sample of.

**CPT descriptors are never retained.** The CMS RVU file carries a DESCRIPTION
column that is AMA-copyrighted. :func:`parse_pprrvu` does not read it, and
:func:`build_fixture` blanks it before writing. This repository is public and
must contain numeric codes only.
"""

from __future__ import annotations

import csv
import io
import json
import os
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from worth_fees.models import CodeSystem, SourceIntegrityError, VintageError
from worth_fees.provenance import SourceFile, Sources, sha256_bytes, sha256_file

# ---------------------------------------------------------------------------
# Pinned vintages
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Vintage:
    """One pinned CMS Physician Fee Schedule release."""

    rule_year: int
    quarter: int
    label: str
    url: str
    archive_sha256: str
    release_date: date
    pprrvu_member: str
    gpci_member: str

    @property
    def key(self) -> str:
        return f"{self.rule_year}q{self.quarter}"

    @property
    def archive_filename(self) -> str:
        return self.url.rsplit("/", 1)[-1]

    @property
    def effective(self) -> tuple[date, date]:
        """Service dates these rates govern, as a half-open ``[from, to)`` range.

        Modelled as the calendar quarter: a quarterly release governs service
        dates from the start of its quarter until the next release takes over.
        CMS corrections can apply retroactively, so treat this as the default
        rather than a settled rule, and verify before relying on it for claims
        spanning a quarter boundary.
        """
        start_month = 3 * (self.quarter - 1) + 1
        start = date(self.rule_year, start_month, 1)
        end = (
            date(self.rule_year + 1, 1, 1)
            if self.quarter == 4
            else date(self.rule_year, start_month + 3, 1)
        )
        return start, end


def _cy2026(
    quarter: int, label: str, url: str, sha256: str, released: date, member: str
) -> Vintage:
    """CY2026 quarterly release. All four share one GPCI file for the year."""
    return Vintage(
        rule_year=2026,
        quarter=quarter,
        label=label,
        url=url,
        archive_sha256=sha256,
        release_date=released,
        pprrvu_member=member,
        gpci_member="GPCI2026.csv",
    )


# Every CY2026 release carries an "-updated-" suffix: CMS revised each one after
# first publishing it. The hash pins the revision we actually read, so a silent
# re-cut upstream is a loud failure rather than a changed number.
PINNED_VINTAGES: Final[dict[tuple[int, int], Vintage]] = {
    (2026, 2): _cy2026(
        2,
        "RVU26B",
        "https://www.cms.gov/files/zip/rvu26b-updated-05-01-2026.zip",
        "7d9a398769fc4da406aa99284f3ab1a56eb609696fcf2f6f166387574edd5728",
        date(2026, 5, 1),
        "PPRRVU2026_Apr_nonQPP.csv",
    ),
    (2026, 3): _cy2026(
        3,
        "RVU26C",
        "https://www.cms.gov/files/zip/rvu26c-updated-06-30-2026.zip",
        "d45a158e02694c1539e7f88192c611883e377181eda86dc213359707bcacbacb",
        date(2026, 6, 30),
        "PPRRVU2026_Jul_nonQPP.csv",
    ),
    (2026, 4): _cy2026(
        4,
        "RVU26D",
        "https://www.cms.gov/files/zip/rvu26d-updated-08-26-2026.zip",
        "521ff0f4ecbf13b5d99dc1dc04d9f82e5361e55c6b4b6b3033043598bf8406e6",
        date(2026, 8, 26),
        "PPRRVU2026_Oct_nonQPP.csv",
    ),
    (2026, 1): Vintage(
        rule_year=2026,
        quarter=1,
        label="RVU26A",
        url="https://www.cms.gov/files/zip/rvu26a-updated-12-29-2025.zip",
        archive_sha256="91b9bdd5459bc4c19d4f8203410b29a672db37def2e70f01ef627b8b18fc7482",
        release_date=date(2025, 12, 29),
        # CY2026 has *two* conversion factors: a qualifying-APM one (in the QPP
        # file) and a non-qualifying one (here). worth-fees models the
        # non-qualifying CF only; see PAYMENT_BASIS_NOTE.
        pprrvu_member="PPRRVU2026_Jan_nonQPP.csv",
        gpci_member="GPCI2026.csv",
    ),
}

PAYMENT_BASIS: Final = "non-qualifying-apm"
"""Which of the two CY2026 conversion factors this package models."""

PAYMENT_BASIS_NOTE: Final = (
    "non-qualifying APM conversion factor (CMS PPRRVU nonQPP file); "
    "the qualifying-APM CF is not modelled"
)

APPLY_WORK_GPCI_FLOOR: Final = True
"""Whether to use the work GPCI column that has the 1.0 floor applied.

CMS publishes the work GPCI twice -- with and without the 1.0 floor -- because
whether the floor is in force is a statutory question, not a CMS one. Which
column is correct therefore depends on the law in effect for the payment year,
so the choice is made explicit here and recorded in every derivation trace
rather than buried in a column index.
"""

# ---------------------------------------------------------------------------
# Parsed rows
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RvuRow:
    """One (code, modifier) row of the CMS RVU file. No descriptor, by design."""

    code: str
    modifier: str
    status_code: str
    global_days: str
    """Global surgical period: 000, 010, 090, XXX, YYY, ZZZ or MMM."""
    work_rvu: Decimal
    pe_rvu_nonfacility: Decimal
    pe_rvu_nonfacility_na: bool
    pe_rvu_facility: Decimal
    pe_rvu_facility_na: bool
    mp_rvu: Decimal
    conversion_factor: Decimal

    @property
    def code_system(self) -> CodeSystem:
        """Which code system owns this code, and therefore who claims copyright."""
        return CodeSystem.classify(self.code)


@dataclass(frozen=True, slots=True)
class GpciRow:
    """One locality row of the CMS GPCI file."""

    mac: str
    state: str
    locality: str
    name: str
    work_gpci_no_floor: Decimal
    work_gpci_with_floor: Decimal
    pe_gpci: Decimal
    mp_gpci: Decimal

    @property
    def key(self) -> str:
        return f"{self.state}{self.locality}"

    @property
    def work_gpci(self) -> Decimal:
        return self.work_gpci_with_floor if APPLY_WORK_GPCI_FLOOR else self.work_gpci_no_floor


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    """Everything one vintage contributes to a derivation."""

    vintage: Vintage
    rvus: dict[tuple[str, str], RvuRow]
    gpcis: dict[str, GpciRow]
    conversion_factor: Decimal
    sources: Sources
    retrieved_at: datetime
    """When the CMS archive was fetched, as distinct from when CMS published it."""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# The CMS RVU layout is positional and stable within a release family. Indices
# are named here and validated against the header row on every parse, so a
# layout change is a loud failure instead of a wrong number.
_HCPCS: Final = 0
_MOD: Final = 1
_DESCRIPTION: Final = 2  # AMA-copyrighted. Never read; blanked in fixtures.
_STATUS: Final = 3
_WORK_RVU: Final = 5
_NONFAC_PE_RVU: Final = 6
_NONFAC_NA: Final = 7
_FAC_PE_RVU: Final = 8
_FAC_NA: Final = 9
_MP_RVU: Final = 10
# CMS's own sum of the three components. We do not use these to price -- we
# use them to check that we read the right columns. See _cross_check below.
_NONFAC_TOTAL: Final = 11
_FAC_TOTAL: Final = 12
_GLOB_DAYS: Final = 14
_CONV_FACTOR: Final = 25
_PPRRVU_COLUMNS: Final = 32

_ENCODING: Final = "latin-1"


def _decimal(raw: str) -> Decimal:
    """Parse a CMS numeric cell. Blank means zero; anything else must be valid."""
    text = raw.strip()
    return Decimal(text) if text else Decimal("0")


def _rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


def _cross_check(row: list[str], code: str, modifier: str) -> None:
    """Verify our column picks against CMS's own published totals.

    The RVU file carries CMS's own sum of the three components in the
    NON-FACILITY TOTAL and FACILITY TOTAL columns. We never price from them --
    but if our work, practice-expense and malpractice picks do not add up to
    CMS's totals, we are reading the wrong columns, and CMS just told us so.

    This is the strongest check available without leaving the file: it catches
    a shifted layout on every row of every release, offline, for free. It holds
    for all 77,312 rows across the four pinned CY2026 releases.
    """
    work, mp = _decimal(row[_WORK_RVU]), _decimal(row[_MP_RVU])
    for pe_at, total_at, setting in (
        (_NONFAC_PE_RVU, _NONFAC_TOTAL, "non-facility"),
        (_FAC_PE_RVU, _FAC_TOTAL, "facility"),
    ):
        ours = work + _decimal(row[pe_at]) + mp
        theirs = _decimal(row[total_at])
        if ours != theirs:
            label = f"{code}-{modifier}" if modifier else code
            raise SourceIntegrityError(
                f"{label}: our {setting} components sum to {ours}, but CMS's own "
                f"{setting.upper()} TOTAL column says {theirs}. The column layout is not "
                "what this parser expects; refusing to price from it."
            )


def parse_pprrvu(text: str) -> tuple[dict[tuple[str, str], RvuRow], Decimal]:
    """Parse a CMS PPRRVU CSV into rows keyed by ``(code, modifier)``.

    Returns the rows and the file's single conversion factor. Raises if the
    file carries more than one distinct conversion factor, which would mean the
    caller has a file whose payment basis this code does not understand.
    """
    rows = _rows(text)
    header_at = next(
        (i for i, r in enumerate(rows) if r and r[_HCPCS].strip().upper() == "HCPCS"),
        None,
    )
    if header_at is None:
        raise SourceIntegrityError("PPRRVU file has no HCPCS header row")

    header = rows[header_at]
    if len(header) != _PPRRVU_COLUMNS:
        raise SourceIntegrityError(
            f"PPRRVU header has {len(header)} columns, expected {_PPRRVU_COLUMNS}"
        )
    if (
        header[_MOD].strip().upper() != "MOD"
        or header[_DESCRIPTION].strip().upper() != "DESCRIPTION"
    ):
        raise SourceIntegrityError(f"unexpected PPRRVU column layout: {header[:4]}")

    out: dict[tuple[str, str], RvuRow] = {}
    factors: set[Decimal] = set()
    for row in rows[header_at + 1 :]:
        if not row or not row[_HCPCS].strip():
            continue
        if len(row) != _PPRRVU_COLUMNS:
            raise SourceIntegrityError(
                f"PPRRVU row {row[_HCPCS]!r} has {len(row)} columns, expected {_PPRRVU_COLUMNS}"
            )
        code = row[_HCPCS].strip().upper()
        modifier = row[_MOD].strip().upper()
        _cross_check(row, code, modifier)
        factor = _decimal(row[_CONV_FACTOR])
        if factor > 0:
            factors.add(factor)
        out[(code, modifier)] = RvuRow(
            code=code,
            modifier=modifier,
            status_code=row[_STATUS].strip().upper(),
            global_days=row[_GLOB_DAYS].strip().upper(),
            work_rvu=_decimal(row[_WORK_RVU]),
            pe_rvu_nonfacility=_decimal(row[_NONFAC_PE_RVU]),
            pe_rvu_nonfacility_na=row[_NONFAC_NA].strip().upper() == "NA",
            pe_rvu_facility=_decimal(row[_FAC_PE_RVU]),
            pe_rvu_facility_na=row[_FAC_NA].strip().upper() == "NA",
            mp_rvu=_decimal(row[_MP_RVU]),
            conversion_factor=factor,
        )

    if len(factors) != 1:
        raise SourceIntegrityError(
            f"expected exactly one conversion factor in PPRRVU file, found {sorted(factors)}"
        )
    return out, factors.pop()


_GPCI_FIELDS: Final = {
    "mac": ("medicare administrative contractor",),
    "state": ("state",),
    "locality": ("locality number",),
    "name": ("locality name",),
    "with_floor": ("pw gpci", "with 1.0"),
    "pe": ("pe gpci",),
    "mp": ("mp gpci",),
}

# CY2026 Q1 published the work GPCI twice, with and without the statutory 1.0
# floor. From Q2 2026 CMS publishes only the floored column -- so the file
# itself now answers the question of which one applies. The unfloored column is
# optional: when it is absent, the floored value stands for both.
_GPCI_OPTIONAL: Final = {"no_floor": ("pw gpci", "without")}


def parse_gpci(text: str) -> dict[str, GpciRow]:
    """Parse a CMS GPCI CSV into rows keyed by ``<state><locality>``, e.g. ``CA18``.

    Columns are located by matching the header text rather than by position:
    the GPCI header names its year ("2026 PE GPCI"), so position-independence
    is what lets one parser read successive vintages.
    """
    rows = _rows(text)
    header_at = next(
        (i for i, r in enumerate(rows) if any("locality number" in c.strip().lower() for c in r)),
        None,
    )
    if header_at is None:
        raise SourceIntegrityError("GPCI file has no 'Locality Number' header row")

    header = [c.strip().lower() for c in rows[header_at]]

    def locate(needles: tuple[str, ...]) -> int | None:
        return next((i for i, cell in enumerate(header) if all(n in cell for n in needles)), None)

    index: dict[str, int] = {}
    for field, needles in _GPCI_FIELDS.items():
        match = locate(needles)
        if match is None:
            raise SourceIntegrityError(f"GPCI header has no column matching {needles}")
        index[field] = match
    for field, needles in _GPCI_OPTIONAL.items():
        found = locate(needles)
        if found is not None:
            index[field] = found

    out: dict[str, GpciRow] = {}
    for row in rows[header_at + 1 :]:
        if not row or not row[index["state"]].strip() or not row[index["locality"]].strip():
            continue
        entry = GpciRow(
            mac=row[index["mac"]].strip(),
            state=row[index["state"]].strip().upper(),
            locality=row[index["locality"]].strip(),
            name=row[index["name"]].strip(),
            work_gpci_no_floor=_decimal(
                row[index["no_floor"]] if "no_floor" in index else row[index["with_floor"]]
            ),
            work_gpci_with_floor=_decimal(row[index["with_floor"]]),
            pe_gpci=_decimal(row[index["pe"]]),
            mp_gpci=_decimal(row[index["mp"]]),
        )
        out[entry.key] = entry

    if not out:
        raise SourceIntegrityError("GPCI file contained no locality rows")
    return out


# ---------------------------------------------------------------------------
# Cache (online path)
# ---------------------------------------------------------------------------

_USER_AGENT: Final = "worth-fees/0.1 (+https://github.com/seen-institute/worth)"


def cache_dir() -> Path:
    """Where downloaded CMS archives live. Gitignored; never read by CI."""
    override = os.environ.get("WORTH_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "worth" / "cms"


def fetch_archive(vintage: Vintage, *, force: bool = False) -> Path:
    """Download a pinned CMS archive into the cache and verify its sha256.

    The hash is checked before any parsing happens, so a truncated download or
    a silently re-cut upstream file fails here rather than becoming a subtly
    wrong dollar amount downstream.
    """
    target = cache_dir() / vintage.archive_filename
    if force or not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(vintage.url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(request, timeout=300) as response:
            payload = response.read()
        target.write_bytes(payload)

    actual = sha256_file(target)
    if actual != vintage.archive_sha256:
        raise SourceIntegrityError(
            f"{vintage.archive_filename}: sha256 {actual} does not match the pinned "
            f"{vintage.archive_sha256}. Refusing to parse."
        )
    return target


def read_member(archive: Path, member: str) -> bytes:
    """Read one file out of a CMS release archive."""
    with zipfile.ZipFile(archive) as bundle:
        return bundle.read(member)


# ---------------------------------------------------------------------------
# Fixtures (offline path)
# ---------------------------------------------------------------------------

FIXTURE_DIR: Final = Path(__file__).parent / "fixtures"
MANIFEST_NAME: Final = "manifest.json"


def _fixture_names(vintage: Vintage) -> tuple[str, str]:
    return f"pprrvu-{vintage.key}.csv", f"gpci-{vintage.key}.csv"


def vintage_for(rule_year: int, quarter: int) -> Vintage:
    """Look up the pinned vintage for a rule year and quarter."""
    try:
        return PINNED_VINTAGES[(rule_year, quarter)]
    except KeyError:
        available = ", ".join(f"CY{y} Q{q}" for y, q in sorted(PINNED_VINTAGES))
        raise VintageError(
            f"no pinned CMS vintage for CY{rule_year} Q{quarter}; available: {available}"
        ) from None


def vintage_for_date(service_date: date) -> Vintage:
    """Find the pinned vintage governing a service date.

    Claims carry a date of service, and the rate that applies is the one in
    force on that date -- not the newest one. This is the lookup that keeps a
    2024 claim from being priced with 2026 rates.
    """
    for vintage in PINNED_VINTAGES.values():
        start, end = vintage.effective
        if start <= service_date < end:
            return vintage
    covered = ", ".join(
        f"{v.effective[0].isoformat()}..{v.effective[1].isoformat()}"
        for v in sorted(PINNED_VINTAGES.values(), key=lambda v: v.effective)
    )
    raise VintageError(
        f"no pinned CMS vintage covers service date {service_date.isoformat()}; "
        f"pinned ranges: {covered}"
    )


def _verify(path: Path, expected: str) -> str:
    actual = sha256_file(path)
    if actual != expected:
        raise SourceIntegrityError(
            f"{path.name}: sha256 {actual} does not match the manifest value {expected}. "
            "The committed fixture has been modified without rebuilding its manifest."
        )
    return actual


def load(rule_year: int, quarter: int) -> FeeSchedule:
    """Load a pinned vintage from the committed fixtures. Offline, always.

    Both fixtures are hashed and checked against the manifest before they are
    parsed, so an edited fixture is caught here rather than quietly changing
    every published number.
    """
    vintage = vintage_for(rule_year, quarter)
    catalogue = json.loads((FIXTURE_DIR / MANIFEST_NAME).read_text(encoding="utf-8"))
    manifest = catalogue.get("vintages", {}).get(vintage.key)

    if manifest is None:
        built = ", ".join(sorted(catalogue.get("vintages", {}))) or "none"
        raise SourceIntegrityError(
            f"no committed fixture for CY{rule_year} Q{quarter}; built vintages: {built}. "
            "Run `just refresh-fixture` to build it."
        )
    if manifest["archive"]["sha256"] != vintage.archive_sha256:
        raise SourceIntegrityError(
            "fixture manifest was built from a different CMS archive than the pinned vintage"
        )

    rvu_name, gpci_name = _fixture_names(vintage)
    rvu_path, gpci_path = FIXTURE_DIR / rvu_name, FIXTURE_DIR / gpci_name
    rvu_sha = _verify(rvu_path, manifest["fixtures"]["pprrvu"]["sha256"])
    gpci_sha = _verify(gpci_path, manifest["fixtures"]["gpci"]["sha256"])

    rvus, conversion_factor = parse_pprrvu(rvu_path.read_text(encoding=_ENCODING))
    gpcis = parse_gpci(gpci_path.read_text(encoding=_ENCODING))

    archive = SourceFile(
        filename=vintage.archive_filename,
        sha256=vintage.archive_sha256,
        release_date=vintage.release_date,
        role="archive",
        note=vintage.url,
    )

    def chain(role: str, fixture_name: str, fixture_sha: str) -> SourceFile:
        member = manifest["members"][role]
        note = "row subset of the CMS file"
        if role == "pprrvu":
            note += "; CPT descriptor column blanked (AMA-copyrighted)"
        return SourceFile(
            filename=fixture_name,
            sha256=fixture_sha,
            release_date=vintage.release_date,
            role=f"{role} (stripped fixture)",
            note=note,
            derived_from=SourceFile(
                filename=member["filename"],
                sha256=member["sha256"],
                release_date=vintage.release_date,
                role=role,
                derived_from=archive,
            ),
        )

    return FeeSchedule(
        vintage=vintage,
        rvus=rvus,
        gpcis=gpcis,
        conversion_factor=conversion_factor,
        retrieved_at=datetime.fromisoformat(manifest["retrieved_at"]),
        sources=Sources(
            release=vintage.label,
            release_date=vintage.release_date,
            rvu=chain("pprrvu", rvu_name, rvu_sha),
            gpci=chain("gpci", gpci_name, gpci_sha),
        ),
    )


def load_from_archive(rule_year: int, quarter: int) -> FeeSchedule:
    """Load a full vintage from the cached CMS archive. Requires the download.

    Same parser and same provenance model as :func:`load`, but reads every row
    rather than the committed sample, and chains two links instead of three
    because no fixture is involved. This is what you load a database from;
    :func:`load` is what runs offline in the demo and in CI.
    """
    vintage = vintage_for(rule_year, quarter)
    archive = fetch_archive(vintage)
    archive_source = SourceFile(
        filename=vintage.archive_filename,
        sha256=vintage.archive_sha256,
        release_date=vintage.release_date,
        role="archive",
        note=vintage.url,
    )

    def member(role: str, name: str) -> tuple[bytes, SourceFile]:
        payload = read_member(archive, name)
        return payload, SourceFile(
            filename=name,
            sha256=sha256_bytes(payload),
            release_date=vintage.release_date,
            role=role,
            derived_from=archive_source,
        )

    rvu_bytes, rvu_source = member("pprrvu", vintage.pprrvu_member)
    gpci_bytes, gpci_source = member("gpci", vintage.gpci_member)

    rvus, conversion_factor = parse_pprrvu(rvu_bytes.decode(_ENCODING))
    gpcis = parse_gpci(gpci_bytes.decode(_ENCODING))

    return FeeSchedule(
        vintage=vintage,
        rvus=rvus,
        gpcis=gpcis,
        conversion_factor=conversion_factor,
        retrieved_at=datetime.fromtimestamp(
            (cache_dir() / vintage.archive_filename).stat().st_mtime, tz=UTC
        ),
        sources=Sources(
            release=vintage.label,
            release_date=vintage.release_date,
            rvu=rvu_source,
            gpci=gpci_source,
        ),
    )


def build_fixture(
    rule_year: int,
    quarter: int,
    codes: list[str],
    localities: list[str],
) -> Path:
    """Rebuild the committed fixtures from a cached CMS archive.

    Keeps the CMS header block and layout verbatim so the fixture stays a
    genuine sample of the real file, keeps every modifier variant of the
    requested codes, and blanks the AMA-copyrighted descriptor column.
    """
    vintage = vintage_for(rule_year, quarter)
    archive = fetch_archive(vintage)
    wanted_codes = {c.strip().upper() for c in codes}
    wanted_localities = {loc.strip().upper() for loc in localities}

    rvu_bytes = read_member(archive, vintage.pprrvu_member)
    gpci_bytes = read_member(archive, vintage.gpci_member)

    rvu_rows = _rows(rvu_bytes.decode(_ENCODING))
    rvu_header_at = next(i for i, r in enumerate(rvu_rows) if r and r[_HCPCS].strip() == "HCPCS")
    kept_rvu = list(rvu_rows[: rvu_header_at + 1])
    for row in rvu_rows[rvu_header_at + 1 :]:
        if row and row[_HCPCS].strip().upper() in wanted_codes:
            stripped = list(row)
            stripped[_DESCRIPTION] = ""  # AMA-copyrighted; must not be committed.
            kept_rvu.append(stripped)

    gpci_rows = _rows(gpci_bytes.decode(_ENCODING))
    gpci_header_at = next(
        i for i, r in enumerate(gpci_rows) if any("locality number" in c.lower() for c in r)
    )
    gpci_header = [c.strip().lower() for c in gpci_rows[gpci_header_at]]
    state_at = next(i for i, c in enumerate(gpci_header) if c == "state")
    loc_at = next(i for i, c in enumerate(gpci_header) if "locality number" in c)
    kept_gpci = list(gpci_rows[: gpci_header_at + 1])
    for row in gpci_rows[gpci_header_at + 1 :]:
        if len(row) > max(state_at, loc_at):
            key = f"{row[state_at].strip().upper()}{row[loc_at].strip()}"
            if key in wanted_localities:
                kept_gpci.append(list(row))

    rvu_name, gpci_name = _fixture_names(vintage)
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, rows in ((rvu_name, kept_rvu), (gpci_name, kept_gpci)):
        buffer = io.StringIO(newline="")
        csv.writer(buffer, lineterminator="\n").writerows(rows)
        (FIXTURE_DIR / name).write_text(buffer.getvalue(), encoding=_ENCODING, newline="")

    entry = {
        "vintage": {
            "rule_year": vintage.rule_year,
            "quarter": vintage.quarter,
            "label": vintage.label,
            "release_date": vintage.release_date.isoformat(),
            "url": vintage.url,
            "payment_basis": PAYMENT_BASIS_NOTE,
        },
        "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "archive": {
            "filename": vintage.archive_filename,
            "sha256": vintage.archive_sha256,
        },
        "members": {
            "pprrvu": {
                "filename": vintage.pprrvu_member,
                "sha256": sha256_bytes(rvu_bytes),
            },
            "gpci": {"filename": vintage.gpci_member, "sha256": sha256_bytes(gpci_bytes)},
        },
        "fixtures": {
            "pprrvu": {
                "filename": rvu_name,
                "sha256": sha256_file(FIXTURE_DIR / rvu_name),
                "codes": sorted(wanted_codes),
                "rows": len(kept_rvu) - (rvu_header_at + 1),
            },
            "gpci": {
                "filename": gpci_name,
                "sha256": sha256_file(FIXTURE_DIR / gpci_name),
                "localities": sorted(wanted_localities),
                "rows": len(kept_gpci) - (gpci_header_at + 1),
            },
        },
    }
    manifest_path = FIXTURE_DIR / MANIFEST_NAME
    catalogue: dict[str, Any] = {}
    if manifest_path.exists():
        catalogue = json.loads(manifest_path.read_text(encoding="utf-8"))
    catalogue["_comment"] = (
        "Generated by `worth-fees build-fixture`. Do not hand-edit: worth_fees.sources.load() "
        "verifies each fixture against the sha256 recorded here and refuses to parse on mismatch. "
        "CPT descriptors are stripped on ingest; this repository is public and CPT descriptors "
        "are AMA-copyrighted."
    )
    vintages: dict[str, Any] = catalogue.get("vintages", {})
    vintages[vintage.key] = entry
    catalogue["vintages"] = dict(sorted(vintages.items()))
    manifest_path.write_text(json.dumps(catalogue, indent=2) + "\n", encoding="utf-8")
    return manifest_path
