"""The decimal discipline is the product. These tests hold it in place."""

from __future__ import annotations

import decimal
from decimal import Decimal, DivisionByZero, InvalidOperation

import pytest
from worth_fees.money import CENTS, WORTH_CONTEXT, money_context, to_cents, usd


def test_context_is_pinned_exactly_as_specified() -> None:
    assert WORTH_CONTEXT.prec == 28
    assert WORTH_CONTEXT.rounding == decimal.ROUND_HALF_EVEN
    assert WORTH_CONTEXT.traps[InvalidOperation]
    assert WORTH_CONTEXT.traps[DivisionByZero]


def test_money_context_does_not_depend_on_ambient_state() -> None:
    """A hostile caller mangling the global context must not change our answers."""
    with decimal.localcontext() as ambient:
        ambient.prec = 3
        ambient.rounding = decimal.ROUND_UP
        with money_context() as ctx:
            assert ctx.prec == 28
            assert ctx.rounding == decimal.ROUND_HALF_EVEN
            assert Decimal("1.234567890123") * Decimal("1") == Decimal("1.234567890123")


def test_money_context_restores_the_previous_context() -> None:
    before = decimal.getcontext().prec
    with money_context():
        pass
    assert decimal.getcontext().prec == before


def test_pinned_context_object_is_never_mutated() -> None:
    """``localcontext`` copies, so the module-level context stays pristine."""
    with money_context() as ctx:
        ctx.prec = 5
    assert WORTH_CONTEXT.prec == 28


def test_invalid_operation_traps_instead_of_returning_nan() -> None:
    with pytest.raises(InvalidOperation), money_context():
        Decimal("Infinity") - Decimal("Infinity")


def test_division_by_zero_traps_instead_of_returning_infinity() -> None:
    with pytest.raises(DivisionByZero), money_context():
        Decimal("1") / Decimal("0")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("104.886842216", "104.89"),
        ("0.005", "0.00"),  # ROUND_HALF_EVEN: ties go to the even digit
        ("0.015", "0.02"),
        ("0.025", "0.02"),
        ("-0.005", "-0.00"),
    ],
)
def test_to_cents_rounds_half_even(raw: str, expected: str) -> None:
    assert to_cents(Decimal(raw)) == Decimal(expected)


def test_to_cents_returns_exactly_two_places() -> None:
    assert to_cents(Decimal("7")).as_tuple().exponent == CENTS.as_tuple().exponent


def test_usd_formats_with_separators() -> None:
    assert usd(Decimal("1234.5")) == "$1,234.50"
