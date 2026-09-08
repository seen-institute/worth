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
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from worth_fees.models import CodeSystem, PaymentBasis, SourceIntegrityError, VintageError
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
    """The non-qualifying-APM RVU file. The package's base file."""
    pprrvu_qpp_member: str
    """The qualifying-APM RVU file, same archive. Read only for its conversion
    factor and the set of codes it covers; its RVUs are cross-checked against
    the base file rather than trusted."""
    gpci_member: str
    locco_member: str | None = None
    """Locality-to-county crosswalk, when the release ships a readable one.

    ``None`` for RVU26C, which publishes the crosswalk only as ``.xlsx``. The
    package parses CSV with the standard library and takes no dependency to
    read a spreadsheet, so that release simply has no crosswalk rather than a
    crosswalk obtained some other way.
    """

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
    quarter: int,
    label: str,
    url: str,
    sha256: str,
    released: date,
    month: str,
    *,
    locco: str | None = "26LOCCO.csv",
) -> Vintage:
    """CY2026 quarterly release. All four share one GPCI file for the year."""
    return Vintage(
        rule_year=2026,
        quarter=quarter,
        label=label,
        url=url,
        archive_sha256=sha256,
        release_date=released,
        pprrvu_member=f"PPRRVU2026_{month}_nonQPP.csv",
        pprrvu_qpp_member=f"PPRRVU2026_{month}_QPP.csv",
        gpci_member="GPCI2026.csv",
        locco_member=locco,
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
        "Apr",
    ),
    (2026, 3): _cy2026(
        3,
        "RVU26C",
        "https://www.cms.gov/files/zip/rvu26c-updated-06-30-2026.zip",
        "d45a158e02694c1539e7f88192c611883e377181eda86dc213359707bcacbacb",
        date(2026, 6, 30),
        "Jul",
        # RVU26C ships 26LOCCO as .xlsx only, no CSV, no TXT.
        locco=None,
    ),
    (2026, 4): _cy2026(
        4,
        "RVU26D",
        "https://www.cms.gov/files/zip/rvu26d-updated-08-26-2026.zip",
        "521ff0f4ecbf13b5d99dc1dc04d9f82e5361e55c6b4b6b3033043598bf8406e6",
        date(2026, 8, 26),
        "Oct",
    ),
    (2026, 1): Vintage(
        rule_year=2026,
        quarter=1,
        label="RVU26A",
        url="https://www.cms.gov/files/zip/rvu26a-updated-12-29-2025.zip",
        archive_sha256="91b9bdd5459bc4c19d4f8203410b29a672db37def2e70f01ef627b8b18fc7482",
        release_date=date(2025, 12, 29),
        # CY2026 has *two* conversion factors: a qualifying-APM one (the QPP
        # file) and a non-qualifying one (the base file). Both are read; see
        # PaymentBasis and _cross_check_qpp.
        pprrvu_member="PPRRVU2026_Jan_nonQPP.csv",
        pprrvu_qpp_member="PPRRVU2026_Jan_QPP.csv",
        gpci_member="GPCI2026.csv",
        locco_member="26LOCCO.csv",
    ),
}

DEFAULT_PAYMENT_BASIS: Final = PaymentBasis.NON_QUALIFYING_APM
"""The basis used when a caller does not choose one.

Most clinicians are not qualifying APM participants, so the non-qualifying
conversion factor is the answer to an unqualified question. The choice is
recorded on every derivation rather than assumed.
"""

PAYMENT_BASIS_NOTES: Final[dict[PaymentBasis, str]] = {
    PaymentBasis.NON_QUALIFYING_APM: (
        "non-qualifying APM conversion factor (CMS PPRRVU nonQPP file)"
    ),
    PaymentBasis.QUALIFYING_APM: ("qualifying APM conversion factor (CMS PPRRVU QPP file)"),
}

# CMS's own marker for which basis a PPRRVU file carries, in the PRIC IND
# column. Constant down the whole file, so it is a label on the file rather
# than data about a code, which makes it exactly the right thing to assert
# the file's identity against before reading a conversion factor out of it.
_PRICING_INDICATOR: Final[dict[PaymentBasis, str]] = {
    PaymentBasis.NON_QUALIFYING_APM: "9",
    PaymentBasis.QUALIFYING_APM: "1",
}

