"""The Layer A scorer.

A pure function of a marker set, a rule pack and a provenance filter. No I/O,
no clock, no network, no randomness, no ambient state, which is the property
that lets a third party install the package, run it on the same inputs, and get
the same number in 2031.

The scorer is a *filter* over provenance rather than a separate implementation
per layer. Running it three times over one pipeline output, changing only the
filter, yields the Layer A score, the score with the ML lane admitted, and the
score with the generative lane admitted. That is what turns "where does the ML
classifier belong" from a matter of argument into a measurable delta.

Weights are renormalised over the admitted markers so that a filter that
excludes a lane still produces a 0 to 100 score on the same scale. Without that,
Layer A and Layer A + ML would live on different scales and the delta between
them would be meaningless.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from worth_complexity.models import (
    PROVENANCE_RANK,
    ComplexityScore,
    Encounter,
    Marker,
    MissingMarkerError,
    ScoredEncounter,
)
from worth_complexity.money import money_context

if TYPE_CHECKING:
    from worth_complexity.rulepack import RulePack


HUNDRED = Decimal(100)


def _resolve(
    markers: tuple[Marker, ...],
    provenance_filter: frozenset[str],
) -> tuple[dict[str, Marker], tuple[Marker, ...]]:
    """Pick one marker per id, by lane precedence, and report what was displaced.

    Two lanes can offer the same fact: OpTime carries incision-to-close, and the
    operative note usually states the operative time in a sentence. Both are
    extracted, because which sources a partner actually populates is not known
    in advance. Only the higher-precedence one is scored.

    Resolution is a total order, not a first-wins accident of iteration, so the
    same marker set always produces the same score regardless of the order the
    extractors happened to run in.
    """
    best: dict[str, Marker] = {}
    displaced: list[Marker] = []
    for marker in markers:
        if marker.provenance not in provenance_filter:
            continue
        held = best.get(marker.marker_id)
        if held is None:
            best[marker.marker_id] = marker
        elif PROVENANCE_RANK[marker.provenance] < PROVENANCE_RANK[held.provenance]:
            best[marker.marker_id] = marker
            displaced.append(held)
        else:
            displaced.append(marker)
    return best, tuple(displaced)


def score(
    encounter: Encounter,
    markers: tuple[Marker, ...],
    pack: RulePack,
    provenance_filter: frozenset[str],
) -> ScoredEncounter:
    """Score one encounter, returning the number and the arithmetic behind it."""
    rules = pack.for_provenance(provenance_filter)
    if not rules:
        msg = "rule pack admits no markers under this provenance filter"
        raise MissingMarkerError(msg)

    admitted, superseded = _resolve(markers, provenance_filter)
    missing = [r.marker_id for r in rules if r.marker_id not in admitted]
    if missing:
        msg = f"{encounter.encounter_id}: markers required by the rule pack are absent: {missing}"
        raise MissingMarkerError(msg)

    trace: list[str] = []
    with money_context():
        weight_total = sum((r.weight for r in rules), start=Decimal(0))
        running = Decimal(0)
        trace.append(f"{'marker':<32}{'value':>10}{'norm':>10}{'weight':>10}{'contrib':>11}")
        trace.append("-" * 73)
        for rule in rules:
            marker = admitted[rule.marker_id]
            norm = rule.normalise(marker.value)
            weight = rule.weight / weight_total
            contribution = norm * weight * HUNDRED
            running += contribution
            trace.append(
                f"{rule.marker_id:<32}{marker.value:>10}{norm:>10.4f}"
                f"{weight:>10.4f}{contribution:>11.4f}"
            )
        trace.append("-" * 73)
        trace.append(f"{'sum':<32}{'':>10}{'':>10}{'':>10}{running:>11.4f}")
        rounded = int(running.quantize(Decimal("1")))
        trace.append(f"rounded to the ordinal scale -> {rounded}")

    return ScoredEncounter(
        encounter=encounter,
        score=ComplexityScore(rounded),
        markers=tuple(admitted[r.marker_id] for r in rules),
        superseded=superseded,
        trace=tuple(trace),
        rule_pack_id=pack.rule_pack_id,
        rule_pack_version=pack.version,
        rule_pack_digest=pack.digest,
        provenance_filter=tuple(sorted(provenance_filter)),
    )
