"""worth-complexity. Layer A complexity scoring and the payment adequacy ratio.

The whole package in one call::

    >>> from pathlib import Path
    >>> from worth_complexity import run
    >>> r = run(Path("clinical"), Path("remittance"), locality="NY01")  # doctest: +SKIP
    >>> r.records[0].adequacy  # doctest: +SKIP
    Ratio(Decimal('0.7043'))

``run`` reads a partner extract and returns a scored, linked cohort: a Layer A
complexity score per encounter with the arithmetic behind it, a Method 0 slope
per code and payer, the fee schedule's complexity relation fitted on the
comparator cohort, each payer's multiple of the schedule, and a payment
adequacy ratio per encounter.

Nothing here reaches the network, reads a clock, or imports a cloud SDK.
"""

from worth_complexity.adequacy import (
    Multiplier,
    Observation,
    PricedEncounter,
    PricingError,
    adequacy,
    method_zero_slopes,
    payer_multipliers,
    price_comparators,
    schedule_curve,
)
from worth_complexity.curve import Fit, fit
from worth_complexity.linkage import Linkage, LinkedEncounter, link
from worth_complexity.models import (
    LAYER_A,
    LAYER_A_STRUCTURED_ONLY,
    SUPPRESSION_THRESHOLD,
    Adequacy,
    Cohort,
    ComplexityScore,
    Encounter,
    ExtrapolationError,
    Marker,
    MethodologyViolation,
    MissingMarkerError,
    NoReferenceCurveError,
    Ratio,
    RemitLine,
    RulePackError,
    ScoredEncounter,
    SourceRef,
    UnlinkedEncounterError,
    WorthComplexityError,
)
from worth_complexity.money import WORTH_CONTEXT, Money, money_context, to_cents
from worth_complexity.pipeline import Run, run
from worth_complexity.rulepack import MarkerRule, RulePack
from worth_complexity.sampling import Distribution, Interval
from worth_complexity.scoring import score
from worth_complexity.version import __version__ as __version__

__all__ = [
    "LAYER_A",
    "LAYER_A_STRUCTURED_ONLY",
    "SUPPRESSION_THRESHOLD",
    "WORTH_CONTEXT",
    "Adequacy",
    "Cohort",
    "ComplexityScore",
    "Distribution",
    "Encounter",
    "ExtrapolationError",
    "Fit",
    "IndexRecord",
    "Interval",
    "Linkage",
    "LinkedEncounter",
    "Marker",
    "MarkerRule",
    "MethodologyViolation",
    "MissingMarkerError",
    "Money",
    "Multiplier",
    "NoReferenceCurveError",
    "Observation",
    "PricedEncounter",
    "PricingError",
    "Ratio",
    "RemitLine",
    "RulePack",
    "RulePackError",
    "Run",
    "ScoredEncounter",
    "SourceRef",
    "Stratum",
    "UnlinkedEncounterError",
    "WorthComplexityError",
    "adequacy",
    "fit",
    "link",
    "method_zero_slopes",
    "money_context",
    "payer_multipliers",
    "price_comparators",
    "run",
    "schedule_curve",
    "score",
    "to_cents",
]
