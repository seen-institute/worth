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
    MarkerRow,
    MissingMarkerError,
    ScoredEncounter,
)
from worth_complexity.money import money_context

if TYPE_CHECKING:
    from worth_complexity.rulepack import MarkerRule, RulePack


HUNDRED = Decimal(100)

# Plausibility guards on a *structured* marker's raw value (decision 6,
# CONTRACT-SEEDS.md): a value outside these bounds is not evidence of
# anything, it is a data-quality failure, and is rejected -> treated as
# missing (defaulted to zero) exactly like an absent marker, with the reason
# recorded. Guards run only against ``provenance == "structured"`` markers;
# a narrative ("rule") marker's own extraction (``markers.py``) already
# decides what counts as a value at all.
_MINUTES_FLOOR = Decimal(0)
_OPERATIVE_MINUTES_CEILING = Decimal(1440)
"""Minutes in a day. An operative time longer than that is not a long case,
it is a timestamp error."""
_EBL_CEILING = Decimal(5000)
"""Millilitres. A number this large on a single case is a units error
(e.g. a lab value entered in the wrong field), not a massive hemorrhage."""
_EPISODE_READING_CEILING = Decimal(5000)
"""A monitoring episode's device-reading volume (``monitoring_intensity``,
sourced from a per-episode reading count) this high in a 30-day window is a
duplicate-row or export error, not real telemetry."""
_ASA_VALID: frozenset[Decimal] = frozenset(Decimal(i) for i in range(1, 7))
"""ASA physical status is classified 1 through 6; the surgical pack's own
anchors (1..4) bound where the score curve flattens, not what counts as a
plausible reading."""
_MINUTES_MARKER_SUBSTRING = "minutes"
"""Any marker id built on a minutes count (``operative_minutes``,
``total_documented_minutes``, ``coordination_minutes``, ``oversight_minutes``,
...) cannot be negative; caught generically rather than one id at a time so a
future pack's own ``*_minutes`` marker is guarded for free."""


def _guard(marker_id: str, value: Decimal) -> str | None:
    """A rejection reason for an implausible *structured* value, or ``None``.

    Every bound here is a physical-plausibility check, not a scoring
    judgement: the guard does not ask whether a value is a hard case, only
    whether it could be a real one. A value that fails is rejected -> missing
    (decision 6), never clamped and never scored.
    """
    if marker_id == "asa_class":
        if value not in _ASA_VALID:
            return f"asa_class {value} is not an ASA physical status class (1..6)"
        return None
    if marker_id == "operative_minutes":
        if value < _MINUTES_FLOOR or value > _OPERATIVE_MINUTES_CEILING:
            return (
                f"operative_minutes {value} is outside the plausible range "
                f"[{_MINUTES_FLOOR}, {_OPERATIVE_MINUTES_CEILING}]"
            )
        return None
    if marker_id == "estimated_blood_loss_ml":
        if value < _MINUTES_FLOOR or value > _EBL_CEILING:
            return (
                f"estimated_blood_loss_ml {value} is outside the plausible range "
                f"[{_MINUTES_FLOOR}, {_EBL_CEILING}]"
            )
        return None
    if marker_id == "monitoring_intensity" and value > _EPISODE_READING_CEILING:
        return (
            f"monitoring_intensity {value} implies an episode reading count "
            f"> {_EPISODE_READING_CEILING}"
        )
    if _MINUTES_MARKER_SUBSTRING in marker_id and value < _MINUTES_FLOOR:
        return f"{marker_id} {value} is negative"
    return None


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


def _missing_reason(
    rule: MarkerRule, admitted: dict[str, Marker]
) -> tuple[Marker | None, str | None]:
    """Whether ``rule``'s marker is usable: the admitted ``Marker`` and
    ``None`` when it is, or ``None`` and a reason when it is not.

    A structured marker that fails the plausibility guard (:func:`_guard`)
    is treated identically to one that was never extracted at all: both are
    "missing" for scoring purposes (decision 6), the only difference is the
    reason recorded.
    """
    marker = admitted.get(rule.marker_id)
    if marker is None:
        return None, "absent"
    if marker.provenance == "structured":
        reason = _guard(rule.marker_id, marker.value)
        if reason is not None:
            return None, f"rejected: {reason}"
    return marker, None


