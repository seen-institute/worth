"""Rule packs: the weights and anchors, as versioned data rather than code.

The methodology's promise is that a Layer A score is "a fixed, published
formula applied to facts that each trace to a structured field or an auditable
rule". A weight buried in a Python expression is not published in any useful
sense, a clinician advisor cannot read it and a referee cannot diff it
between versions. So the formula lives in JSON, ships inside the package, and
is content-addressed: every score records the sha256 of the exact pack that
produced it, forever.

Two invariants are enforced at load rather than trusted:

  * Weights sum to exactly one, in Decimal. A pack whose weights sum to 0.99
    produces scores that are quietly 1% low and comparable with nothing.
  * Anchors are ordered. A marker whose low anchor exceeds its high one would
    invert the scale for that marker alone, which is very hard to see in a
    finished number.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from worth_complexity.models import Provenance, RulePackError
from worth_complexity.money import money_context
from worth_complexity.provenance import sha256_bytes

_PACK_DIR = Path(__file__).parent / "rulepacks"
ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class Pattern:
    """One compiled expression and the value a match contributes."""

    pattern_id: str
    regex: re.Pattern[str]
    value: Decimal | None
    """``None`` for a numeric rule, whose value is capture group 1."""
    scale: Decimal = Decimal(1)
    """Multiplier applied to a captured number, e.g. hours to minutes."""


@dataclass(frozen=True, slots=True)
class MarkerRule:
    """How one marker is extracted, normalised, and how much it counts."""

    marker_id: str
    provenance: Provenance
    weight: Decimal
    anchor_low: Decimal
    anchor_high: Decimal
    note: str | None = None

    aggregate: str = ""
    """``count_distinct``, ``max_value`` or ``numeric``. Narrative rules only."""
    note_types: frozenset[str] = field(default_factory=frozenset)
    sections: frozenset[str] = field(default_factory=frozenset)
    """Sections the rule may read. Empty means every section not excluded."""
    excluded_sections: frozenset[str] = field(default_factory=frozenset)
    patterns: tuple[Pattern, ...] = ()

    @property
    def reads_narrative(self) -> bool:
        """True when this marker can be read out of a note.

        A marker whose ``provenance`` is ``rule`` has no other source. A marker
        whose provenance is ``structured`` may *also* carry patterns: the note
        usually restates the operative time and the blood loss, and extracting
        both means the pipeline still produces the marker at a partner whose
        extract omits the field. Where both arrive, precedence discards the
        narrative one, see ``PROVENANCE_RANK``.
        """
        return bool(self.patterns)

    def normalise(self, value: Decimal) -> Decimal:
        """Map a raw value onto 0 to 1 against this marker's fixed anchors.

        Values outside the anchors clamp. Clamping rather than extrapolating is
        deliberate: an eleven-hour case is not twice as complex as a
        five-and-a-half-hour one on an ordinal instrument, and letting a single
        outlier run the scale past 100 would put the whole cohort's ranking at
        the mercy of one record.
        """
        with money_context():
            span = self.anchor_high - self.anchor_low
            if span <= 0:
                msg = f"{self.marker_id}: anchor_high must exceed anchor_low"
                raise RulePackError(msg)
            t = (value - self.anchor_low) / span
            if t < 0:
                return Decimal(0)
            if t > ONE:
                return ONE
            return t


@dataclass(frozen=True, slots=True)
class RulePack:
    """A complete, content-addressed scoring formula."""

    rule_pack_id: str
    version: str
    status: str
    digest: str
    """sha256 of the pack file exactly as shipped."""
    markers: tuple[MarkerRule, ...]

    @property
    def is_provisional(self) -> bool:
        """True while the weights have not been set by clinical and statistical review."""
        return self.status != "ratified"

    def for_provenance(self, allowed: frozenset[str]) -> tuple[MarkerRule, ...]:
        """The subset of markers a given provenance filter admits."""
        return tuple(m for m in self.markers if m.provenance in allowed)


_AGGREGATES = frozenset({"count_distinct", "max_value", "numeric"})


def _pattern(rule_id: str, raw: dict[str, str]) -> Pattern:
    """Compile one pattern, failing at load rather than at extraction time."""
    try:
        regex = re.compile(raw["regex"], re.IGNORECASE)
    except re.error as exc:
        msg = f"{rule_id}/{raw.get('id', '?')}: {exc}"
        raise RulePackError(msg) from exc
    return Pattern(
        pattern_id=raw["id"],
        regex=regex,
        value=Decimal(raw["value"]) if "value" in raw else None,
        scale=Decimal(raw.get("scale", "1")),
    )


def load(name: str = "gyn-surgical-v1") -> RulePack:
    """Load a rule pack shipped with the package, hashing the bytes it came from."""
    path = _PACK_DIR / f"{name}.json"
    if not path.is_file():
        msg = f"no such rule pack: {name}"
        raise RulePackError(msg)
    raw = path.read_bytes()
    return loads(raw)


def loads(raw: bytes) -> RulePack:
    """Parse and validate rule pack bytes."""
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:  # pragma: no cover - malformed pack
        msg = f"rule pack is not valid JSON: {exc}"
        raise RulePackError(msg) from exc

    markers: list[MarkerRule] = []
    for entry in doc["markers"]:
        narrative = entry.get("narrative", {})
        rule = MarkerRule(
            marker_id=entry["marker_id"],
            provenance=entry["provenance"],
            weight=Decimal(entry["weight"]),
            anchor_low=Decimal(entry["anchor_low"]),
            anchor_high=Decimal(entry["anchor_high"]),
            note=entry.get("note"),
            aggregate=narrative.get("aggregate", ""),
            note_types=frozenset(narrative.get("note_types", ())),
            sections=frozenset(narrative.get("sections", ())),
            excluded_sections=frozenset(narrative.get("excluded_sections", ())),
            patterns=tuple(
                _pattern(rule_id=entry["marker_id"], raw=p) for p in narrative.get("patterns", ())
            ),
        )
        if rule.anchor_high <= rule.anchor_low:
            msg = f"{rule.marker_id}: anchor_high must exceed anchor_low"
            raise RulePackError(msg)
        if rule.provenance == "rule" and not rule.patterns:
            msg = (
                f"{rule.marker_id}: provenance 'rule' has no other source, so it needs "
                "a narrative block"
            )
            raise RulePackError(msg)
        if rule.reads_narrative:
            if rule.aggregate not in _AGGREGATES:
                msg = (
                    f"{rule.marker_id}: narrative rules need an aggregate, one of "
                    f"{sorted(_AGGREGATES)}"
                )
                raise RulePackError(msg)
            if not rule.patterns:
                msg = f"{rule.marker_id}: a narrative rule with no patterns reads nothing"
                raise RulePackError(msg)
            if not rule.note_types:
                msg = (
                    f"{rule.marker_id}: a narrative rule must name the note types it "
                    "reads. An operative finding does not mean the same thing in a "
                    "discharge summary."
                )
                raise RulePackError(msg)
        markers.append(rule)

    with money_context():
        total = sum((m.weight for m in markers), start=Decimal(0))
    if total != ONE:
        msg = f"rule pack weights must sum to exactly 1, got {total}"
        raise RulePackError(msg)

    return RulePack(
        rule_pack_id=doc["rule_pack_id"],
        version=str(doc["version"]),
        status=doc.get("status", "provisional"),
        digest=sha256_bytes(raw),
        markers=tuple(markers),
    )