APPLY_WORK_GPCI_FLOOR: Final = True
"""Whether to use the work GPCI column that has the 1.0 floor applied.

CMS publishes the work GPCI twice, with and without the 1.0 floor, because
whether the floor is in force is a statutory question, not a CMS one. Which
column is correct therefore depends on the law in effect for the payment year,
so the choice is made explicit here and recorded in every derivation trace
rather than buried in a column index.
"""

# ---------------------------------------------------------------------------
# Parsed rows
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PaymentPolicy:
    """CMS payment-policy indicators for one RVU row. Stored, never priced.

    These are the columns that say *how* a payment would be adjusted when a
    service is billed bilaterally, as multiple procedures, or with an assistant
    at surgery. worth-fees does not model those adjustments, ``fees.py``
    rejects the modifiers that trigger them rather than guessing, so nothing
    here enters the arithmetic.

    They are parsed and stored anyway for two reasons. Ingest is where the
    bytes are, so reading a column costs nothing now and a re-download later.
    And the work splits are the closest thing the fee schedule publishes to a
    decomposition of physician effort, which is the question WORTH exists to
    ask. Only codes with a global period carry them, and the post-operative
    share never exceeds 0.23 in CY2026, but at equal work RVUs and equal
    global periods, a code that is 23% post-operative is still a different
    claim about what was done than one that is 5%.

    Every field is the CMS cell verbatim, so an unexpected value is preserved
    rather than normalised into something that looks meaningful.
    """

    pctc_indicator: str
    """Professional/technical split behaviour. 0 = the concept does not apply."""
    multiple_procedure: str
    bilateral_surgery: str
    assistant_surgery: str
    co_surgery: str
    team_surgery: str
    endoscopic_base: str
    """The endoscopic base code this one is a family member of, or blank."""

    pre_op: Decimal
    intra_op: Decimal
    post_op: Decimal
    """Shares of the work RVU by phase of care. Sum to 1.00, or all zero."""

    @property
    def work_split_total(self) -> Decimal:
        """The three phase shares summed. 1.00 for a code with a global period."""
        return self.pre_op + self.intra_op + self.post_op


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
    """The conversion factor of the file this row was parsed from. Prefer
    :attr:`FeeSchedule.conversion_factors`, which is keyed by payment basis."""
    policy: PaymentPolicy
    """Payment-policy indicators. Carried, not used in the formula."""
    qpp_eligible: bool = True
    """Whether this code also appears in the qualifying-APM file.

    False only for codes that carry no payable amount under any basis: across
    all four CY2026 releases, every code CMS omits from the QPP file has a
    non-payable status. So this never decides whether a price exists, it
    records a fact about the source files, and :func:`_cross_check_qpp`
    fails loudly if that ever stops being true.
    """

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
class LocalityCounty:
    """One row of the CMS locality-to-county crosswalk (``26LOCCO``).

    Reference data. It says which geography a locality covers, in CMS's own
    words, and nothing here is ever priced from.

    The county text is kept verbatim, typos and all, the CY2026 file spells
    Orange county ``ORAGNGE``, because normalising it would mean shipping
    guesses about what CMS meant. A caller that wants a county lookup can build
    one and own that decision; this package will not make it silently. Note in
    particular that this cannot resolve an address to a locality: for that CMS
    publishes a separate ZIP-code file, which is not pinned here.
    """

    ordinal: int
    """Position in the file, from 1. The key, because a locality can occupy
    several rows and CY2026 repeats one outright: Missouri's rest-of-state is
    serviced by two carriers, and CMS represents that as two rows identical
    but for trailing whitespace. Keying on the row keeps the file as
    published instead of quietly editing it."""
    mac: str
    state_name: str
    """CMS's full state name, e.g. ``CALIFORNIA``. Not the two-letter code:
    the crosswalk and the GPCI file identify states differently."""
    locality_number: str
    fee_schedule_area: str
    counties: str
    """Free text. ``ALL COUNTIES`` for a statewide locality."""


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    """Everything one vintage contributes to a derivation."""

    vintage: Vintage
    rvus: dict[tuple[str, str], RvuRow]
    gpcis: dict[str, GpciRow]
    conversion_factors: dict[PaymentBasis, Decimal]
    """One conversion factor per payment basis, read from CMS's two files."""
    sources: Sources
    retrieved_at: datetime
    """When the CMS archive was fetched, as distinct from when CMS published it."""
    localities: tuple[LocalityCounty, ...] = ()
    """The locality-to-county crosswalk, if the release carried one."""
    locco_source: SourceFile | None = None
    """Provenance for the crosswalk. Kept off :class:`Sources` because no
    derivation reads it, and a derivation's sources must name only what its
    number actually depended on."""

    @property
    def conversion_factor(self) -> Decimal:
        """The default (non-qualifying-APM) conversion factor."""
        return self.conversion_factors[DEFAULT_PAYMENT_BASIS]


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
# CMS's own sum of the three components. We do not use these to price, we
# use them to check that we read the right columns. See _cross_check below.
_NONFAC_TOTAL: Final = 11
_FAC_TOTAL: Final = 12
_PCTC_IND: Final = 13
_GLOB_DAYS: Final = 14
# Pre-, intra- and post-operative shares of the work RVU. They sum to 1.00 for
# a code with a global period and are 0.00 otherwise.
_PRE_OP: Final = 15
_INTRA_OP: Final = 16
_POST_OP: Final = 17
# The payment-adjusting indicators. Stored, never priced: see PaymentPolicy.
_MULT_PROC: Final = 18
_BILAT_SURG: Final = 19
_ASST_SURG: Final = 20
_CO_SURG: Final = 21
_TEAM_SURG: Final = 22
# Constant within a file: 9 in the non-QPP file, 1 in the QPP file. CMS's own
# label for which payment basis the file carries. See _payment_basis_marker.
_PRIC_IND: Final = 23
_ENDO_BASE: Final = 24
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
    NON-FACILITY TOTAL and FACILITY TOTAL columns. We never price from them, but if our work,
    practice-expense and malpractice picks do not add up to
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