def score(
    encounter: Encounter,
    markers: tuple[Marker, ...],
    pack: RulePack,
    provenance_filter: frozenset[str],
) -> ScoredEncounter:
    """Score one encounter, returning the number and the arithmetic behind it.

    A marker the rule pack lists but this encounter's extract did not
    produce -- or produced with an implausible structured value -- no longer
    fails the whole encounter (decision 6, CONTRACT-SEEDS.md). It scores at
    zero, and its marker id is recorded on ``ScoredEncounter.missing`` and,
    with its reason, on ``ScoredEncounter.marker_rows`` and in the
    derivation ``trace``. :class:`~worth_complexity.models.MissingMarkerError`
    is now raised only when *every* structured marker the pack admits under
    this filter is missing or rejected: at that point nothing measurable
    about the encounter's complexity survived, and the encounter is
    unscorable rather than merely thin.
    """
    rules = pack.for_provenance(provenance_filter)
    if not rules:
        msg = "rule pack admits no markers under this provenance filter"
        raise MissingMarkerError(msg)

    admitted, superseded = _resolve(markers, provenance_filter)

    usable: dict[str, Marker] = {}
    missing: list[str] = []
    reasons: dict[str, str] = {}
    for rule in rules:
        marker, reason = _missing_reason(rule, admitted)
        if marker is not None:
            usable[rule.marker_id] = marker
        else:
            missing.append(rule.marker_id)
            assert reason is not None
            reasons[rule.marker_id] = reason

    structured_ids = {r.marker_id for r in rules if r.provenance == "structured"}
    missing_ids = set(missing)
    unscorable = (
        structured_ids <= missing_ids
        if structured_ids
        else missing_ids == {r.marker_id for r in rules}
    )
    if unscorable:
        msg = (
            f"{encounter.encounter_id}: every structured marker the rule pack "
            f"requires is absent or rejected: {sorted(missing_ids & structured_ids) or missing}"
        )
        raise MissingMarkerError(msg)

    trace: list[str] = []
    marker_rows: list[MarkerRow] = []
    with money_context():
        weight_total = sum((r.weight for r in rules), start=Decimal(0))
        running = Decimal(0)
        trace.append(
            f"{'marker':<32}{'value':>10}{'norm':>10}{'weight':>10}{'contrib':>11}  status"
        )
        trace.append("-" * 80)
        for rule in rules:
            weight = rule.weight / weight_total
            marker = usable.get(rule.marker_id)
            if marker is not None:
                norm = rule.normalise(marker.value)
                contribution = norm * weight * HUNDRED
                running += contribution
                trace.append(
                    f"{rule.marker_id:<32}{marker.value:>10}{norm:>10.4f}"
                    f"{weight:>10.4f}{contribution:>11.4f}"
                )
                marker_rows.append(
                    MarkerRow(
                        marker_id=rule.marker_id,
                        provenance=rule.provenance,
                        weight=weight,
                        value=marker.value,
                        contribution=contribution,
                        missing=False,
                        reason=None,
                    )
                )
            else:
                reason = reasons[rule.marker_id]
                contribution = Decimal(0)
                trace.append(
                    f"{rule.marker_id:<32}{'—':>10}{'—':>10}"
                    f"{weight:>10.4f}{contribution:>11.4f}  MISSING: {reason}"
                )
                marker_rows.append(
                    MarkerRow(
                        marker_id=rule.marker_id,
                        provenance=rule.provenance,
                        weight=weight,
                        value=None,
                        contribution=contribution,
                        missing=True,
                        reason=reason,
                    )
                )
        trace.append("-" * 80)
        trace.append(f"{'sum':<32}{'':>10}{'':>10}{'':>10}{running:>11.4f}")
        rounded = int(running.quantize(Decimal("1")))
        trace.append(f"rounded to the ordinal scale -> {rounded}")
        if missing:
            trace.append(f"missing (scored at zero): {', '.join(missing)}")

    return ScoredEncounter(
        encounter=encounter,
        score=ComplexityScore(rounded),
        markers=tuple(usable[r.marker_id] for r in rules if r.marker_id in usable),
        superseded=superseded,
        trace=tuple(trace),
        rule_pack_id=pack.rule_pack_id,
        rule_pack_version=pack.version,
        rule_pack_digest=pack.digest,
        provenance_filter=tuple(sorted(provenance_filter)),
        missing=tuple(missing),
        marker_rows=tuple(marker_rows),
    )
