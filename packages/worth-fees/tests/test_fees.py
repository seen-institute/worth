"""The formula, its refusals, and the reproducibility properties that matter."""

from __future__ import annotations

import csv
import dataclasses
import decimal
from decimal import Decimal

import pytest
from worth_fees import (
    NotPayableError,
    PlaceOfService,
    UnknownCodeError,
    UnknownLocalityError,
    UnsupportedModifierError,
    expected_allowed,
)
from worth_fees.sources import FIXTURE_DIR, PINNED_VINTAGES

VINTAGE = PINNED_VINTAGES[(2026, 1)]
NON_FACILITY = PlaceOfService.NON_FACILITY
FACILITY = PlaceOfService.FACILITY


def _independent_amount(code: str, modifier: str, setting: PlaceOfService) -> Decimal:
    """Recompute the allowed amount straight from the fixture CSVs.

    Deliberately does not import the production accessors: if worth-fees reads
    the wrong column or applies the wrong GPCI, this disagrees with it.
    """
    rvu_rows = list(
        csv.reader(
            (FIXTURE_DIR / f"pprrvu-{VINTAGE.key}.csv").read_text(encoding="latin-1").splitlines()
        )
    )
    row = next(
        r
        for r in rvu_rows
        if r and r[0].strip() == code and r[1].strip() == modifier and r[3].strip() == "A"
    )
    work, mp = Decimal(row[5]), Decimal(row[10])
    pe = Decimal(row[8]) if setting is FACILITY else Decimal(row[6])
    conversion_factor = Decimal(row[25])

    gpci_rows = list(
        csv.reader(
            (FIXTURE_DIR / f"gpci-{VINTAGE.key}.csv").read_text(encoding="latin-1").splitlines()
        )
    )
    gpci = next(
        r for r in gpci_rows if len(r) == 8 and r[1].strip() == "CA" and r[2].strip() == "18"
    )
    work_gpci, pe_gpci, mp_gpci = Decimal(gpci[5]), Decimal(gpci[6]), Decimal(gpci[7])

    with decimal.localcontext(decimal.Context(prec=28, rounding=decimal.ROUND_HALF_EVEN)):
        total = work * work_gpci + pe * pe_gpci + mp * mp_gpci
        return (total * conversion_factor).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# The formula
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "modifier", "setting"),
    [
        ("99213", "", NON_FACILITY),
        ("99213", "", FACILITY),
        ("99214", "", NON_FACILITY),
        ("99232", "", FACILITY),
        ("20610", "", NON_FACILITY),
        ("20610", "", FACILITY),
        ("71046", "26", FACILITY),
        ("71046", "TC", NON_FACILITY),
        ("93000", "", NON_FACILITY),
        ("29881", "", FACILITY),
    ],
)
def test_matches_an_independent_recomputation(
    code: str, modifier: str, setting: PlaceOfService
) -> None:
    derivation = expected_allowed(code, [modifier] if modifier else [], "CA18", setting, 2026, 1)
    assert derivation.amount == _independent_amount(code, modifier, setting)


def test_facility_and_non_facility_use_different_practice_expense_rvus() -> None:
    office = expected_allowed("99213", [], "CA18", NON_FACILITY, 2026, 1)
    hospital = expected_allowed("99213", [], "CA18", FACILITY, 2026, 1)

    assert office.pe_rvu == Decimal("1.46")
    assert hospital.pe_rvu == Decimal("0.33")
    assert office.amount > hospital.amount
    assert office.work_rvu == hospital.work_rvu


def test_professional_and_technical_components_sum_to_the_global_service() -> None:
    """A real property of the CMS file, and a check that 26/TC select real rows."""
    global_service = expected_allowed("71046", [], "CA18", NON_FACILITY, 2026, 1)
    professional = expected_allowed("71046", ["26"], "CA18", NON_FACILITY, 2026, 1)
    technical = expected_allowed("71046", ["TC"], "CA18", NON_FACILITY, 2026, 1)

    assert professional.adjusted_rvu_total + technical.adjusted_rvu_total == pytest.approx(
        global_service.adjusted_rvu_total
    )
    assert professional.work_rvu + technical.work_rvu == global_service.work_rvu
    assert professional.pe_rvu + technical.pe_rvu == global_service.pe_rvu
    assert professional.mp_rvu + technical.mp_rvu == global_service.mp_rvu


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------


def test_no_float_anywhere_in_a_derivation() -> None:
    """Walk every field of the result and refuse to find a float.

    Written with ``getattr`` rather than named attributes on purpose: it covers
    fields added to :class:`FeeDerivation` later, and it is opaque to the type
    checker, so the assertion is a real runtime check rather than something
    ``mypy --strict`` proves away as unreachable.
    """
    derivation = expected_allowed("99213", [], "CA18", NON_FACILITY, 2026, 1)

    numeric: list[str] = []
    for field in dataclasses.fields(derivation):
        value: object = getattr(derivation, field.name)
        assert not isinstance(value, float), f"{field.name} is a float: {value!r}"
        if isinstance(value, Decimal):
            numeric.append(field.name)

    # Guard the guard: if the money fields ever stop being Decimal, the loop
    # above would pass vacuously.
    assert {"amount", "work_rvu", "pe_rvu", "mp_rvu", "conversion_factor"} <= set(numeric)


