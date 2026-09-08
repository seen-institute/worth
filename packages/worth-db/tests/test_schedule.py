"""The drift test: every fixture code priced identically from a fixture and from Postgres.

Every code and modifier in the committed fixture, in every fixture locality,
in both settings and on both bases, is priced twice: once from the fixture
the package ships, once from a schedule rebuilt out of database rows. Amount
and trace must be identical.
"""

from __future__ import annotations

import os
from itertools import product

import pytest
from worth_db import db
from worth_db.schedule import load_all, schedule_from_db
from worth_fees import PaymentBasis, PlaceOfService, WorthFeesError, expected_allowed
from worth_fees.sources import PINNED_VINTAGES, load

DATABASE_URL = os.environ.get("DATABASE_URL")


@pytest.fixture(scope="module")
def loaded() -> str:
    if not DATABASE_URL:
        pytest.skip("DATABASE_URL is not set")
    load_all(DATABASE_URL)
    return DATABASE_URL


@pytest.mark.db
@pytest.mark.parametrize(("year", "quarter"), sorted(PINNED_VINTAGES))
def test_rows_price_exactly_as_the_fixture_does(loaded: str, year: int, quarter: int) -> None:
    fixture = load(year, quarter)
    with db.connect(loaded) as conn:
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


@pytest.mark.db
def test_a_price_from_load_all_matches_the_fixture(loaded: str) -> None:
    schedules = load_all(loaded)
    schedule = schedules[(2026, 1)]
    direct = expected_allowed("99213", [], "CA18", PlaceOfService.NON_FACILITY, 2026, 1)
    via_db = expected_allowed(
        "99213", [], "CA18", PlaceOfService.NON_FACILITY, 2026, 1, schedule=schedule
    )
    assert via_db.amount == direct.amount
    assert via_db.trace == direct.trace
