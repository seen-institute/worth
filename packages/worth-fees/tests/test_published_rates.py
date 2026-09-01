"""Do we reproduce Medicare's published rates?

This is the test the project exists to pass, and the one place where a
fabricated number would do real damage: an expected value invented to make a
test go green would look exactly like evidence of correctness while destroying
the only property WORTH claims to have.

So there are two separate tables here, and they are not the same kind of thing.

``VERIFIED_CMS_RATES`` holds amounts a human has confirmed against the CMS
Physician Fee Schedule Look-Up Tool. It is empty until that happens. Nothing
may be added to it on the strength of our own output.

``COMPUTED_UNVERIFIED`` holds what this implementation currently produces. It
is a drift alarm, not evidence: it catches a refactor that silently changes a
published number. It is explicitly *not* a claim that these amounts are right.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from worth_fees import PlaceOfService, expected_allowed

LOOKUP_TOOL = "https://www.cms.gov/medicare/physician-fee-schedule/search"

Case = tuple[str, tuple[str, ...], str, PlaceOfService]

# --------------------------------------------------------------------------
# Confirmed against the CMS Physician Fee Schedule Look-Up Tool by a human.
# Add an entry ONLY after checking that exact case in the tool. Record who
# checked it and when in the commit message.
# --------------------------------------------------------------------------
VERIFIED_CMS_RATES: dict[Case, str] = {
    # ("99213", (), "CA18", PlaceOfService.NON_FACILITY): "104.89",
}

# --------------------------------------------------------------------------
# Current output of this implementation. NOT independently verified.
# Regenerate with `just verify-rates`.
# --------------------------------------------------------------------------
COMPUTED_UNVERIFIED: dict[Case, str] = {
    ("99213", (), "CA18", PlaceOfService.NON_FACILITY): "104.89",
    ("99213", (), "CA18", PlaceOfService.FACILITY): "60.24",
    ("99214", (), "CA18", PlaceOfService.NON_FACILITY): "148.89",
    ("99232", (), "CA18", PlaceOfService.FACILITY): "73.75",
    ("20610", (), "CA18", PlaceOfService.NON_FACILITY): "75.49",
    ("71046", ("26",), "CA18", PlaceOfService.FACILITY): "10.68",
    ("71046", ("TC",), "CA18", PlaceOfService.NON_FACILITY): "27.09",
    ("93000", (), "CA18", PlaceOfService.NON_FACILITY): "17.02",
    ("29881", (), "CA18", PlaceOfService.FACILITY): "553.33",
}


def _amount(case: Case) -> Decimal:
    code, modifiers, locality, setting = case
    return expected_allowed(code, list(modifiers), locality, setting, 2026, 1).amount


@pytest.mark.skipif(
    not VERIFIED_CMS_RATES,
    reason=(
        "No CMS-verified rates recorded yet. Run `just verify-rates`, check each amount "
        f"against {LOOKUP_TOOL}, then paste the confirmed values into VERIFIED_CMS_RATES."
    ),
)
def test_reproduces_published_medicare_rates() -> None:
    mismatches = {
        case: (str(_amount(case)), expected)
        for case, expected in VERIFIED_CMS_RATES.items()
        if _amount(case) != Decimal(expected)
    }
    assert not mismatches, f"computed != CMS published: {mismatches}"


@pytest.mark.parametrize("case", list(COMPUTED_UNVERIFIED), ids=str)
def test_no_unintended_drift_in_computed_amounts(case: Case) -> None:
    """Drift alarm only. Passing does not mean the amount matches CMS."""
    assert _amount(case) == Decimal(COMPUTED_UNVERIFIED[case])


def test_verified_and_computed_tables_do_not_contradict() -> None:
    """Once a rate is verified, the drift table must be updated to match it."""
    for case, verified in VERIFIED_CMS_RATES.items():
        computed = COMPUTED_UNVERIFIED.get(case)
        if computed is not None:
            assert Decimal(computed) == Decimal(verified), (
                f"{case}: COMPUTED_UNVERIFIED says {computed}, CMS-verified value is "
                f"{verified}. Fix the implementation, then update the drift table."
            )
