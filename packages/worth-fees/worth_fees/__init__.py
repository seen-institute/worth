"""worth-fees, reproducible Medicare Physician Fee Schedule allowed amounts.

The whole package in one call::

    >>> from worth_fees import PlaceOfService, expected_allowed
    >>> d = expected_allowed("99213", [], "CA18", PlaceOfService.NON_FACILITY, 2026, 1)
    >>> print(d.render())  # doctest: +SKIP

``d.amount`` is the number, ``d.trace`` is the arithmetic that produced it, and
``d.source`` is the sha256 of every file it came from.
"""

from worth_fees.models import (
    FeeDerivation,
    NotPayableError,
    PaymentBasis,
    PlaceOfService,
    SourceIntegrityError,
    UnknownCodeError,
    UnknownLocalityError,
    UnsupportedModifierError,
    VintageError,
    WorthFeesError,
)
from worth_fees.money import WORTH_CONTEXT, Money, money_context, to_cents
from worth_fees.provenance import SourceFile, Sources

from worth_fees.fees import expected_allowed  # isort: skip  (depends on the above)

__all__ = [
    "WORTH_CONTEXT",
    "FeeDerivation",
    "Money",
    "NotPayableError",
    "PaymentBasis",
    "PlaceOfService",
    "SourceFile",
    "SourceIntegrityError",
    "Sources",
    "UnknownCodeError",
    "UnknownLocalityError",
    "UnsupportedModifierError",
    "VintageError",
    "WorthFeesError",
    "expected_allowed",
    "money_context",
    "to_cents",
]

__version__ = "0.1.0"