def parse_pprrvu(
    text: str, *, expect_basis: PaymentBasis | None = None
) -> tuple[dict[tuple[str, str], RvuRow], Decimal]:
    """Parse a CMS PPRRVU CSV into rows keyed by ``(code, modifier)``.

    Returns the rows and the file's single conversion factor. Raises if the
    file carries more than one distinct conversion factor, which would mean the
    caller has a file whose payment basis this code does not understand.

    ``expect_basis`` additionally asserts CMS's own pricing indicator, so
    reading the QPP file where the non-QPP one was meant is caught here rather
    than becoming a number that is wrong by $0.17 per RVU.
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
    indicators: set[str] = set()
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
        if row[_PRIC_IND].strip():
            indicators.add(row[_PRIC_IND].strip())
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
            policy=PaymentPolicy(
                pctc_indicator=row[_PCTC_IND].strip(),
                multiple_procedure=row[_MULT_PROC].strip(),
                bilateral_surgery=row[_BILAT_SURG].strip(),
                assistant_surgery=row[_ASST_SURG].strip(),
                co_surgery=row[_CO_SURG].strip(),
                team_surgery=row[_TEAM_SURG].strip(),
                endoscopic_base=row[_ENDO_BASE].strip().upper(),
                pre_op=_decimal(row[_PRE_OP]),
                intra_op=_decimal(row[_INTRA_OP]),
                post_op=_decimal(row[_POST_OP]),
            ),
        )

    if len(factors) != 1:
        raise SourceIntegrityError(
            f"expected exactly one conversion factor in PPRRVU file, found {sorted(factors)}"
        )
    if expect_basis is not None:
        wanted = _PRICING_INDICATOR[expect_basis]
        if indicators != {wanted}:
            raise SourceIntegrityError(
                f"expected the {expect_basis.value} PPRRVU file, whose pricing indicator is "
                f"{wanted} on every row, but found {sorted(indicators)}. Refusing to read a "
                "conversion factor out of a file that is not the one it was asked for."
            )
    return out, factors.pop()


def _cross_check_qpp(
    base: dict[tuple[str, str], RvuRow],
    qpp: dict[tuple[str, str], RvuRow],
) -> None:
    """Verify the two payment bases really do differ only in conversion factor.

    worth-fees stores one set of RVU rows and two conversion factors, which is
    only sound because CMS's two files agree on everything else. That is an
    observed fact about CY2026, not a guarantee, so it is asserted on every
    parse rather than trusted: if CMS ever diverges the two files, this fails
    instead of silently pricing the qualifying basis off the wrong RVUs.

    Two things are checked. Shared codes must carry identical RVUs and policy
    indicators. And the qualifying file must not omit any code that is payable,
    because a missing payable code would mean the basis genuinely changes what
    is covered, not just what a unit is worth.
    """
    for key in sorted(base.keys() & qpp.keys()):
        ours, theirs = base[key], qpp[key]
        if (ours.work_rvu, ours.pe_rvu_nonfacility, ours.pe_rvu_facility, ours.mp_rvu) != (
            theirs.work_rvu,
            theirs.pe_rvu_nonfacility,
            theirs.pe_rvu_facility,
            theirs.mp_rvu,
        ) or (ours.status_code, ours.global_days, ours.policy) != (
            theirs.status_code,
            theirs.global_days,
            theirs.policy,
        ):
            label = f"{key[0]}-{key[1]}" if key[1] else key[0]
            raise SourceIntegrityError(
                f"{label} differs between the non-QPP and QPP files beyond the conversion "
                "factor. worth-fees stores one set of RVUs for both payment bases, which "
                "this contradicts. The two bases now need separate rows."
            )

    missing_payable = sorted(
        key for key in base.keys() - qpp.keys() if base[key].status_code in _PAYABLE_STATUS
    )
    if missing_payable:
        shown = ", ".join(f"{c}-{m}" if m else c for c, m in missing_payable[:5])
        raise SourceIntegrityError(
            f"{len(missing_payable)} payable code(s) are in the non-QPP file but absent from "
            f"the QPP file (e.g. {shown}). The qualifying-APM basis would have no rate for "
            "them, so the two bases no longer share a code set."
        )


# Status codes that carry a payable amount. Duplicated from fees.py rather than
# imported, because sources.py must not depend on the pricing layer, and a
# test asserts the two stay in step.
_PAYABLE_STATUS: Final = frozenset({"A", "R", "T"})


def _mark_qpp_eligibility(
    base: dict[tuple[str, str], RvuRow],
    qpp: dict[tuple[str, str], RvuRow],
) -> dict[tuple[str, str], RvuRow]:
    """Record, per row, whether the qualifying-APM file also carries the code."""
    return {key: replace(row, qpp_eligible=key in qpp) for key, row in base.items()}


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
# floor. From Q2 2026 CMS publishes only the floored column, so the file
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


_LOCCO_FIELDS: Final = {
    # CMS misspells "Administrative" in this file's header. Matching a
    # substring that avoids the typo keeps the parser working either way.
    "mac": ("contractor",),
    "locality": ("locality number",),
    "state": ("state",),
    "area": ("fee schedule area",),
    "counties": ("counties",),
}


def parse_locco(text: str) -> tuple[LocalityCounty, ...]:
    """Parse the CMS locality-to-county crosswalk (``26LOCCO``).

    The file is laid out for a human reader rather than a parser: blank spacer
    rows between entries, a footnote at the end, and a state column that is
    filled in only on the first locality of each state. The state is carried
    forward, which is the one inference this function makes and the only one
    the layout forces.

    Everything else is preserved verbatim. See :class:`LocalityCounty` for why
    the county text is not cleaned up.
    """
    rows = _rows(text)
    header_at = next(
        (i for i, r in enumerate(rows) if any("locality number" in c.strip().lower() for c in r)),
        None,
    )
    if header_at is None:
        raise SourceIntegrityError("locality crosswalk has no 'Locality Number' header row")

    header = [c.strip().lower() for c in rows[header_at]]
    index: dict[str, int] = {}
    for field, needles in _LOCCO_FIELDS.items():
        match = next((i for i, cell in enumerate(header) if all(n in cell for n in needles)), None)
        if match is None:
            raise SourceIntegrityError(f"locality crosswalk header has no column for {needles}")
        index[field] = match

    out: list[LocalityCounty] = []
    state = ""
    for row in rows[header_at + 1 :]:
        if len(row) <= max(index.values()):
            continue
        mac = row[index["mac"]].strip()
        locality = row[index["locality"]].strip()
        # Spacer rows are blank; the trailing footnote has prose in the MAC
        # column and no locality number. Both fail this test.
        if not locality or not mac.isdigit():
            continue
        state = row[index["state"]].strip().upper() or state
        out.append(
            LocalityCounty(
                ordinal=len(out) + 1,
                mac=mac,
                state_name=state,
                locality_number=locality,
                fee_schedule_area=row[index["area"]].strip(),
                counties=row[index["counties"]].strip(),
            )
        )

    if not out:
        raise SourceIntegrityError("locality crosswalk contained no locality rows")
    return tuple(out)


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


def _member_names(vintage: Vintage) -> dict[str, str]:
    """The CMS member file each role is cut from, for roles this release has."""
    names = {
        "pprrvu": vintage.pprrvu_member,
        "pprrvu_qpp": vintage.pprrvu_qpp_member,
        "gpci": vintage.gpci_member,
        "locco": vintage.locco_member,
    }
    return {role: name for role, name in names.items() if name is not None}


def _fixture_names(vintage: Vintage) -> dict[str, str]:
    """Committed fixture filename for each role, e.g. ``pprrvu-2026q1.csv``."""
    return {role: f"{role.replace('_', '-')}-{vintage.key}.csv" for role in _member_names(vintage)}


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
    force on that date, not the newest one. This is the lookup that keeps a
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

    names = _fixture_names(vintage)
    text: dict[str, str] = {}
    shas: dict[str, str] = {}
    for role, filename in names.items():
        path = FIXTURE_DIR / filename
        if role not in manifest["fixtures"]:
            raise SourceIntegrityError(
                f"the committed fixture for CY{rule_year} Q{quarter} has no {role!r} file. "
                "It predates a change to what is sampled; run `just refresh-fixture`."
            )
        shas[role] = _verify(path, manifest["fixtures"][role]["sha256"])
        text[role] = path.read_text(encoding=_ENCODING)

    rvus, conversion_factor = parse_pprrvu(
        text["pprrvu"], expect_basis=PaymentBasis.NON_QUALIFYING_APM
    )
    qpp_rvus, qpp_conversion_factor = parse_pprrvu(
        text["pprrvu_qpp"], expect_basis=PaymentBasis.QUALIFYING_APM
    )
    _cross_check_qpp(rvus, qpp_rvus)
    rvus = _mark_qpp_eligibility(rvus, qpp_rvus)
    gpcis = parse_gpci(text["gpci"])
    localities = parse_locco(text["locco"]) if "locco" in text else ()

    archive = SourceFile(
        filename=vintage.archive_filename,
        sha256=vintage.archive_sha256,
        release_date=vintage.release_date,
        role="archive",
        note=vintage.url,
    )

    def chain(role: str) -> SourceFile:
        member = manifest["members"][role]
        note = "row subset of the CMS file"
        if role.startswith("pprrvu"):
            note += "; CPT descriptor column blanked (AMA-copyrighted)"
        return SourceFile(
            filename=names[role],
            sha256=shas[role],
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
        localities=localities,
        conversion_factors={
            PaymentBasis.NON_QUALIFYING_APM: conversion_factor,
            PaymentBasis.QUALIFYING_APM: qpp_conversion_factor,
        },
        retrieved_at=datetime.fromisoformat(manifest["retrieved_at"]),
        sources=Sources(
            release=vintage.label,
            release_date=vintage.release_date,
            rvu=chain("pprrvu"),
            rvu_qpp=chain("pprrvu_qpp"),
            gpci=chain("gpci"),
        ),
        locco_source=chain("locco") if "locco" in names else None,
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

    members = _member_names(vintage)
    rvu_bytes, rvu_source = member("pprrvu", members["pprrvu"])
    qpp_bytes, qpp_source = member("pprrvu_qpp", members["pprrvu_qpp"])
    gpci_bytes, gpci_source = member("gpci", members["gpci"])
    locco: tuple[bytes, SourceFile] | None = (
        member("locco", members["locco"]) if "locco" in members else None
    )

    rvus, conversion_factor = parse_pprrvu(
        rvu_bytes.decode(_ENCODING), expect_basis=PaymentBasis.NON_QUALIFYING_APM
    )
    qpp_rvus, qpp_conversion_factor = parse_pprrvu(
        qpp_bytes.decode(_ENCODING), expect_basis=PaymentBasis.QUALIFYING_APM
    )
    _cross_check_qpp(rvus, qpp_rvus)
    rvus = _mark_qpp_eligibility(rvus, qpp_rvus)
    gpcis = parse_gpci(gpci_bytes.decode(_ENCODING))
    localities = parse_locco(locco[0].decode(_ENCODING)) if locco else ()

    return FeeSchedule(
        vintage=vintage,
        rvus=rvus,
        gpcis=gpcis,
        localities=localities,
        conversion_factors={
            PaymentBasis.NON_QUALIFYING_APM: conversion_factor,
            PaymentBasis.QUALIFYING_APM: qpp_conversion_factor,
        },
        retrieved_at=datetime.fromtimestamp(
            (cache_dir() / vintage.archive_filename).stat().st_mtime, tz=UTC
        ),
        sources=Sources(
            release=vintage.label,
            release_date=vintage.release_date,
            rvu=rvu_source,
            rvu_qpp=qpp_source,
            gpci=gpci_source,
        ),
        locco_source=locco[1] if locco else None,
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
    members = _member_names(vintage)
    wanted_codes = {c.strip().upper() for c in codes}
    wanted_localities = {loc.strip().upper() for loc in localities}

    raw = {role: read_member(archive, name) for role, name in members.items()}
    rvu_bytes = raw["pprrvu"]
    gpci_bytes = raw["gpci"]

    def sample_rvu(payload: bytes) -> list[list[str]]:
        """Keep the CMS header block and the wanted codes, blanking descriptors."""
        rows = _rows(payload.decode(_ENCODING))
        header_at = next(i for i, r in enumerate(rows) if r and r[_HCPCS].strip() == "HCPCS")
        kept = list(rows[: header_at + 1])
        for row in rows[header_at + 1 :]:
            if row and row[_HCPCS].strip().upper() in wanted_codes:
                stripped = list(row)
                stripped[_DESCRIPTION] = ""  # AMA-copyrighted; must not be committed.
                kept.append(stripped)
        return kept

    kept_rvu = sample_rvu(rvu_bytes)
    rvu_header_at = next(i for i, r in enumerate(kept_rvu) if r and r[_HCPCS].strip() == "HCPCS")
    kept_qpp = sample_rvu(raw["pprrvu_qpp"])
    qpp_header_at = next(i for i, r in enumerate(kept_qpp) if r and r[_HCPCS].strip() == "HCPCS")

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

    # The crosswalk is committed whole rather than sampled. It is 7 KB, it
    # carries no CPT material, and subsetting it would actively break it: the
    # state column is filled in only on the first locality of each state, so
    # dropping rows would leave the survivors inheriting the wrong state.
    names = _fixture_names(vintage)
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    kept = {
        "pprrvu": (kept_rvu, rvu_header_at),
        "pprrvu_qpp": (kept_qpp, qpp_header_at),
        "gpci": (kept_gpci, gpci_header_at),
    }
    if "locco" in raw:
        kept_locco = _rows(raw["locco"].decode(_ENCODING))
        kept["locco"] = (
            kept_locco,
            next(
                i
                for i, r in enumerate(kept_locco)
                if any("locality number" in c.lower() for c in r)
            ),
        )
    for role, (rows, _) in kept.items():
        buffer = io.StringIO(newline="")
        csv.writer(buffer, lineterminator="\n").writerows(rows)
        (FIXTURE_DIR / names[role]).write_text(buffer.getvalue(), encoding=_ENCODING, newline="")

    fixtures: dict[str, Any] = {}
    for role, (rows, header_at) in kept.items():
        detail: dict[str, Any] = {
            "filename": names[role],
            "sha256": sha256_file(FIXTURE_DIR / names[role]),
            "rows": len(rows) - (header_at + 1),
        }
        if role.startswith("pprrvu"):
            detail["codes"] = sorted(wanted_codes)
        elif role == "gpci":
            detail["localities"] = sorted(wanted_localities)
        else:
            detail["note"] = "committed in full; see build_fixture"
        fixtures[role] = detail

    entry = {
        "vintage": {
            "rule_year": vintage.rule_year,
            "quarter": vintage.quarter,
            "label": vintage.label,
            "release_date": vintage.release_date.isoformat(),
            "url": vintage.url,
            "payment_bases": {basis.value: note for basis, note in PAYMENT_BASIS_NOTES.items()},
        },
        "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "archive": {
            "filename": vintage.archive_filename,
            "sha256": vintage.archive_sha256,
        },
        "members": {
            role: {"filename": members[role], "sha256": sha256_bytes(raw[role])} for role in members
        },
        "fixtures": fixtures,
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
