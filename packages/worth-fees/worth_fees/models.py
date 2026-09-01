"""Public value types and errors for worth-fees."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from worth_fees.money import usd

if TYPE_CHECKING:
    from worth_fees.provenance import Sources


_CPT_I = re.compile(r"\d{5}")
_CPT_II = re.compile(r"\d{4}F")
_CPT_III = re.compile(r"\d{4}T")
_HCPCS_II = re.compile(r"[A-Z]\d{4}")


class WorthFeesError(Exception):
    """Base class for every error this package raises."""


class UnknownCodeError(WorthFeesError):
    """The HCPCS/CPT code (with the given modifier) is not in the RVU file."""


class UnknownLocalityError(WorthFeesError):
    """The locality is not in the GPCI file."""


class NotPayableError(WorthFeesError):
    """The code exists but carries no payable RVUs in the requested setting.

    Raised for non-payable status codes (bundled, excluded, carrier-priced) and
    for the ``NA`` practice-expense indicator, which marks a code as not paid in
    that place of service. Returning a number in these cases would be worse
    than failing: it would look like an answer.
    """


class UnsupportedModifierError(WorthFeesError):
    """A modifier was supplied that changes payment in a way this scope omits.

    worth-fees currently models the base fee-schedule formula only. Modifiers
    that scale the allowed amount (bilateral, multiple-procedure, assistant-at-
    surgery, and so on) are rejected rather than ignored.
    """


class VintageError(WorthFeesError):
    """No pinned CMS vintage exists for the requested rule year and quarter."""


class SourceIntegrityError(WorthFeesError):
    """A source file's sha256 did not match the pinned value."""


class CodeSystem(StrEnum):
    """Which code system owns a HCPCS code, and therefore who claims copyright.

    CPT (Categories I, II and III) is AMA-copyrighted. HCPCS Level II codes are
    created by CMS and are public domain. Publication decisions differ between
    the two, so the distinction is carried explicitly rather than re-derived by
    whoever consumes the data.
    """

    CPT_I = "cpt-i"
    """Category I: five digits, e.g. 99213. AMA."""
    CPT_II = "cpt-ii"
    """Category II performance measures: four digits then F, e.g. 0001F. AMA."""
    CPT_III = "cpt-iii"
    """Category III emerging technology: four digits then T, e.g. 0042T. AMA."""
    HCPCS_II = "hcpcs-ii"
    """Level II: a letter then four digits, e.g. G0008. CMS, public domain."""
    UNKNOWN = "unknown"
    """Placeholders and anything unrecognised, e.g. the GXXX1 stub in RVU26A."""

    @classmethod
    def classify(cls, code: str) -> CodeSystem:
        """Classify a HCPCS/CPT code by its shape."""
        value = code.strip().upper()
        if _CPT_I.fullmatch(value):
            return cls.CPT_I
        if _CPT_II.fullmatch(value):
            return cls.CPT_II
        if _CPT_III.fullmatch(value):
            return cls.CPT_III
        if _HCPCS_II.fullmatch(value):
            return cls.HCPCS_II
        return cls.UNKNOWN

    @property
    def is_ama_copyrighted(self) -> bool:
        """True for CPT Categories I-III; False for HCPCS Level II."""
        return self in {CodeSystem.CPT_I, CodeSystem.CPT_II, CodeSystem.CPT_III}


class PlaceOfService(StrEnum):
    """Which practice-expense RVU applies.

    Medicare pays a lower practice-expense RVU when the physician works in a
    facility, because the facility is separately paid for the overhead.
    """

    NON_FACILITY = "non-facility"
    FACILITY = "facility"


@dataclass(frozen=True, slots=True)
class FeeDerivation:
    """A Medicare allowed amount together with everything needed to re-check it.

    ``amount`` is the number. ``trace`` is the arithmetic that produced it, and
    ``source`` is the provenance of every input. The three together are the
    unit of work this project trades in: a referee who distrusts us should be
    able to re-derive ``amount`` from ``trace`` by hand, and re-obtain the
    inputs from ``source`` without asking us for anything.
    """

    code: str
    modifier: str
    """The RVU-selecting modifier actually applied (``""``, ``26``, or ``TC``)."""
    modifiers: tuple[str, ...]
    """Every modifier supplied by the caller, as given."""
    locality: str
    locality_name: str
    place_of_service: PlaceOfService
    rule_year: int
    quarter: int

    work_rvu: Decimal
    pe_rvu: Decimal
    mp_rvu: Decimal
    work_gpci: Decimal
    pe_gpci: Decimal
    mp_gpci: Decimal
    conversion_factor: Decimal

    adjusted_rvu_total: Decimal
    """The parenthesised sum, unrounded, at full context precision."""

    amount: Decimal
    trace: tuple[str, ...]
    source: Sources

    def render(self) -> str:
        """The whole derivation as printable text."""
        lines = [
            f"{self.code}"
            + (f"-{self.modifier}" if self.modifier else "")
            + f"   locality {self.locality} ({self.locality_name})",
            f"{self.place_of_service.value}   CY{self.rule_year} Q{self.quarter}",
            "",
            "Derivation",
            "----------",
        ]
        lines.extend(f"  {step}".rstrip() for step in self.trace)
        lines += ["", "Source", "------"]
        lines.extend(self.source.render("  "))
        lines += ["", f"Allowed amount: {usd(self.amount)}"]
        return "\n".join(lines)
