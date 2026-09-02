"""Postgres: the loader, and the round trip from rows back to a price.

These need a database. Set ``DATABASE_URL`` (the compose one is
``postgresql://worth:worth@127.0.0.1:55432/worth``) and they run; leave it
unset and they skip. CI provides one.

The drift test is the point of the file. Every code and modifier in the
committed fixture, in every fixture locality, in both settings and on both
bases, is priced twice: once from the fixture the package ships, once from a
schedule rebuilt out of database rows. Amount and trace must be identical.
"""

from __future__ import annotations

import os
from itertools import product

import pytest
from fastapi.testclient import TestClient
from worth_api import db
from worth_api.fees import schedule_from_db
from worth_api.main import create_app
from worth_api.settings import Settings
from worth_complexity.cli import FIXTURE
from worth_fees import PaymentBasis, PlaceOfService, WorthFeesError, expected_allowed
from worth_fees.sources import PINNED_VINTAGES, load

pytestmark = pytest.mark.db

URL = os.environ.get("DATABASE_URL")


@pytest.fixture(scope="module")
def settings() -> Settings:
    if not URL:
        pytest.skip("DATABASE_URL is not set")
    return Settings(dataset_dir=FIXTURE, database_url=URL)


@pytest.fixture(scope="module")
def db_client(settings: Settings) -> TestClient:
    """Booting the app applies the schema and loads whatever is missing."""
    return TestClient(create_app(settings))


def test_boot_leaves_every_pinned_vintage_priceable_from_the_database(
    db_client: TestClient,
) -> None:
    body = db_client.get("/api/health").json()
    assert body["database"] != "not configured"
    assert "worth:" not in body["database"]  # no credentials in health
    assert all(v["origin"] == "database" for v in body["fees"])
    assert len(body["fees"]) == len(PINNED_VINTAGES)


@pytest.mark.usefixtures("db_client")
def test_boot_is_idempotent(settings: Settings) -> None:
    """A second boot finds everything present and loads nothing."""
    with db.connect(settings.database_url or "") as conn:
        assert db.ensure_schema(conn) is False
        for vintage in PINNED_VINTAGES.values():
            assert db.ensure_loaded(conn, load(vintage.rule_year, vintage.quarter)) == "present"


@pytest.mark.usefixtures("db_client")
@pytest.mark.parametrize(("year", "quarter"), sorted(PINNED_VINTAGES))
def test_rows_price_exactly_as_the_fixture_does(
    settings: Settings, year: int, quarter: int
) -> None:
    fixture = load(year, quarter)
    with db.connect(settings.database_url or "") as conn:
        rebuilt = schedule_from_db(conn, fixture.vintage)

    assert set(fixture.rvus) <= set(rebuilt.rvus)
    assert set(fixture.gpcis) <= set(rebuilt.gpcis)
    assert rebuilt.conversion_factors == fixture.conversion_factors
    assert rebuilt.sources.rvu.chain()[-1].sha256 == fixture.vintage.archive_sha256

    compared = 0
    for (code, modifier), locality, setting, basis in product(
        fixture.rvus, fixture.gpcis, PlaceOfService, PaymentBasis
    ):
        args = (code, [modifier] if modifier else [], locality, setting, year, quarter)
        try:
            direct = expected_allowed(*args, payment_basis=basis)
        except WorthFeesError as refusal:
            with pytest.raises(type(refusal)):
                expected_allowed(*args, payment_basis=basis, schedule=rebuilt)
            continue
        via_db = expected_allowed(*args, payment_basis=basis, schedule=rebuilt)
        assert via_db.amount == direct.amount, (code, modifier, locality, setting, basis)
        assert via_db.trace == direct.trace, (code, modifier, locality, setting, basis)
        compared += 1
    assert compared > 0


def test_the_price_route_answers_from_the_database(db_client: TestClient) -> None:
    body = db_client.post(
        "/api/fees/price", json={"code": "99213", "locality": "CA18", "date": "2026-03-14"}
    ).json()
    direct = expected_allowed("99213", [], "CA18", PlaceOfService.NON_FACILITY, 2026, 1)
    assert body["amount"] == str(direct.amount)
