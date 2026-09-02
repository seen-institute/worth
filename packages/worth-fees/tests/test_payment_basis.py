"""The two CY2026 conversion factors, and the invariant that lets them share rows.

worth-fees stores one set of RVU rows and two conversion factors. That is only
sound because CMS's non-QPP and QPP files agree on everything except the factor
itself. These tests hold that assumption to account: the arithmetic here is
trivial, the point is that the assumption is checked rather than trusted.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal

import pytest
from worth_fees import expected_allowed, fees
from worth_fees.models import (
    NotPayableError,
    PaymentBasis,
    PlaceOfService,
    SourceIntegrityError,
)
from worth_fees.sources import (
    FIXTURE_DIR,
    PINNED_VINTAGES,
    RvuRow,
    _cross_check_qpp,
    load,
    parse_pprrvu,
)

NON_FACILITY = PlaceOfService.NON_FACILITY
VINTAGE = PINNED_VINTAGES[(2026, 1)]

# CMS's published CY2026 factors. Hard-coded on purpose: if a refactor started
# reading the wrong column, comparing the file against itself would not notice.
NON_QUALIFYING_CF = Decimal("33.4009")
QUALIFYING_CF = Decimal("33.5675")


# ---------------------------------------------------------------------------
# The factors themselves
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("quarter", [1, 2, 3, 4])
def test_both_conversion_factors_are_read_from_the_files(quarter: int) -> None:
    schedule = load(2026, quarter)
    assert schedule.conversion_factors == {
        PaymentBasis.NON_QUALIFYING_APM: NON_QUALIFYING_CF,
        PaymentBasis.QUALIFYING_APM: QUALIFYING_CF,
    }


def test_default_basis_is_non_qualifying() -> None:
    """An unqualified question gets the factor most clinicians are paid on."""
    default = expected_allowed("99213", [], "CA18", NON_FACILITY, 2026, 1)
    explicit = expected_allowed(
        "99213",
        [],
        "CA18",
        NON_FACILITY,
        2026,
        1,
        payment_basis=PaymentBasis.NON_QUALIFYING_APM,
    )
    assert default.payment_basis is PaymentBasis.NON_QUALIFYING_APM
    assert default.amount == explicit.amount == Decimal("104.89")


def test_qualifying_basis_differs_only_by_the_conversion_factor() -> None:
    """Same RVUs, same GPCIs, one different multiplier, and it shows."""
    base = expected_allowed("99213", [], "CA18", NON_FACILITY, 2026, 1)
    qualifying = expected_allowed(
        "99213", [], "CA18", NON_FACILITY, 2026, 1, payment_basis="qualifying-apm"
    )

    assert (base.work_rvu, base.pe_rvu, base.mp_rvu) == (
        qualifying.work_rvu,
        qualifying.pe_rvu,
        qualifying.mp_rvu,
    )
    assert base.adjusted_rvu_total == qualifying.adjusted_rvu_total
    assert base.conversion_factor == NON_QUALIFYING_CF
    assert qualifying.conversion_factor == QUALIFYING_CF
    assert qualifying.amount > base.amount


def test_the_basis_is_recorded_on_the_derivation() -> None:
    """Which factor was applied is part of the answer, not a hidden setting."""
    derivation = expected_allowed(
        "99213", [], "CA18", NON_FACILITY, 2026, 1, payment_basis="qualifying-apm"
    )
    assert derivation.payment_basis is PaymentBasis.QUALIFYING_APM
    assert "qualifying APM conversion factor" in derivation.trace[0]
    assert str(QUALIFYING_CF) in derivation.render()


# ---------------------------------------------------------------------------
# Which codes each basis covers
# ---------------------------------------------------------------------------


def test_a_code_absent_from_the_qpp_file_is_flagged() -> None:
    """00100 is anesthesia: in the base file, absent from the qualifying one."""
    schedule = load(2026, 1)
    assert schedule.rvus[("00100", "")].qpp_eligible is False
    assert schedule.rvus[("99213", "")].qpp_eligible is True


def test_a_code_outside_the_qpp_file_fails_on_its_status_first() -> None:
    """00100 is absent from the QPP file *and* status J. The status is the
    better answer, so that is the one the caller gets."""
    with pytest.raises(NotPayableError, match="status code J"):
        expected_allowed("00100", [], "CA18", NON_FACILITY, 2026, 1, payment_basis="qualifying-apm")


def test_qualifying_basis_refuses_a_payable_code_it_does_not_cover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Belt and braces, and deliberately unreachable against real CMS data.

    ``_cross_check_qpp`` refuses to load a release where a payable code is
    missing from the QPP file, so this branch cannot fire in production, it
    exists so that if that guard is ever loosened, the failure is a refusal
    rather than a price computed from the wrong file. Reaching it therefore
    takes a hand-built schedule.
    """
    schedule = load(2026, 1)
    doctored = replace(
        schedule,
        rvus=dict(schedule.rvus)
        | {("99213", ""): replace(schedule.rvus[("99213", "")], qpp_eligible=False)},
    )
    monkeypatch.setattr(fees, "_schedule", lambda *_: doctored)

    with pytest.raises(NotPayableError, match="qualifying-APM file"):
        expected_allowed("99213", [], "CA18", NON_FACILITY, 2026, 1, payment_basis="qualifying-apm")


