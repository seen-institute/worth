"""Money arithmetic for WORTH.

This module is a deliberate, byte-for-byte copy of ``worth_fees.money``.

worth-fees and worth-complexity are published as independent packages, on
independent release cadences, and an auditor is expected to be able to read
either one without installing the other. A shared ``worth-core`` would buy a
few dozen lines of deduplication at the cost of a dependency edge between two
things that must stay separately auditable. The copy is guarded instead: see
``tests/test_money_drift.py``, which fails if the two files diverge by a single
byte.

Two rules live here so that nothing else in the codebase has to remember them:

1. Money is :class:`decimal.Decimal`. Never ``float``. A float cannot represent
   ``0.01``, and a payment standard whose arithmetic is approximate is not a
   standard.

2. Every arithmetic operation runs inside an *explicitly pinned* decimal
   context. Python's ambient decimal context is mutable, thread-local, global
   state: any caller (or any imported library) can change its precision or
   rounding mode and silently change our answers. A result that depends on
   ambient state is not reproducible, and reproducibility is the only property
   this project exists to have.

The traps matter as much as the precision. ``InvalidOperation`` and
``DivisionByZero`` raise instead of quietly yielding ``NaN``/``Infinity``, so a
malformed input becomes a loud failure rather than a plausible-looking number.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import ROUND_HALF_EVEN, Context, Decimal, DivisionByZero, InvalidOperation
from decimal import localcontext as _localcontext

type Money = Decimal
"""A monetary amount, in US dollars. Always a ``Decimal``, never a ``float``."""

WORTH_CONTEXT: Context = Context(
    prec=28,
    rounding=ROUND_HALF_EVEN,
    traps=[InvalidOperation, DivisionByZero],
)
"""The one decimal context every WORTH computation runs under."""

CENTS: Decimal = Decimal("0.01")


@contextmanager
def money_context() -> Iterator[Context]:
    """Run a block under :data:`WORTH_CONTEXT`.

    ``localcontext`` takes a *copy* of the context it is given, so the pinned
    context object is never mutated by callers and the previous ambient context
    is restored on exit.
    """
    with _localcontext(WORTH_CONTEXT) as ctx:
        yield ctx


def to_cents(value: Decimal) -> Money:
    """Round ``value`` to whole cents under the pinned context.

    Rounding happens exactly once, on the final amount. Intermediate products
    keep full 28-digit precision; rounding each term as you go is how two
    implementations of the same formula end up a penny apart.
    """
    with money_context():
        return value.quantize(CENTS)


def usd(value: Money) -> str:
    """Format an amount for human display, e.g. ``$117.79``."""
    return f"${value:,.2f}"