def test_result_is_independent_of_the_ambient_decimal_context() -> None:
    """The reproducibility property the whole project rests on."""
    baseline = expected_allowed("99213", [], "CA18", NON_FACILITY, 2026, 1).amount
    with decimal.localcontext() as ctx:
        ctx.prec = 4
        ctx.rounding = decimal.ROUND_UP
        assert expected_allowed("99213", [], "CA18", NON_FACILITY, 2026, 1).amount == baseline


def test_derivation_is_deterministic() -> None:
    first = expected_allowed("99214", [], "CA18", NON_FACILITY, 2026, 1)
    second = expected_allowed("99214", [], "CA18", NON_FACILITY, 2026, 1)
    assert first == second


def test_trace_shows_every_input_and_the_running_sum() -> None:
    derivation = expected_allowed("99213", [], "CA18", NON_FACILITY, 2026, 1)
    body = "\n".join(derivation.trace)

    for value in (
        derivation.work_rvu,
        derivation.pe_rvu,
        derivation.mp_rvu,
        derivation.work_gpci,
        derivation.pe_gpci,
        derivation.mp_gpci,
        derivation.conversion_factor,
        derivation.adjusted_rvu_total,
    ):
        assert str(value) in body, f"{value} missing from trace"
    assert "running total" in body
    assert f"{derivation.amount:.2f}" in body
    assert all(isinstance(step, str) for step in derivation.trace)


def test_source_carries_filename_hash_and_release_date() -> None:
    source = expected_allowed("99213", [], "CA18", NON_FACILITY, 2026, 1).source
    assert source.release == "RVU26A"
    assert source.release_date.isoformat() == "2025-12-29"
    for entry in source.files:
        assert entry.filename
        assert len(entry.sha256) == 64
        assert entry.release_date == source.release_date


# ---------------------------------------------------------------------------
# Refusals: a wrong number is worse than an error
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "setting"),
    [
        ("29881", NON_FACILITY),  # surgical, facility-only
        ("99232", NON_FACILITY),  # inpatient care, facility-only
        ("93000", FACILITY),  # global ECG, not paid in facility
        ("71046", FACILITY),  # global x-ray, not paid in facility
    ],
)
def test_na_practice_expense_indicator_refuses_to_price(code: str, setting: PlaceOfService) -> None:
    with pytest.raises(NotPayableError, match="NA"):
        expected_allowed(code, [], "CA18", setting, 2026, 1)


def test_unknown_code_names_the_fixture_limit() -> None:
    # 99406 (smoking/tobacco cessation counseling) is not one of the codes
    # any pack's comparators or work rules need, so it stays outside
    # FIXTURE_CODES on purpose, as a code guaranteed to exercise this path.
    with pytest.raises(UnknownCodeError, match="fixture"):
        expected_allowed("99406", [], "CA18", NON_FACILITY, 2026, 1)


def test_unknown_modifier_variant_lists_what_exists() -> None:
    """99213 has no technical component, and the error should say so."""
    with pytest.raises(UnknownCodeError, match=r"exists with modifiers \[''\]"):
        expected_allowed("99213", ["TC"], "CA18", NON_FACILITY, 2026, 1)


def test_unknown_locality_lists_available_localities() -> None:
    with pytest.raises(UnknownLocalityError, match="CA18"):
        expected_allowed("99213", [], "NY99", NON_FACILITY, 2026, 1)


@pytest.mark.parametrize("modifier", ["50", "51", "62", "80", "AS", "53"])
def test_payment_adjusting_modifiers_are_refused_not_ignored(modifier: str) -> None:
    with pytest.raises(UnsupportedModifierError, match="scales the allowed amount"):
        expected_allowed("29881", [modifier], "CA18", FACILITY, 2026, 1)


def test_unrecognised_modifier_is_refused() -> None:
    with pytest.raises(UnsupportedModifierError, match="not recognised"):
        expected_allowed("99213", ["ZZ"], "CA18", NON_FACILITY, 2026, 1)


def test_professional_and_technical_are_mutually_exclusive() -> None:
    with pytest.raises(UnsupportedModifierError, match="mutually exclusive"):
        expected_allowed("71046", ["26", "TC"], "CA18", NON_FACILITY, 2026, 1)


def test_inert_modifiers_do_not_change_the_amount() -> None:
    plain = expected_allowed("99213", [], "CA18", NON_FACILITY, 2026, 1)
    with_modifier = expected_allowed("99213", ["25"], "CA18", NON_FACILITY, 2026, 1)
    assert with_modifier.amount == plain.amount
    assert with_modifier.modifiers == ("25",)
    assert with_modifier.modifier == ""


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("locality", ["CA18", "ca18", "CA-18", "ca 18", " CA18 "])
def test_locality_spellings_normalise(locality: str) -> None:
    assert expected_allowed("99213", [], locality, NON_FACILITY, 2026, 1).locality == "CA18"


def test_place_of_service_accepts_its_string_value() -> None:
    assert (
        expected_allowed("99213", [], "CA18", "non-facility", 2026, 1).amount
        == expected_allowed("99213", [], "CA18", NON_FACILITY, 2026, 1).amount
    )
