"""The generic study-payment engine every class's scenario ``build()`` uses
for :data:`worth_complexity.synthetic.scenarios.Scenario.payment_rule`.

Every class's baseline generator already fits one schedule curve on the
comparator cohort's (Layer A score, reference-release PFS) pairs and prices
the study cohort as some function of it -- the surgical and episode
generators literally (``curve.predict(mean or median score) * FACTOR``); the
visit generator implicitly, because a flat billed code makes the same
relationship trivially true. :func:`price_study` is that function, made
explicit and made to support every payment rule the catalog names instead
of just the one ``flat-at-median-share`` (baseline) fixes.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from worth_fees.sources import Vintage

    from worth_complexity.curve import Fit

CENTS = Decimal("0.01")

#: The newest CMS vintage this package has pinned (``worth_fees.sources.
#: PINNED_VINTAGES``). Used only as :func:`vintage_for_date_clamped`'s
#: fallback -- see that function.
_NEWEST_PINNED_VINTAGE = (2026, 4)

FEE_SCHEDULE_STEP = Decimal("1.04")
"""The ``policy-change`` scenario's surgical/episode fee-schedule step
(CONTRACT-SEEDS.md's catalog: "payer multiples unchanged, PFS x1.04
after") -- a flat multiple every generator applies to its own priced base
amount for a service date on or after ``Scenario.policy_date``, comparator
and study alike. Not a real CMS conversion-factor change (no second vintage
is pinned to derive it from); a deliberately planted step for the Over-time
output to read back."""


def vintage_for_date_clamped(service_date: date) -> Vintage:
    """``worth_fees.sources.vintage_for_date``, clamped to the newest pinned
    vintage for a service date it does not cover.

    The ``policy-change`` scenario's service window runs through CY2027
    (CONTRACT-SEEDS.md's catalog), and no CY2027 CMS archive is pinned yet
    (``worth_fees.sources.PINNED_VINTAGES`` stops at CY2026 Q4) --
    ``vintage_for_date`` raises ``VintageError`` for any date past CY2026.
    Rather than let the generator crash on its own planted scenario, a 2027
    (or later) service date is priced at the 2026 Q4 vintage instead: the
    newest real schedule this package carries, applied forward -- the same
    thing a real biller does the day CMS's next release is late. This is a
    generator-side clamp only; ``worth_fees.sources.vintage_for_date`` itself
    is untouched and still raises for a real, non-synthetic caller.
    """
    from worth_fees.models import VintageError
    from worth_fees.sources import vintage_for, vintage_for_date

    try:
        return vintage_for_date(service_date)
    except VintageError:
        return vintage_for(*_NEWEST_PINNED_VINTAGE)


PAYMENT_RULES = (
    "flat-at-median-share",
    "on-curve",
    "at-median",
    "curve-share",
    "overpaid-at-median",
)


def price_study(
    rule: str,
    share: Decimal,
    *,
    own_score: Decimal,
    mean_code_score: Decimal,
    median_study_score: Decimal,
    curve: Fit,
) -> Decimal:
    """The pre-multiplier, pre-noise base amount for one study encounter.

    * ``flat-at-median-share`` (baseline) and ``overpaid-at-median``: every
      case billing the same code is paid the same amount --
      ``curve(mean score for that code) * share`` -- flat within a code,
      the phenomenon both scenarios demonstrate (baseline below the curve,
      overpaid above it).
    * ``on-curve`` (parity): ``curve(this case's own score)``, ``share``
      ignored -- payment tracks complexity directly.
    * ``curve-share`` (center-mispriced): ``curve(this case's own score) *
      share`` -- tracks complexity, uniformly discounted.
    * ``at-median`` (compression): every study case, regardless of code, is
      paid ``curve(the whole study cohort's median score)`` -- capped at
      what the middle case is worth, ``share`` ignored.
    """
    if rule == "on-curve":
        base = curve.predict(own_score, allow_extrapolation=True)
    elif rule == "curve-share":
        base = curve.predict(own_score, allow_extrapolation=True) * share
    elif rule == "at-median":
        base = curve.predict(median_study_score, allow_extrapolation=True)
    elif rule in ("flat-at-median-share", "overpaid-at-median"):
        base = curve.predict(mean_code_score, allow_extrapolation=True) * share
    else:
        msg = f"unknown payment rule: {rule!r} (want one of {PAYMENT_RULES})"
        raise ValueError(msg)
    return base.quantize(CENTS)


#: The noise every generator's ``assign_payments`` multiplies the priced
#: base amount by. One shared constant, not read from the scenario, because
#: it is not a knob the catalog varies -- it is what "a little noise" means
#: everywhere it is mentioned.
NOISE_RANGE = (0.985, 1.015)


def ratio_band(
    rule: str, share: Decimal, *, noise: tuple[float, float] = NOISE_RANGE
) -> list[float]:
    """The adequacy-ratio band :func:`price_study` plants, evaluated at the
    reference point each rule holds payment to a fixed relationship at:

    * ``on-curve``: ratio 1.0 everywhere (own score), +/- noise only.
    * ``curve-share`` / ``flat-at-median-share`` / ``overpaid-at-median``:
      ratio ``share`` at the rule's own reference score (a code's mean, for
      the flat rules; every score, for ``curve-share``), +/- noise.
    * ``at-median``: ratio 1.0 *at the median score only* -- away from it
      the ratio moves with the gap between the case's own score and the
      median, which is not a fixed band; :func:`worth_complexity.synthetic.
      scenarios.SCENARIOS`'s ``compression`` notes documents the shape
      instead of overstating precision here.

    Bands are derived from ``rule``/``share`` alone, the same formula for
    every encounter class -- never hand-typed per class."""
    lo_n, hi_n = noise
    reference = 1.0 if rule in ("on-curve", "at-median") else float(share)
    return [round(reference * lo_n, 4), round(reference * hi_n, 4)]
