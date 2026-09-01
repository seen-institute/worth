"""The command line surface: time parsing, pricing, and offline vintage coverage."""

from __future__ import annotations

import json
from datetime import date
from itertools import pairwise

import pytest
from worth_fees.cli import main, parse_when
from worth_fees.models import VintageError
from worth_fees.sources import PINNED_VINTAGES, load, vintage_for_date


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-01-15", (2026, 1)),
        ("2026-03-31", (2026, 1)),
        ("2026-04-01", (2026, 2)),
        ("2026-09-30", (2026, 3)),
        ("2026-10-01", (2026, 4)),
        ("2026Q2", (2026, 2)),
        ("2026q4", (2026, 4)),
        ("2026", (2026, 1)),
    ],
)
def test_parse_when_accepts_dates_quarters_and_years(text: str, expected: tuple[int, int]) -> None:
    assert parse_when(text) == expected


def test_parse_when_accepts_today() -> None:
    assert parse_when("today") == parse_when(date.today().isoformat())


@pytest.mark.parametrize("text", ["never", "2026-13-01", "20260101", "Q2", ""])
def test_parse_when_rejects_nonsense(text: str) -> None:
    with pytest.raises(VintageError):
        parse_when(text)


def test_quarter_boundaries_are_half_open() -> None:
    """The last day of a quarter and the first of the next resolve differently."""
    assert vintage_for_date(date(2026, 6, 30)).label == "RVU26B"
    assert vintage_for_date(date(2026, 7, 1)).label == "RVU26C"


def test_service_date_outside_pinned_range_is_refused() -> None:
    with pytest.raises(VintageError, match="no pinned CMS vintage covers"):
        vintage_for_date(date(2019, 5, 1))


@pytest.mark.parametrize("key", sorted(PINNED_VINTAGES))
def test_every_pinned_vintage_loads_offline(key: tuple[int, int]) -> None:
    """Each pinned quarter must have a committed fixture; the CLI relies on it."""
    schedule = load(*key)
    assert schedule.vintage.label == PINNED_VINTAGES[key].label
    assert schedule.rvus
    assert schedule.gpcis
    assert schedule.conversion_factor > 0


def test_pinned_vintages_tile_the_year_without_gaps() -> None:
    ranges = sorted(v.effective for v in PINNED_VINTAGES.values())
    for (_, end), (next_start, _) in pairwise(ranges):
        assert end == next_start, "quarters must abut exactly, or a date falls in no release"


def test_price_prints_only_the_amount(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["price", "99213", "CA18", "2026-03-14", "--amount"]) == 0
    assert capsys.readouterr().out.strip() == "104.89"


def test_price_facility_differs_from_office(capsys: pytest.CaptureFixture[str]) -> None:
    main(["price", "99213", "CA18", "2026-03-14", "--amount"])
    office = capsys.readouterr().out.strip()
    main(["price", "99213", "CA18", "2026-03-14", "--facility", "--amount"])
    facility = capsys.readouterr().out.strip()
    assert office != facility


def test_price_json_is_machine_readable(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["price", "71046", "CA18", "2026-03-14", "--modifier", "TC", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["amount"] == "27.09"
    assert payload["currency"] == "USD"
    assert payload["modifier"] == "TC"
    assert payload["locality"] == "CA18"
    assert payload["trace"]
    # Provenance survives serialisation: three chain links per file, hashes intact.
    assert len(payload["source"]["files"]) == 6
    assert all(len(f["sha256"]) == 64 for f in payload["source"]["files"])


def test_price_reports_errors_and_exits_nonzero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["price", "99213", "ZZ99", "2026-03-14"]) == 2
    assert "UnknownLocalityError" in capsys.readouterr().err
