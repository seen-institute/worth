"""Emit a Postgres load script for one fee schedule vintage.

This module writes SQL text. It does not connect to a database, which is
deliberate: a driver would be the package's first runtime dependency, and the
one property worth-fees trades on is that a hospital security review has
nothing to audit but the standard library. A ``.sql`` file also travels into an
air-gapped environment in a way a Python client does not, and can itself be
hashed and reviewed before anyone runs it.

Apply ``migrations/0001_fee_schedule.sql`` first, then pipe the output of
:func:`export` into ``psql``.

Reloading a vintage replaces it wholesale, in one transaction. That is a reload
of the same immutable release, not an edit: if the archive's bytes changed, it
is a different release and belongs under a different ``release_id``.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import TYPE_CHECKING

from worth_fees.models import CodeSystem, PaymentBasis
from worth_fees.sources import APPLY_WORK_GPCI_FLOOR

if TYPE_CHECKING:
    from worth_fees.provenance import SourceFile
    from worth_fees.sources import FeeSchedule

__all__ = ["export"]

_RVU_COLUMNS = (
    "release_id",
    "hcpcs",
    "modifier",
    "code_system",
    "status_code",
    "global_days",
    "work_rvu",
    "pe_rvu_nonfacility",
    "pe_rvu_nonfacility_na",
    "pe_rvu_facility",
    "pe_rvu_facility_na",
    "mp_rvu",
    "qpp_eligible",
    "pctc_indicator",
    "multiple_procedure",
    "bilateral_surgery",
    "assistant_surgery",
    "co_surgery",
    "team_surgery",
    "endoscopic_base",
    "pre_op_share",
    "intra_op_share",
    "post_op_share",
    "source_role",
)
_CONVERSION_FACTOR_COLUMNS = (
    "release_id",
    "payment_basis",
    "conversion_factor",
    "source_role",
)
_LOCALITY_COUNTY_COLUMNS = (
    "release_id",
    "ordinal",
    "mac",
    "state_name",
    "locality_number",
    "fee_schedule_area",
    "counties",
    "source_role",
)
_GPCI_COLUMNS = (
    "release_id",
    "locality",
    "state",
    "locality_number",
    "locality_name",
    "mac",
    "work_gpci_no_floor",
    "work_gpci_floor",
    "pe_gpci",
    "mp_gpci",
    "source_role",
)


def _role(source: SourceFile) -> str:
    """Slugify a provenance role into a stable SQL identifier."""
    return source.role.replace(" (stripped fixture)", "_fixture").replace(" ", "_")


def _literal(value: object) -> str:
    """Render a SQL literal. Numerics unquoted, everything else quoted."""
    if value is None:
        return "NULL"
    if isinstance(value, Decimal | int):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _copy_cell(value: object) -> str:
    """Escape one cell for COPY ... FROM STDIN in text format."""
    if value is None:
        return r"\N"
    if isinstance(value, bool):
        return "t" if value else "f"
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("\t", "\\t")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def _copy_block(
    table: str, columns: tuple[str, ...], rows: Sequence[tuple[object, ...]]
) -> list[str]:
    out = [f"COPY {table} ({', '.join(columns)}) FROM STDIN;"]
    out += ["\t".join(_copy_cell(c) for c in row) for row in rows]
    out.append(r"\.")
    out.append("")
    return out


def export(schedule: FeeSchedule) -> str:
    """Render a complete, transactional Postgres load script for ``schedule``."""
    vintage = schedule.vintage
    release = vintage.label
    gpci_basis = "floor" if APPLY_WORK_GPCI_FLOOR else "no-floor"
    effective_from, effective_to = vintage.effective

    # Deduplicate the provenance chain by role: both files descend from the
    # same archive, which is one row, not two.
    chain: dict[str, SourceFile] = {}
    provenance = list(schedule.sources.files)
    if schedule.locco_source is not None:
        provenance.append(schedule.locco_source)
    for entry in provenance:
        for link in entry.chain():
            chain.setdefault(_role(link), link)

    lines = [
        f"-- worth-fees load script for CMS {release} (CY{vintage.rule_year} Q{vintage.quarter})",
        f"-- archive:  {vintage.archive_filename}",
        f"-- sha256:   {vintage.archive_sha256}",
        f"-- url:      {vintage.url}",
        f"-- released: {vintage.release_date.isoformat()}",
        f"-- rows:     {len(schedule.rvus)} rvu, {len(schedule.gpcis)} gpci, "
        f"{len(schedule.localities)} locality-county",
        "--",
        "-- Apply migrations/0001_fee_schedule.sql first.",
        "",
        "BEGIN;",
        "",
        f"DELETE FROM fee_schedule_release WHERE release_id = {_literal(release)};",
        "",
        "INSERT INTO fee_schedule_release (",
        "    release_id, rule_year, quarter, work_gpci_basis,",
        "    released_on, effective, source_url, archive_sha256, retrieved_at",
        ") VALUES (",
        f"    {_literal(release)}, {vintage.rule_year}, {vintage.quarter},",
        f"    {_literal(gpci_basis)}, {_literal(vintage.release_date.isoformat())},",
        f"    daterange({_literal(effective_from.isoformat())}, "
        f"{_literal(effective_to.isoformat())}, '[)'),",
        f"    {_literal(vintage.url)}, {_literal(vintage.archive_sha256)},",
        f"    {_literal(schedule.retrieved_at.isoformat())}",
        ");",
        "",
        "-- Provenance chain. Deferred FK: children may precede their parent.",
        "SET CONSTRAINTS ALL DEFERRED;",
        "INSERT INTO source_file (release_id, role, filename, sha256, note, derived_from) VALUES",
    ]

    entries = [
        "    ("
        + ", ".join(
            (
                _literal(release),
                _literal(role),
                _literal(link.filename),
                _literal(link.sha256),
                _literal(link.note),
                _literal(_role(link.derived_from) if link.derived_from else None),
            )
        )
        + ")"
        for role, link in chain.items()
    ]
    lines.append(",\n".join(entries) + ";")
    lines.append("")

    rvu_role = _role(schedule.sources.rvu)
    gpci_role = _role(schedule.sources.gpci)
    qpp_role = _role(schedule.sources.rvu_qpp) if schedule.sources.rvu_qpp else rvu_role

    rvu_rows = [
        (
            release,
            row.code,
            row.modifier,
            CodeSystem.classify(row.code).value,
            row.status_code,
            row.global_days or None,
            row.work_rvu,
            row.pe_rvu_nonfacility,
            row.pe_rvu_nonfacility_na,
            row.pe_rvu_facility,
            row.pe_rvu_facility_na,
            row.mp_rvu,
            row.qpp_eligible,
            row.policy.pctc_indicator or None,
            row.policy.multiple_procedure or None,
            row.policy.bilateral_surgery or None,
            row.policy.assistant_surgery or None,
            row.policy.co_surgery or None,
            row.policy.team_surgery or None,
            row.policy.endoscopic_base or None,
            row.policy.pre_op,
            row.policy.intra_op,
            row.policy.post_op,
            rvu_role,
        )
        for _, row in sorted(schedule.rvus.items())
    ]
    conversion_factor_rows = [
        (
            release,
            basis.value,
            schedule.conversion_factors[basis],
            qpp_role if basis is PaymentBasis.QUALIFYING_APM else rvu_role,
        )
        for basis in sorted(schedule.conversion_factors, key=lambda b: b.value)
    ]
    locality_county_rows = [
        (
            release,
            row.ordinal,
            row.mac,
            row.state_name,
            row.locality_number,
            row.fee_schedule_area or None,
            row.counties or None,
            _role(schedule.locco_source) if schedule.locco_source else rvu_role,
        )
        for row in schedule.localities
    ]
    gpci_rows = [
        (
            release,
            row.key,
            row.state,
            row.locality,
            row.name,
            row.mac,
            row.work_gpci_no_floor,
            row.work_gpci_with_floor,
            row.pe_gpci,
            row.mp_gpci,
            gpci_role,
        )
        for _, row in sorted(schedule.gpcis.items())
    ]

    lines += _copy_block(
        "fee_schedule_conversion_factor", _CONVERSION_FACTOR_COLUMNS, conversion_factor_rows
    )
    lines += _copy_block("rvu", _RVU_COLUMNS, rvu_rows)
    lines += _copy_block("gpci", _GPCI_COLUMNS, gpci_rows)
    if locality_county_rows:
        lines += _copy_block("locality_county", _LOCALITY_COUNTY_COLUMNS, locality_county_rows)
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"
