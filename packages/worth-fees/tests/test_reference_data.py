"""Data that is stored but never priced from.

Two additions pull in CMS columns and files the formula does not touch: the
payment-policy indicators on each RVU row, and the locality-to-county
crosswalk. Both are reference data, and the tests here are mostly about what
they must *not* do, leak into the arithmetic, or quietly become a lookup that
answers more than the source supports.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from worth_fees import expected_allowed
from worth_fees.models import PlaceOfService, SourceIntegrityError
from worth_fees.sources import PINNED_VINTAGES, load, parse_locco

NON_FACILITY = PlaceOfService.NON_FACILITY
QUARTERS_WITH_CROSSWALK = [1, 2, 4]


# ---------------------------------------------------------------------------
# Payment-policy indicators
# ---------------------------------------------------------------------------


def test_policy_indicators_are_parsed() -> None:
    """29881 is knee arthroscopy: a 90-day global with an endoscopic base code."""
    policy = load(2026, 1).rvus[("29881", "")].policy
    assert policy.endoscopic_base == "29870"
    assert policy.multiple_procedure == "3"
    assert policy.bilateral_surgery == "1"


def test_an_office_visit_carries_no_endoscopic_base() -> None:
    assert load(2026, 1).rvus[("99213", "")].policy.endoscopic_base == ""


def test_work_splits_sum_to_one_or_zero() -> None:
    """CMS apportions the work RVU across phases of care, or not at all. The
    schema encodes this as a CHECK, so a third shape would break a load."""
    for row in load(2026, 1).rvus.values():
        assert row.policy.work_split_total in (Decimal("0.00"), Decimal("1.00"))


def test_a_global_period_code_has_a_work_split() -> None:
    """29881 carries 90 days of follow-up, so its work is apportioned."""
    policy = load(2026, 1).rvus[("29881", "")].policy
    assert policy.work_split_total == Decimal("1.00")
    assert policy.post_op > 0


def test_an_xxx_global_code_has_no_work_split() -> None:
    policy = load(2026, 1).rvus[("99213", "")].policy
    assert policy.work_split_total == Decimal("0.00")


def test_policy_indicators_do_not_reach_the_arithmetic() -> None:
    """The formula is three products and a sum. Nothing here may enter it."""
    derivation = expected_allowed("29881", [], "CA18", PlaceOfService.FACILITY, 2026, 1)
    policy = load(2026, 1).rvus[("29881", "")].policy
    assert policy.endoscopic_base not in "".join(derivation.trace)
    assert derivation.adjusted_rvu_total == (
        derivation.work_rvu * derivation.work_gpci
        + derivation.pe_rvu * derivation.pe_gpci
        + derivation.mp_rvu * derivation.mp_gpci
    )


# ---------------------------------------------------------------------------
# Locality-to-county crosswalk
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("quarter", QUARTERS_WITH_CROSSWALK)
def test_crosswalk_covers_every_locality(quarter: int) -> None:
    """109 localities in the GPCI file, and the crosswalk accounts for all of
    them, some across several rows, where a locality spans county groups."""
    schedule = load(2026, quarter)
    assert len(schedule.localities) >= len(schedule.gpcis)
    pairs = {(row.mac, row.locality_number) for row in schedule.localities}
    assert {(g.mac, g.locality) for g in schedule.gpcis.values()} <= pairs


def test_the_state_column_is_carried_forward() -> None:
    """CMS fills the state in only on the first locality of each state. Every
    row must still name one, or a subset of this file would be mislabelled."""
    localities = load(2026, 1).localities
    assert all(row.state_name for row in localities)
    assert {"CALIFORNIA", "ALABAMA", "WYOMING"} <= {r.state_name for r in localities}


def test_county_text_is_preserved_verbatim() -> None:
    """CMS's own typo. Fixing it would mean storing a guess about their intent,
    and the next reader would have no way to tell ours from theirs."""
    california = [r for r in load(2026, 1).localities if r.state_name == "CALIFORNIA"]
    assert any("ORAGNGE" in r.counties for r in california)


def test_statewide_localities_say_so() -> None:
    alabama = [r for r in load(2026, 1).localities if r.state_name == "ALABAMA"]
    assert len(alabama) == 1
    assert alabama[0].counties == "ALL COUNTIES"
    assert alabama[0].fee_schedule_area == "STATEWIDE"


def test_the_trailing_footnote_is_not_a_locality() -> None:
    """The file ends with a prose footnote about dual-carrier localities."""
    assert all(row.mac.isdigit() for row in load(2026, 1).localities)
    assert not any("Payment locality" in row.counties for row in load(2026, 1).localities)


def test_crosswalk_requires_a_header() -> None:
    with pytest.raises(SourceIntegrityError, match="Locality Number"):
        parse_locco("just,some,rows\n1,2,3\n")


def test_a_release_without_a_readable_crosswalk_simply_has_none() -> None:
    """RVU26C ships 26LOCCO as .xlsx only. The package parses CSV with the
    standard library and takes no dependency to read a spreadsheet, so that
    release has no crosswalk rather than one obtained some other way."""
    assert PINNED_VINTAGES[(2026, 3)].locco_member is None
    schedule = load(2026, 3)
    assert schedule.localities == ()
    assert schedule.locco_source is None
    # Everything that is priced still works.
    assert expected_allowed("99213", [], "CA18", NON_FACILITY, 2026, 3).amount > 0


def test_the_crosswalk_is_not_an_address_lookup() -> None:
    """A guard on scope, not behaviour. The crosswalk names counties in prose;
    resolving an address needs CMS's separate ZIP-code file, which is not
    pinned here. If a resolver is ever added, it must not be built on this."""
    schedule = load(2026, 1)
    assert not hasattr(schedule, "locality_for_zip")
    assert not hasattr(schedule, "locality_for_county")