@pytest.mark.parametrize("quarter", [1, 2, 3, 4])
def test_every_payable_code_exists_under_both_bases(quarter: int) -> None:
    """The subset only ever drops codes that carry no allowed amount anyway.

    This is what makes ``qpp_eligible`` a fact about the source files rather
    than a gate on pricing. If CMS ever drops a payable code from the QPP file,
    the basis starts changing coverage and not just the multiplier, so this
    is asserted, not assumed.
    """
    schedule = load(2026, quarter)
    unpayable = {
        key for key, row in schedule.rvus.items() if row.status_code not in {"A", "R", "T"}
    }
    assert {key for key, row in schedule.rvus.items() if not row.qpp_eligible} <= unpayable


# ---------------------------------------------------------------------------
# The guards that keep the shared-row design honest
# ---------------------------------------------------------------------------


def test_wrong_file_for_the_basis_is_refused() -> None:
    """CMS labels each file in its PRIC IND column; reading the QPP file where
    the base one was meant would be wrong by $0.17 per RVU, silently."""
    text = (FIXTURE_DIR / f"pprrvu-{VINTAGE.key}.csv").read_text(encoding="latin-1")
    with pytest.raises(SourceIntegrityError, match="pricing indicator"):
        parse_pprrvu(text, expect_basis=PaymentBasis.QUALIFYING_APM)


def test_the_expected_basis_check_passes_on_the_right_file() -> None:
    for name, basis in (
        (f"pprrvu-{VINTAGE.key}.csv", PaymentBasis.NON_QUALIFYING_APM),
        (f"pprrvu-qpp-{VINTAGE.key}.csv", PaymentBasis.QUALIFYING_APM),
    ):
        text = (FIXTURE_DIR / name).read_text(encoding="latin-1")
        rows, _ = parse_pprrvu(text, expect_basis=basis)
        assert rows


def _fixture_pair() -> tuple[dict[tuple[str, str], RvuRow], dict[tuple[str, str], RvuRow]]:
    base, _ = parse_pprrvu(
        (FIXTURE_DIR / f"pprrvu-{VINTAGE.key}.csv").read_text(encoding="latin-1")
    )
    qpp, _ = parse_pprrvu(
        (FIXTURE_DIR / f"pprrvu-qpp-{VINTAGE.key}.csv").read_text(encoding="latin-1")
    )
    return base, qpp


def test_the_real_files_agree() -> None:
    """The premise of the whole design, asserted against the committed pair."""
    base, qpp = _fixture_pair()
    _cross_check_qpp(base, qpp)


def _diverge_work_rvu(row: RvuRow) -> RvuRow:
    return replace(row, work_rvu=row.work_rvu + Decimal("0.01"))


def _diverge_status(row: RvuRow) -> RvuRow:
    return replace(row, status_code="C")


def _diverge_global(row: RvuRow) -> RvuRow:
    return replace(row, global_days="090")


def _diverge_policy(row: RvuRow) -> RvuRow:
    return replace(row, policy=replace(row.policy, bilateral_surgery="9"))


@pytest.mark.parametrize(
    "diverge",
    [_diverge_work_rvu, _diverge_status, _diverge_global, _diverge_policy],
    ids=["work_rvu", "status_code", "global_days", "policy_indicator"],
)
def test_diverging_rows_between_the_bases_are_caught(
    diverge: Callable[[RvuRow], RvuRow],
) -> None:
    """If the two files ever stop agreeing, loading must fail rather than
    price the qualifying basis off the other file's RVUs.

    Divergence is injected on the parsed row rather than in the CSV text: a
    text edit trips ``_cross_check`` against CMS's own totals first, which is
    the earlier guard doing its job and not what is under test here.
    """
    base, qpp = _fixture_pair()
    key = ("99213", "")
    qpp[key] = diverge(qpp[key])
    with pytest.raises(SourceIntegrityError, match="differs between the non-QPP and QPP"):
        _cross_check_qpp(base, qpp)


def test_a_payable_code_missing_from_the_qpp_file_is_caught() -> None:
    base, qpp = _fixture_pair()
    del qpp[("99213", "")]
    with pytest.raises(SourceIntegrityError, match="payable code"):
        _cross_check_qpp(base, qpp)
