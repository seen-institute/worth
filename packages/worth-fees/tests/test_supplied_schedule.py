"""``expected_allowed(schedule=...)``: pricing from a schedule the caller supplies."""

from __future__ import annotations

import pytest
from worth_fees import PlaceOfService, VintageError, expected_allowed
from worth_fees.sources import load


def test_a_supplied_schedule_prices_identically_to_the_fixture_path() -> None:
    supplied = load(2026, 1)
    via_fixture = expected_allowed("99213", [], "CA18", PlaceOfService.NON_FACILITY, 2026, 1)
    via_supplied = expected_allowed(
        "99213", [], "CA18", PlaceOfService.NON_FACILITY, 2026, 1, schedule=supplied
    )
    assert via_supplied.amount == via_fixture.amount
    assert via_supplied.trace == via_fixture.trace
    assert via_supplied.source == via_fixture.source


def test_a_schedule_of_the_wrong_vintage_is_refused() -> None:
    """The caller names the vintage twice; the two must agree or nothing is priced."""
    with pytest.raises(VintageError, match="RVU26B"):
        expected_allowed(
            "99213", [], "CA18", PlaceOfService.NON_FACILITY, 2026, 1, schedule=load(2026, 2)
        )
