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
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from worth_complexity.models import Provenance, RulePackError
from worth_complexity.money import money_context
from worth_complexity.provenance import sha256_bytes

if TYPE_CHECKING:
    from worth_complexity.method1 import WorkRule

_PACK_DIR = Path(__file__).parent / "rulepacks"
ONE = Decimal("1")

_EXTERNAL_RATIFIED_NOTE = (
    "external rule packs are capped at provisional; ratified packs ship with worth-complexity"
)


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
class ProcedureRule:
    """A separate-procedure statement Method 1 looks for in an operative note.

    Distinct from :class:`MarkerRule`: a marker contributes a weighted value to
    the Layer A score, while a procedure rule feeds the documented-vs-submitted
    cross-check instead, "was this step, named in the record, also named on the
    claim". Some steps have a billable code of their own that NCCI bundles into
    a bigger procedure done the same session, so documenting the step without
    the bundled code being present is not, by itself, evidence of anything;
    some steps have no code at all. ``candidate_codes`` and ``bundled_with``
    are what let ``method1.cross_check`` tell those cases apart without
    guessing at a coding rule the pack does not publish.
    """

    rule_id: str
    patterns: tuple[re.Pattern[str], ...]
    sections: frozenset[str]
    """Sections this rule may match in, e.g. ``findings``, ``description of
    procedure``. A statement whose section is not here is never attributed to
    this rule, the same discipline :class:`MarkerRule` applies to narrative."""
    candidate_codes: tuple[str, ...]
    """The code(s) this step could be billed under. Empty when none exists."""
    bundled_with: tuple[str, ...]
    """Codes that, if submitted, already cover this step under NCCI, so an
    absent candidate is a mismatch rather than a missed charge."""
    note: str
    """Why the bundling (or lack of a candidate) is what it is, in our own
    words: no CPT descriptor text, only the reasoning."""


@dataclass(frozen=True, slots=True)
class RulePack:
    """A complete, content-addressed scoring formula."""

    rule_pack_id: str
    version: str
    status: str
    digest: str
    """sha256 of the pack file exactly as shipped."""
    markers: tuple[MarkerRule, ...]
    procedures: tuple[ProcedureRule, ...] = ()
    """Method 1's separate-procedure patterns. Optional: a pack with no
    ``procedures`` block still scores Layer A, it just cannot cross-check
    documented steps against a claim."""
    work_rules: tuple[WorkRule, ...] = ()
    """Method 1's threshold rules over structured facts (decision 4,
    CONTRACT-PACKS.md): the visit and episode classes' equivalent of
    ``procedures`` above, evaluated by
    ``method1.evaluate_work_rules`` instead of ``method1.cross_check``.
    Optional, and empty on the surgical pack, which uses ``procedures``."""
    weights_version: str = ""
    """The version of the weights and anchors below, separate from the pack's
    own ``version`` so a pack can gain a new marker or procedure rule without
    the weights themselves having moved. Defaults to ``version`` (see
    :func:`loads`) for a pack that has not split the two yet."""
    encounter_class: str = "surgical"
    """The kind of encounter this pack scores. Decision 9: raw scores are
    never compared across classes; every population-level row carries this so
    a caller can enforce it."""
    source: Literal["packaged", "external"] = "packaged"
    """Where this pack was loaded from. Every run reports it (layer 2): a
    ``ratified`` pack may only ever have ``"packaged"`` here, enforced at
    load rather than trusted, see :func:`loads`."""
    source_path: str | None = None
    """Absolute path to the file, for an external pack. ``None`` for a
    packaged pack — it has no path meaningful outside this package."""
    filename: str = ""
    """The file name the pack was read from, e.g. ``surgical-v1.json``."""
    declared_status: str | None = None
    """The status the file itself declared, when that differs from
    :attr:`status` — currently only set when an external pack declared
    ``ratified`` and was capped to ``provisional`` on load."""
    status_note: str = ""
    """Set alongside :attr:`declared_status`: why ``status`` was overridden."""

    @property
    def is_provisional(self) -> bool:
        """True while the weights have not been set by clinical and statistical review."""
        return self.status != "ratified"

    @property
    def rulebook_version(self) -> str:
        """``"{rule_pack_id}@{version}"``, carried beside ``digest`` on every
        output (decision 5). Nothing pins the digest to this string; it is a
        human-readable version label, not a substitute for the content hash."""
        return f"{self.rule_pack_id}@{self.version}"

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


def _procedure_rule(raw: dict[str, Any]) -> ProcedureRule:
    """Compile one procedure rule, failing at load rather than at extraction time."""
    rule_id: str = raw["id"]
    try:
        patterns = tuple(re.compile(p, re.IGNORECASE) for p in raw.get("patterns", ()))
    except re.error as exc:
        msg = f"{rule_id}: {exc}"
        raise RulePackError(msg) from exc
    return ProcedureRule(
        rule_id=rule_id,
        patterns=patterns,
        sections=frozenset(raw.get("sections", ())),
        candidate_codes=tuple(raw.get("candidate_codes", ())),
        bundled_with=tuple(raw.get("bundled_with", ())),
        note=str(raw.get("note", "")),
    )


