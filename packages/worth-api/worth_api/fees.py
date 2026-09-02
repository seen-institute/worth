"""Prices: the fee schedule from Postgres rows, and the formula over it.

``worth-fees`` prices from a :class:`FeeSchedule`. It builds one from its
committed fixture, eight codes, two localities, enough to prove the arithmetic
offline. The console has to price any code in any locality, so this module
builds the same dataclass from the database rows the load script wrote, and
hands it to the same ``expected_allowed``. The formula, the trace and the
refusals are ``worth-fees``' own; nothing here re-derives a number.

The round trip is guarded: ``tests/test_db.py`` prices every fixture code both
ways and asserts identical amounts and traces.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from worth_fees import PaymentBasis, PlaceOfService, expected_allowed
from worth_fees.cli import parse_when
from worth_fees.models import SourceIntegrityError, VintageError
from worth_fees.provenance import SourceFile, Sources
from worth_fees.sources import (
    PINNED_VINTAGES,
    FeeSchedule,
    GpciRow,
    LocalityCounty,
    PaymentPolicy,
    RvuRow,
    Vintage,
    cache_dir,
    load,
    load_from_archive,
    vintage_for,
)

from worth_api import db

if TYPE_CHECKING:
    from worth_fees.models import FeeDerivation

    from worth_api.db import Connection
    from worth_api.settings import Settings

log = logging.getLogger(__name__)


class Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


# --------------------------------------------------------------------------
# On the wire
# --------------------------------------------------------------------------


class PriceRequest(Model):
    code: str
    locality: str
    """Medicare locality, e.g. ``CA18``."""
    date: str = "today"
    """A service date (``2026-03-14``), a quarter (``2026Q2``), a year, or ``today``."""
    setting: Literal["facility", "non-facility"] = "non-facility"
    modifiers: list[str] = Field(default_factory=list)
    payment_basis: Literal["non-qualifying-apm", "qualifying-apm"] = "non-qualifying-apm"


class SourceLink(Model):
    filename: str
    sha256: str
    role: str
    release_date: str
    note: str | None


class DerivationSource(Model):
    release: str
    release_date: str
    files: list[SourceLink]


class DerivationInputs(Model):
    """Every input as a decimal string. Strings, because a float is not money."""

    work_rvu: str
    pe_rvu: str
    mp_rvu: str
    work_gpci: str
    pe_gpci: str
    mp_gpci: str
    conversion_factor: str


class Derivation(Model):
    """A priced answer with its arithmetic and its receipts. Mirrors ``worth-fees price --json``."""

    amount: str
    currency: Literal["USD"] = "USD"
    code: str
    modifier: str
    modifiers: list[str]
    locality: str
    locality_name: str
    place_of_service: str
    rule_year: int
    quarter: int
    payment_basis: str
    inputs: DerivationInputs
    adjusted_rvu_total: str
    trace: list[str]
    source: DerivationSource


class VintageInfo(Model):
    key: str
    label: str
    rule_year: int
    quarter: int
    effective_from: str
    effective_to: str
    released_on: str
    origin: Literal["database", "fixture"]
    """Where the server is pricing this vintage from right now."""
    coverage: Literal["archive", "fixture"]
    """Whether that source holds the full CMS release or the committed sample."""
    codes: int
    """Distinct codes priceable, counting modifier variants once."""
    localities: int


class Locality(Model):
    locality: str
    name: str
    mac: str


def derivation(d: FeeDerivation) -> Derivation:
    return Derivation(
        amount=str(d.amount),
        code=d.code,
        modifier=d.modifier,
        modifiers=list(d.modifiers),
        locality=d.locality,
        locality_name=d.locality_name,
        place_of_service=d.place_of_service.value,
        rule_year=d.rule_year,
        quarter=d.quarter,
        payment_basis=d.payment_basis.value,
        inputs=DerivationInputs(
            work_rvu=str(d.work_rvu),
            pe_rvu=str(d.pe_rvu),
            mp_rvu=str(d.mp_rvu),
            work_gpci=str(d.work_gpci),
            pe_gpci=str(d.pe_gpci),
            mp_gpci=str(d.mp_gpci),
            conversion_factor=str(d.conversion_factor),
        ),
        adjusted_rvu_total=str(d.adjusted_rvu_total),
        trace=list(d.trace),
        source=DerivationSource(
            release=d.source.release,
            release_date=d.source.release_date.isoformat(),
            files=[
                SourceLink(
                    filename=f.filename,
                    sha256=f.sha256,
                    role=f.role,
                    release_date=f.release_date.isoformat(),
                    note=f.note,
                )
                for entry in d.source.files
                for f in entry.chain()
            ],
        ),
    )


# --------------------------------------------------------------------------
# A FeeSchedule from the rows the load script wrote
# --------------------------------------------------------------------------


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


# --------------------------------------------------------------------------
# The service: boot, then price
# --------------------------------------------------------------------------


def best_local(vintage: Vintage) -> FeeSchedule:
    """The full archive if it is in the cache, else the committed fixture. Never the network."""
    if (cache_dir() / vintage.archive_filename).exists():
        return load_from_archive(vintage.rule_year, vintage.quarter)
    return load(vintage.rule_year, vintage.quarter)


class FeeService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.schedules: dict[tuple[int, int], FeeSchedule] = {}
        self.origin: dict[tuple[int, int], Literal["database", "fixture"]] = {}
        self.migrated = False
        self.actions: dict[str, str] = {}

    def boot(self) -> None:
        """Make every pinned vintage priceable, from the database when there is one.

        With a database: apply the schema if it is missing, load any vintage
        that is absent (upgrading a fixture-loaded one when the archive is in
        the cache), then read each vintage back into memory. Without one: the
        committed fixture, and ``/api/health`` says so.
        """
        url = self._settings.database_url
        if url is None:
            for key in sorted(PINNED_VINTAGES):
                self.schedules[key] = load(*key)
                self.origin[key] = "fixture"
            return
        with db.connect(url) as conn:
            self.migrated = db.ensure_schema(conn)
            for key, vintage in sorted(PINNED_VINTAGES.items()):
                action = db.ensure_loaded(conn, best_local(vintage))
                self.actions[vintage.label] = action
                self.schedules[key] = schedule_from_db(conn, vintage)
                self.origin[key] = "database"
                log.info("%s: %s (%s)", vintage.label, action, db.coverage_of(self.schedules[key]))

    def schedule_for(self, rule_year: int, quarter: int) -> FeeSchedule:
        vintage_for(rule_year, quarter)  # a clear error for an unpinned vintage
        return self.schedules[(rule_year, quarter)]

    def resolve(self, when: str) -> tuple[int, int]:
        return parse_when(when)

    def price(self, request: PriceRequest) -> FeeDerivation:
        rule_year, quarter = self.resolve(request.date)
        return expected_allowed(
            request.code,
            request.modifiers,
            request.locality,
            PlaceOfService(request.setting),
            rule_year,
            quarter,
            payment_basis=request.payment_basis,
            schedule=self.schedule_for(rule_year, quarter),
        )

    def localities(self, when: str) -> list[Locality]:
        rule_year, quarter = self.resolve(when)
        schedule = self.schedule_for(rule_year, quarter)
        return [
            Locality(locality=g.key, name=g.name, mac=g.mac)
            for _, g in sorted(schedule.gpcis.items())
        ]

    def vintages(self) -> list[VintageInfo]:
        out = []
        for key, vintage in sorted(PINNED_VINTAGES.items()):
            schedule = self.schedules[key]
            start, end = vintage.effective
            out.append(
                VintageInfo(
                    key=vintage.key,
                    label=vintage.label,
                    rule_year=vintage.rule_year,
                    quarter=vintage.quarter,
                    effective_from=start.isoformat(),
                    effective_to=end.isoformat(),
                    released_on=vintage.release_date.isoformat(),
                    origin=self.origin[key],
                    coverage="fixture" if db.coverage_of(schedule) == "fixture" else "archive",
                    codes=len({code for code, _ in schedule.rvus}),
                    localities=len(schedule.gpcis),
                )
            )
        return out
