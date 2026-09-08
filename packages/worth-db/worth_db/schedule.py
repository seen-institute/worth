"""Fee schedules, built from the rows the load script wrote.

``worth-fees`` prices from a :class:`FeeSchedule`. It builds one from its
committed fixture, eight codes, two localities, enough to prove the arithmetic
offline. A caller with a full Postgres copy needs to price any code in any
locality, so this module builds the same dataclass from database rows and
hands it to the package's own ``expected_allowed``. The formula, the trace and
the refusals are ``worth-fees``' own; nothing here re-derives a number.

The round trip is guarded: ``tests/test_schedule.py`` prices every fixture
code both ways and asserts identical amounts and traces.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from worth_fees import PaymentBasis
from worth_fees.models import SourceIntegrityError, VintageError
from worth_fees.provenance import SourceFile, Sources
from worth_fees.sources import (
    PINNED_VINTAGES,
    FeeSchedule,
    GpciRow,
    LocalityCounty,
    PaymentPolicy,
    RvuRow,
    cache_dir,
    load,
    load_from_archive,
)

from worth_db import db

if TYPE_CHECKING:
    from worth_fees.sources import Vintage

    from worth_db.db import Connection

log = logging.getLogger(__name__)


def _role_name(slug: str) -> str:
    """Undo ``worth_fees.sql._role``: ``pprrvu_fixture`` was ``pprrvu (stripped fixture)``."""
    return slug.replace("_fixture", " (stripped fixture)")


def _text(value: str | None) -> str:
    return value or ""


def schedule_from_db(conn: Connection, vintage: Vintage) -> FeeSchedule:
    """Read one release back into the dataclass ``expected_allowed`` prices from."""
    release = conn.execute(
        "SELECT released_on, archive_sha256, retrieved_at FROM fee_schedule_release "
        "WHERE release_id = %s",
        (vintage.label,),
    ).fetchone()
    if release is None:
        raise VintageError(f"{vintage.label} is not loaded")
    released_on, archive_sha256, retrieved_at = release
    if archive_sha256 != vintage.archive_sha256:
        raise SourceIntegrityError(
            f"{vintage.label} in the database was loaded from archive {archive_sha256}, "
            f"not the pinned {vintage.archive_sha256}"
        )

    # Provenance chain, nearest link first, resolved through derived_from.
    raw = {
        role: (filename, sha256, note, derived_from)
        for role, filename, sha256, note, derived_from in conn.execute(
            "SELECT role, filename, sha256, note, derived_from FROM source_file "
            "WHERE release_id = %s",
            (vintage.label,),
        ).fetchall()
    }
    links: dict[str, SourceFile] = {}

    def link(role: str) -> SourceFile:
        if role not in links:
            filename, sha256, note, derived_from = raw[role]
            links[role] = SourceFile(
                filename=filename,
                sha256=sha256,
                release_date=released_on,
                role=_role_name(role),
                derived_from=link(derived_from) if derived_from else None,
                note=note,
            )
        return links[role]

    factors = {
        PaymentBasis(basis): (factor, source_role)
        for basis, factor, source_role in conn.execute(
            "SELECT payment_basis, conversion_factor, source_role "
            "FROM fee_schedule_conversion_factor WHERE release_id = %s",
            (vintage.label,),
        ).fetchall()
    }
    base_factor, rvu_role = factors[PaymentBasis.NON_QUALIFYING_APM]
    qpp_factor, qpp_role = factors[PaymentBasis.QUALIFYING_APM]

    rvus: dict[tuple[str, str], RvuRow] = {}
    for row in conn.execute(
        """
        SELECT hcpcs, modifier, status_code, global_days,
               work_rvu, pe_rvu_nonfacility, pe_rvu_nonfacility_na,
               pe_rvu_facility, pe_rvu_facility_na, mp_rvu, qpp_eligible,
               pctc_indicator, multiple_procedure, bilateral_surgery, assistant_surgery,
               co_surgery, team_surgery, endoscopic_base,
               pre_op_share, intra_op_share, post_op_share
        FROM rvu WHERE release_id = %s
        """,
        (vintage.label,),
    ).fetchall():
        (
            code,
            modifier,
            status,
            global_days,
            work,
            pe_nf,
            pe_nf_na,
            pe_f,
            pe_f_na,
            mp,
            qpp_eligible,
            pctc,
            mult,
            bilat,
            asst,
            co,
            team,
            endo,
            pre,
            intra,
            post,
        ) = row
        rvus[(code, modifier)] = RvuRow(
            code=code,
            modifier=modifier,
            status_code=status,
            global_days=_text(global_days),
            work_rvu=work,
            pe_rvu_nonfacility=pe_nf,
            pe_rvu_nonfacility_na=pe_nf_na,
            pe_rvu_facility=pe_f,
            pe_rvu_facility_na=pe_f_na,
            mp_rvu=mp,
            conversion_factor=base_factor,
            policy=PaymentPolicy(
                pctc_indicator=_text(pctc),
                multiple_procedure=_text(mult),
                bilateral_surgery=_text(bilat),
                assistant_surgery=_text(asst),
                co_surgery=_text(co),
                team_surgery=_text(team),
                endoscopic_base=_text(endo),
                pre_op=pre,
                intra_op=intra,
                post_op=post,
            ),
            qpp_eligible=qpp_eligible,
        )

    gpcis: dict[str, GpciRow] = {}
    gpci_role = ""
    for (
        locality,
        state,
        number,
        name,
        mac,
        work_no_floor,
        work_floor,
        pe,
        mp,
        source_role,
    ) in conn.execute(
        """
        SELECT locality, state, locality_number, locality_name, mac,
               work_gpci_no_floor, work_gpci_floor, pe_gpci, mp_gpci, source_role
        FROM gpci WHERE release_id = %s
        """,
        (vintage.label,),
    ).fetchall():
        gpci_role = source_role
        gpcis[locality] = GpciRow(
            mac=mac,
            state=state,
            locality=number,
            name=name,
            work_gpci_no_floor=work_no_floor,
            work_gpci_with_floor=work_floor,
            pe_gpci=pe,
            mp_gpci=mp,
        )

    counties: list[LocalityCounty] = []
    locco_role: str | None = None
    for ordinal, mac, state_name, number, area, county_text, source_role in conn.execute(
        """
        SELECT ordinal, mac, state_name, locality_number, fee_schedule_area, counties, source_role
        FROM locality_county WHERE release_id = %s ORDER BY ordinal
        """,
        (vintage.label,),
    ).fetchall():
        locco_role = source_role
        counties.append(
            LocalityCounty(
                ordinal=ordinal,
                mac=mac,
                state_name=state_name,
                locality_number=number,
                fee_schedule_area=_text(area),
                counties=_text(county_text),
            )
        )

    return FeeSchedule(
        vintage=vintage,
        rvus=rvus,
        gpcis=gpcis,
        conversion_factors={
            PaymentBasis.NON_QUALIFYING_APM: base_factor,
            PaymentBasis.QUALIFYING_APM: qpp_factor,
        },
        sources=Sources(
            release=vintage.label,
            release_date=released_on,
            rvu=link(rvu_role),
            gpci=link(gpci_role),
            rvu_qpp=link(qpp_role) if qpp_role != rvu_role else None,
        ),
        retrieved_at=retrieved_at,
        localities=tuple(counties),
        locco_source=link(locco_role) if locco_role else None,
    )


def best_local(vintage: Vintage) -> FeeSchedule:
    """The full archive if it is in the cache, else the committed fixture. Never the network."""
    if (cache_dir() / vintage.archive_filename).exists():
        return load_from_archive(vintage.rule_year, vintage.quarter)
    return load(vintage.rule_year, vintage.quarter)


def load_all(url: str) -> dict[tuple[int, int], FeeSchedule]:
    """Make every pinned vintage priceable from the database, and read it all back.

    Applies the schema if it is missing, loads any pinned vintage the database
    does not already hold (upgrading a fixture-loaded one when the archive is
    in the cache), then reads each vintage back into a :class:`FeeSchedule`.
    What ``FeeService.boot`` did against a database, without the HTTP service
    around it.
    """
    schedules: dict[tuple[int, int], FeeSchedule] = {}
    with db.connect(url) as conn:
        if db.ensure_schema(conn):
            log.info("applied the fee-schedule schema")
        for key, vintage in sorted(PINNED_VINTAGES.items()):
            action = db.ensure_loaded(conn, best_local(vintage))
            schedule = schedule_from_db(conn, vintage)
            schedules[key] = schedule
            log.info("%s: %s (%s)", vintage.label, action, db.coverage_of(schedule))
    return schedules
