"""Postgres: schema application, idempotent loading, and what is loaded.

These need a database. Set ``DATABASE_URL`` (the compose one is
``postgresql://worth:worth@127.0.0.1:55432/worth``) and they run; leave it
unset and they skip. CI provides one.
"""

from __future__ import annotations

import os

import pytest
from worth_db import db
from worth_db.schedule import load_all
from worth_fees.sources import PINNED_VINTAGES, load

pytestmark = pytest.mark.db

DATABASE_URL = os.environ.get("DATABASE_URL")


@pytest.fixture(scope="module")
def loaded() -> str:
    """Boot once: every pinned vintage loaded and priceable."""
    if not DATABASE_URL:
        pytest.skip("DATABASE_URL is not set")
    load_all(DATABASE_URL)
    return DATABASE_URL


def test_every_pinned_vintage_is_loaded(loaded: str) -> None:
    schedules = load_all(loaded)
    assert set(schedules) == set(PINNED_VINTAGES)


def test_boot_is_idempotent(loaded: str) -> None:
    """A second boot finds the schema and every vintage already present."""
    with db.connect(loaded) as conn:
        assert db.ensure_schema(conn) is False
        for vintage in PINNED_VINTAGES.values():
            assert db.ensure_loaded(conn, load(vintage.rule_year, vintage.quarter)) == "present"


def test_releases_reports_rows_and_coverage(loaded: str) -> None:
    with db.connect(loaded) as conn:
        releases = db.releases(conn)
    assert set(releases) == set(PINNED_VINTAGES)
    for release in releases.values():
        assert release.rvu_rows > 0
        assert release.localities > 0
        assert release.coverage in {"archive", "fixture"}
