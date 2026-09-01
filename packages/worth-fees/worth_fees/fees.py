"""The one public calculation: a Medicare PFS allowed amount you can re-check.

Scope is deliberately narrow. This is the Medicare Physician Fee Schedule
formula and nothing else -- no OPPS, no ASC, no anesthesia, no clinical lab:

    (work_rvu x work_gpci + pe_rvu x pe_gpci + mp_rvu x mp_gpci) x CF

where ``pe_rvu`` is the facility or non-facility practice-expense RVU depending
on the place of service.

Everything the formula touches is a ``Decimal`` evaluated inside the pinned
context from :mod:`worth_fees.money`. Every intermediate product is recorded in
``FeeDerivation.trace`` at full precision, so the returned amount can be
re-derived by hand from the trace alone.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from functools import lru_cache

from worth_fees.models import (
    FeeDerivation,
    NotPayableError,
    PlaceOfService,
    UnknownCodeError,
    UnknownLocalityError,
    UnsupportedModifierError,
)
from worth_fees.money import money_context, to_cents, usd
from worth_fees.sources import (
    APPLY_WORK_GPCI_FLOOR,
    PAYMENT_BASIS_NOTE,
    FeeSchedule,
    GpciRow,
    RvuRow,
    load,
)

__all__ = ["expected_allowed"]

# Modifiers that select a *different RVU row* in the CMS file: the professional
# and technical components of a split-billable service.
_RVU_SELECTING = frozenset({"26", "TC"})

# Modifiers that do not change the base formula. They affect coverage, bundling
# or documentation, not the RVUs or the conversion factor. Grouped by what they
# mean, which is why the formatting is pinned.
# fmt: off
_INERT = frozenset({
    "24", "25", "57",              # separately identifiable / unrelated E/M
    "59", "XE", "XP", "XS", "XU",  # distinct procedural service
    "76", "77",                    # repeat procedure
    "95", "GT",                    # telehealth
    "GA", "GY", "GZ",              # advance beneficiary notice / coverage
    "LT", "RT",                    # anatomic side
})
# fmt: on

# Modifiers that scale the allowed amount. Modelling them is out of scope, so
# they are rejected: silently returning the unadjusted amount would be a wrong
# answer wearing the costume of a right one.
_PAYMENT_ADJUSTING = {
    "50": "bilateral procedure",
    "51": "multiple procedures",
    "52": "reduced services",
    "53": "discontinued procedure",
    "54": "surgical care only",
    "55": "postoperative management only",
    "56": "preoperative management only",
    "62": "two surgeons",
    "66": "surgical team",
    "78": "unplanned return to the operating room",
    "80": "assistant surgeon",
    "81": "minimum assistant surgeon",
    "82": "assistant surgeon (no qualified resident)",
    "AS": "physician assistant / NP as assistant at surgery",
}

# CMS status codes that carry no payable PFS amount.
_NON_PAYABLE_STATUS = {
    "B": "bundled into another service's payment",
    "C": "carrier-priced; no national RVUs",
    "E": "excluded from the physician fee schedule",
    "I": "not valid for Medicare purposes",
    "J": "anesthesia service, paid under the anesthesia formula (out of scope)",
    "M": "measurement code; not payable",
    "N": "non-covered service",
    "P": "bundled or excluded",
    "X": "statutory exclusion from the physician fee schedule",
}


@lru_cache(maxsize=8)
def _schedule(rule_year: int, quarter: int) -> FeeSchedule:
    return load(rule_year, quarter)


def _normalise_locality(locality: str) -> str:
    """``ca-18``, ``CA 18``, ``CA18`` all mean locality ``CA18``."""
    packed = "".join(ch for ch in locality.strip().upper() if ch.isalnum())
    state, digits = packed[:2], packed[2:]
    return f"{state}{digits.zfill(2)}" if digits else state


def _select_modifier(modifiers: Sequence[str]) -> str:
    """Reduce the supplied modifiers to the one that selects an RVU row."""
    cleaned = [m.strip().upper() for m in modifiers if m.strip()]
    for modifier in cleaned:
        if modifier in _PAYMENT_ADJUSTING:
            raise UnsupportedModifierError(
                f"modifier {modifier} ({_PAYMENT_ADJUSTING[modifier]}) scales the allowed "
                "amount. worth-fees models the base fee-schedule formula only, so it will "
                "not guess at the adjusted amount."
            )
        if modifier not in _RVU_SELECTING and modifier not in _INERT:
            raise UnsupportedModifierError(
                f"modifier {modifier} is not recognised. worth-fees accepts component "
                f"modifiers {sorted(_RVU_SELECTING)} and modifiers known not to change the "
                "base formula; anything else is rejected rather than ignored."
            )

    selecting = [m for m in cleaned if m in _RVU_SELECTING]
    if len(selecting) > 1:
        raise UnsupportedModifierError(
            f"modifiers {selecting} are mutually exclusive: a service is billed as the "
            "professional component or the technical component, not both."
        )
    return selecting[0] if selecting else ""


def _lookup_rvus(schedule: FeeSchedule, code: str, modifier: str) -> RvuRow:
    row = schedule.rvus.get((code, modifier))
    if row is not None:
        return row
    label = f"{code}-{modifier}" if modifier else code
    if any(existing == code for existing, _ in schedule.rvus):
        available = sorted(m for c, m in schedule.rvus if c == code)
        raise UnknownCodeError(
            f"{label} is not in the {schedule.vintage.label} RVU file; "
            f"{code} exists with modifiers {available!r}"
        )
    raise UnknownCodeError(
        f"{label} is not in the {schedule.vintage.label} fixture. The committed fixture "
        f"holds only a sample of codes; run `just refresh-fixture` to widen it."
    )


def _lookup_gpci(schedule: FeeSchedule, locality: str) -> GpciRow:
    row = schedule.gpcis.get(locality)
    if row is None:
        available = ", ".join(sorted(schedule.gpcis))
        raise UnknownLocalityError(
            f"locality {locality} is not in the {schedule.vintage.label} GPCI fixture; "
            f"available: {available}"
        )
    return row


def _practice_expense(row: RvuRow, place_of_service: PlaceOfService) -> Decimal:
    """Pick the facility or non-facility PE RVU, refusing NA-flagged settings.

    CMS repeats the other setting's value in an ``NA`` column, so reading the
    number without checking the indicator yields a plausible amount for a
    service that is not payable in that setting at all.
    """
    if place_of_service is PlaceOfService.FACILITY:
        if row.pe_rvu_facility_na:
            raise NotPayableError(
                f"{row.code} carries an NA facility practice-expense indicator: it is not "
                "paid in a facility setting."
            )
        return row.pe_rvu_facility
    if row.pe_rvu_nonfacility_na:
        raise NotPayableError(
            f"{row.code} carries an NA non-facility practice-expense indicator: it is not "
            "paid in a non-facility setting."
        )
    return row.pe_rvu_nonfacility


def expected_allowed(
    code: str,
    modifiers: Sequence[str],
    locality: str,
    place_of_service: PlaceOfService | str,
    rule_year: int,
    quarter: int,
) -> FeeDerivation:
    """Compute the Medicare PFS allowed amount, with its derivation and sources.

    Args:
        code: HCPCS/CPT code, e.g. ``"99213"``.
        modifiers: Modifiers as billed. ``26`` and ``TC`` select the
            professional or technical component; modifiers that scale payment
            are rejected.
        locality: Medicare locality as ``<state><number>``, e.g. ``"CA18"``.
        place_of_service: :class:`PlaceOfService`, or its string value.
        rule_year: Calendar year of the fee schedule, e.g. ``2026``.
        quarter: Quarterly release, ``1``-``4``.

    Returns:
        A :class:`FeeDerivation` carrying ``.amount``, ``.trace`` and ``.source``.

    Raises:
        VintageError: No pinned CMS vintage for that year and quarter.
        UnknownCodeError: The code (with that modifier) is not in the file.
        UnknownLocalityError: The locality is not in the GPCI file.
        NotPayableError: The code has no payable amount in that setting.
        UnsupportedModifierError: A modifier outside the modelled scope.
    """
    setting = PlaceOfService(place_of_service)
    schedule = _schedule(rule_year, quarter)
    normalised_code = code.strip().upper()
    modifier = _select_modifier(modifiers)

    row = _lookup_rvus(schedule, normalised_code, modifier)
    if row.status_code in _NON_PAYABLE_STATUS:
        raise NotPayableError(
            f"{normalised_code} has CMS status code {row.status_code}: "
            f"{_NON_PAYABLE_STATUS[row.status_code]}. There is no PFS allowed amount to report."
        )

    gpci = _lookup_gpci(schedule, _normalise_locality(locality))
    pe_rvu = _practice_expense(row, setting)
    conversion_factor = row.conversion_factor

    floor_note = "with 1.0 floor" if APPLY_WORK_GPCI_FLOOR else "without 1.0 floor"
    trace: list[str] = [
        f"fee schedule: CMS {schedule.vintage.label} (CY{rule_year} Q{quarter}), "
        f"{PAYMENT_BASIS_NOTE}",
        f"locality:     {gpci.key} {gpci.name} (MAC {gpci.mac})",
        f"setting:      {setting.value} (facility PE RVU {row.pe_rvu_facility}, "
        f"non-facility {row.pe_rvu_nonfacility}; using {pe_rvu})",
        f"status code:  {row.status_code} (payable)",
        "",
    ]

    with money_context():
        terms: list[tuple[str, Decimal, Decimal, str]] = [
            ("work", row.work_rvu, gpci.work_gpci, f"work GPCI ({floor_note})"),
            ("PE", pe_rvu, gpci.pe_gpci, "PE GPCI"),
            ("MP", row.mp_rvu, gpci.mp_gpci, "MP GPCI"),
        ]

        running = Decimal("0")
        steps: list[tuple[str, str, str]] = []
        for label, rvu, gpci_value, gpci_name in terms:
            product = rvu * gpci_value
            running += product
            steps.append(
                (
                    f"{label:<4} {rvu} RVU x {gpci_value} {gpci_name}",
                    f"{product}",
                    f"running total {running}",
                )
            )

        adjusted_total = running
        raw = adjusted_total * conversion_factor
        amount = to_cents(raw)

        steps += [
            ("", "", ""),
            ("adjusted RVU total", f"{adjusted_total}", ""),
            (f"x {conversion_factor} conversion factor", f"{raw}", ""),
            ("rounded to cents (ROUND_HALF_EVEN, prec=28)", usd(amount), ""),
        ]

    # Align the '=' column so the arithmetic reads as a column of sums.
    lhs_width = max(len(lhs) for lhs, _, _ in steps)
    value_width = max(len(value) for _, value, _ in steps)
    for lhs, value, suffix in steps:
        if not lhs:
            trace.append("")
            continue
        line = f"{lhs.ljust(lhs_width)}  =  {value.ljust(value_width)}"
        trace.append(f"{line}  {suffix}".rstrip())

    return FeeDerivation(
        code=normalised_code,
        modifier=modifier,
        modifiers=tuple(m.strip().upper() for m in modifiers if m.strip()),
        locality=gpci.key,
        locality_name=gpci.name,
        place_of_service=setting,
        rule_year=rule_year,
        quarter=quarter,
        work_rvu=row.work_rvu,
        pe_rvu=pe_rvu,
        mp_rvu=row.mp_rvu,
        work_gpci=gpci.work_gpci,
        pe_gpci=gpci.pe_gpci,
        mp_gpci=gpci.mp_gpci,
        conversion_factor=conversion_factor,
        adjusted_rvu_total=adjusted_total,
        amount=amount,
        trace=tuple(trace),
        source=schedule.sources,
    )
