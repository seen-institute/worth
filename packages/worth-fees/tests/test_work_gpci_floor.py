"""The statutory 1.0 floor on the work GPCI, and whether the choice is tested.

Whether the floor is in force is a question of law, not of CMS policy, so
``APPLY_WORK_GPCI_FLOOR`` is a decision the package makes rather than reads.
That makes it exactly the kind of setting that must not be silently flippable.

Before AL00 was added to the committed fixture, it was: the only sampled
locality was CA18, whose work GPCI is 1.041 with the floor and 1.041 without.
Every test passed either way. AL00 is 0.988 against 1.000, so it does not.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from worth_fees import expected_allowed, sources
from worth_fees.models import PlaceOfService
from worth_fees.sources import load

NON_FACILITY = PlaceOfService.NON_FACILITY


def test_the_fixture_samples_a_floor_affected_locality() -> None:
    """Without this, APPLY_WORK_GPCI_FLOOR is untested by everything else here."""
    gpcis = load(2026, 1).gpcis
    affected = [g for g in gpcis.values() if g.work_gpci_no_floor != g.work_gpci_with_floor]
    assert affected, (
        "no sampled locality is affected by the work GPCI floor, so the floor "
        "setting could be flipped without failing a single test. Add one to "
        "FIXTURE_LOCALITIES in cli.py and rebuild."
    )


def test_alabama_is_the_floor_affected_sample() -> None:
    alabama = load(2026, 1).gpcis["AL00"]
    assert alabama.work_gpci_no_floor == Decimal("0.988")
    assert alabama.work_gpci_with_floor == Decimal("1.000")


def test_california_is_floor_neutral() -> None:
    """CA18 is why the demo amount does not depend on the floor decision."""
    california = load(2026, 1).gpcis["CA18"]
    assert california.work_gpci_no_floor == california.work_gpci_with_floor


def test_the_floored_column_is_the_one_in_use() -> None:
    assert sources.APPLY_WORK_GPCI_FLOOR is True
    assert load(2026, 1).gpcis["AL00"].work_gpci == Decimal("1.000")


def test_flipping_the_floor_changes_a_published_amount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of the whole file: the setting is load-bearing, and provably so."""
    floored = expected_allowed("99213", [], "AL00", NON_FACILITY, 2026, 1)

    monkeypatch.setattr(sources, "APPLY_WORK_GPCI_FLOOR", False)
    unfloored = expected_allowed("99213", [], "AL00", NON_FACILITY, 2026, 1)

    assert floored.work_gpci == Decimal("1.000")
    assert unfloored.work_gpci == Decimal("0.988")
    assert floored.amount != unfloored.amount
    assert floored.amount > unfloored.amount


def test_flipping_the_floor_leaves_a_neutral_locality_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    floored = expected_allowed("99213", [], "CA18", NON_FACILITY, 2026, 1)
    monkeypatch.setattr(sources, "APPLY_WORK_GPCI_FLOOR", False)
    unfloored = expected_allowed("99213", [], "CA18", NON_FACILITY, 2026, 1)
    assert floored.amount == unfloored.amount == Decimal("104.89")


def test_the_floor_choice_is_recorded_in_the_trace() -> None:
    """A reader must be able to see which column produced the number."""
    derivation = expected_allowed("99213", [], "AL00", NON_FACILITY, 2026, 1)
    assert any("with 1.0 floor" in step for step in derivation.trace)


@pytest.mark.parametrize("quarter", [2, 3, 4])
def test_later_quarters_publish_only_the_floored_column(quarter: int) -> None:
    """From Q2 2026 CMS dropped the unfloored column, answering the question
    itself. The parser stands the floored value in for both."""
    alabama = load(2026, quarter).gpcis["AL00"]
    assert alabama.work_gpci_no_floor == alabama.work_gpci_with_floor == Decimal("1.000")