_BUCKETS = frozenset({"missed", "mismatched", "no_code"})
_WHEN_KEYS = frozenset({"fact", "min", "max", "equals", "flag", "all_of", "any_of"})


def _validate_when(rule_id: str, when: dict[str, Any], *, depth: int = 0) -> None:
    """Fail at load rather than at evaluation time: an unknown key, an
    ``all_of``/``any_of`` nested more than one level, or a clause naming
    neither a comparison nor a nesting operator."""
    unknown = set(when) - _WHEN_KEYS
    if unknown:
        msg = f"{rule_id}: 'when' has unknown key(s) {sorted(unknown)}"
        raise RulePackError(msg)
    for key in ("all_of", "any_of"):
        if key in when:
            if depth:
                msg = f"{rule_id}: '{key}' nests more than one level deep"
                raise RulePackError(msg)
            clauses = when[key]
            if not isinstance(clauses, list) or not clauses:
                msg = f"{rule_id}: '{key}' must be a non-empty list of clauses"
                raise RulePackError(msg)
            for clause in clauses:
                _validate_when(rule_id, clause, depth=depth + 1)
            return
    if "fact" not in when:
        msg = f"{rule_id}: 'when' clause names no fact and no all_of/any_of"
        raise RulePackError(msg)
    comparisons = {"min", "max", "equals", "flag"}
    if not comparisons & set(when):
        msg = f"{rule_id}: 'when' clause has none of min/max/equals/flag"
        raise RulePackError(msg)


def _work_rule(raw: dict[str, Any]) -> WorkRule:
    """Compile one work rule, failing at load rather than at evaluation time."""
    from worth_complexity.method1 import (
        STRENGTH_RANK,  # deferred: method1 imports this module
        WorkRule,
    )

    rule_id: str = raw["id"]
    when = raw["when"]
    if not isinstance(when, dict):
        msg = f"{rule_id}: 'when' must be an object"
        raise RulePackError(msg)
    _validate_when(rule_id, when)

    bucket_if_absent = str(raw["bucket_if_absent"])
    if bucket_if_absent not in _BUCKETS:
        msg = f"{rule_id}: bucket_if_absent must be one of {sorted(_BUCKETS)}"
        raise RulePackError(msg)
    bucket_if_present = raw.get("bucket_if_present")
    if bucket_if_present is not None and bucket_if_present not in _BUCKETS:
        msg = f"{rule_id}: bucket_if_present must be one of {sorted(_BUCKETS)} or null"
        raise RulePackError(msg)
    evidence_strength = str(raw.get("evidence_strength", "moderate"))
    if evidence_strength not in STRENGTH_RANK:
        msg = f"{rule_id}: evidence_strength must be one of {sorted(STRENGTH_RANK)}"
        raise RulePackError(msg)

    return WorkRule(
        rule_id=rule_id,
        when=when,
        billed_any_of=tuple(raw.get("billed_any_of", ())),
        candidate_codes=tuple(raw.get("candidate_codes", ())),
        replaces=raw.get("replaces"),
        bucket_if_absent=bucket_if_absent,  # type: ignore[arg-type]
        bucket_if_present=bucket_if_present,
        statement=str(raw["statement"]),
        section=str(raw["section"]),
        evidence_strength=evidence_strength,
        note=str(raw.get("note", "")),
    )


def _rulepack_dirs() -> tuple[Path, ...]:
    """Directories named by ``WORTH_RULEPACK_DIR``, read fresh at call time.

    ``os.pathsep``-separated, the same convention as ``PATH``, so a
    deployment can point at more than one candidate directory.
    """
    raw = os.environ.get("WORTH_RULEPACK_DIR", "")
    return tuple(Path(p) for p in raw.split(os.pathsep) if p)


def _filename_for(name: str) -> str:
    return name if name.endswith(".json") else f"{name}.json"


def load(name: str = "surgical-v1", *, search: Sequence[Path] | None = None) -> RulePack:
    """Load a rule pack by name, resolving where it comes from (layer 2).

    Resolution order: the directories in ``search``, in order, then the
    directories named by ``WORTH_RULEPACK_DIR`` (``os.pathsep``-separated,
    read at call time), then the packaged ``rulepacks/``. ``name`` may be
    given with or without ``.json``.

    First match wins, with one exception: a packaged pack of the same name
    is never silently shadowed by an external one. That combination is a
    :class:`RulePackError` naming both paths, because an external pack that
    happens to share a packaged pack's file name is far more likely to be a
    mistake than a deliberate override, and a load that silently picked one
    of two same-named files is not auditable. An external pack that wants to
    stand in for a candidate must use a distinct file name.
    """
    filename = _filename_for(name)
    external_dirs = (*(search or ()), *_rulepack_dirs())

    external_path: Path | None = None
    for directory in external_dirs:
        candidate = directory / filename
        if candidate.is_file():
            external_path = candidate
            break

    packaged_path = _PACK_DIR / filename
    packaged_exists = packaged_path.is_file()

    if external_path is not None and packaged_exists:
        msg = (
            f"rule pack {filename!r} exists both packaged ({packaged_path}) and external "
            f"({external_path}); an external rule pack must use a distinct file name, not "
            "silently shadow a packaged one"
        )
        raise RulePackError(msg)

    if external_path is not None:
        return load_path(external_path)

    if packaged_exists:
        raw = packaged_path.read_bytes()
        return loads(raw, source="packaged", source_path=None, filename=filename)

    msg = f"no such rule pack: {name}"
    raise RulePackError(msg)


def load_path(path: Path) -> RulePack:
    """Load one file directly as an external pack, whatever its name."""
    if not path.is_file():
        msg = f"no such rule pack file: {path}"
        raise RulePackError(msg)
    raw = path.read_bytes()
    return loads(raw, source="external", source_path=str(path.resolve()), filename=path.name)


def loads(
    raw: bytes,
    *,
    source: Literal["packaged", "external"] = "packaged",
    source_path: str | None = None,
    filename: str = "",
) -> RulePack:
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

    procedures = tuple(_procedure_rule(p) for p in doc.get("procedures", ()))
    work_rules = tuple(_work_rule(w) for w in doc.get("work_rules", ()))
    version = str(doc["version"])

    declared_status = str(doc.get("status", "provisional"))
    status = declared_status
    status_note = ""
    recorded_declared_status: str | None = None
    if source == "external" and declared_status == "ratified":
        recorded_declared_status = declared_status
        status = "provisional"
        status_note = _EXTERNAL_RATIFIED_NOTE

    return RulePack(
        rule_pack_id=doc["rule_pack_id"],
        version=version,
        status=status,
        digest=sha256_bytes(raw),
        markers=tuple(markers),
        procedures=procedures,
        work_rules=work_rules,
        weights_version=str(doc.get("weights_version", version)),
        encounter_class=doc.get("encounter_class", "surgical"),
        source=source,
        source_path=source_path,
        filename=filename,
        declared_status=recorded_declared_status,
        status_note=status_note,
    )


@dataclass(frozen=True, slots=True)
class PackInfo:
    """One rule pack :func:`available` found, without loading it into a run.

    A malformed external file never raises out of :func:`available`: it
    comes back with ``status="invalid"`` and the error in ``note``, because a
    listing exists precisely so a caller can see what is on disk, including
    the file that does not parse.
    """

    rule_pack_id: str
    version: str
    weights_version: str
    encounter_class: str
    status: str
    digest: str
    source: Literal["packaged", "external"]
    filename: str
    path: str | None
    """Absolute path for an external pack, ``None`` for a packaged one."""
    name: str
    """The loadable name: the file's stem, suitable for :func:`load`."""
    note: str | None = None
    """The load error, when ``status == "invalid"``."""


def _pack_info(path: Path, *, source: Literal["packaged", "external"]) -> PackInfo:
    try:
        raw = path.read_bytes()
        pack = loads(
            raw,
            source=source,
            source_path=str(path.resolve()) if source == "external" else None,
            filename=path.name,
        )
    except (RulePackError, KeyError, ValueError, TypeError, OSError) as exc:
        return PackInfo(
            rule_pack_id="",
            version="",
            weights_version="",
            encounter_class="",
            status="invalid",
            digest="",
            source=source,
            filename=path.name,
            path=str(path.resolve()) if source == "external" else None,
            name=path.stem,
            note=str(exc),
        )
    return PackInfo(
        rule_pack_id=pack.rule_pack_id,
        version=pack.version,
        weights_version=pack.weights_version,
        encounter_class=pack.encounter_class,
        status=pack.status,
        digest=pack.digest,
        source=pack.source,
        filename=pack.filename,
        path=pack.source_path,
        name=path.stem,
    )


def available(*, search: Sequence[Path] | None = None) -> tuple[PackInfo, ...]:
    """Every rule pack layer 2 can see: packaged first, then external.

    Deterministic order (packaged sorted by file name, then each external
    directory in resolution order, sorted by file name within it) so two
    calls in the same environment always list packs the same way. A
    malformed external file yields an ``invalid`` :class:`PackInfo` rather
    than raising, so one bad file never hides the rest of the listing.
    """
    infos: list[PackInfo] = [
        _pack_info(p, source="packaged") for p in sorted(_PACK_DIR.glob("*.json"))
    ]

    for directory in (*(search or ()), *_rulepack_dirs()):
        if not directory.is_dir():
            continue
        infos.extend(_pack_info(p, source="external") for p in sorted(directory.glob("*.json")))

    return tuple(infos)
