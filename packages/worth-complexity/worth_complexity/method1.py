"""Method 1: documented-vs-submitted cross-check.

The methodology's second lever is not "how hard was the case" but "was the
work the surgeon documented actually captured on the claim". A separate
procedure like adhesiolysis can be genuinely done, genuinely worth doing, and
genuinely absent from the bill: bundled under NCCI into whatever else was done
the same session, never coded because no clean code exists for it, or coded
but on a different line than the note implies. None of that is visible from
the complexity score alone, which reads the note but never looks at the 837.

This module is the two-step that closes that gap. ``statements`` reads an
operative note for sentences that name a separate procedure, the same
section-scoped, negation-aware reading ``notes.py`` already does for markers,
so that "no adhesiolysis was required" is refused for the same reason a
marker rule would refuse it. ``cross_check`` then asks, for each statement,
whether a billable code for that step exists at all, and if it does, whether
it appears on the claim, or is properly bundled into something that does.

Three buckets come out of that question, and they mean different things to a
biller: ``missed`` is a code that should have been submitted and was not,
real recoverable revenue. ``mismatched`` is documented work whose code is
correctly absent because NCCI already bundles it into another submitted code,
not recoverable, but still worth a biller seeing so a modifier or a coding
review is not chased for nothing. ``no_code`` is work with no billing vehicle
at all, which pricing cannot touch and which the fee schedule itself has
nothing to say about.

Deliberately not done here: pricing. ``Method1Flag.priced`` is left ``None``
and ``pricing_trace`` empty by every function in this module; putting a
dollar figure on a flag needs the fee schedule and the payer multiplier, which
live in ``worth_fees`` and ``adequacy.py``, so that step is left to the
pipeline that already has both. ``price_flag`` exists only to attach that
number once it is known, not to compute it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from functools import lru_cache
from typing import TYPE_CHECKING, Literal

from worth_complexity import rulepack
from worth_complexity.models import SourceRef, WorthComplexityError
from worth_complexity.money import money_context
from worth_complexity.notes import Match, Note, _modified, find, sections, source_ref

if TYPE_CHECKING:
    from worth_complexity.claims import Claim
    from worth_complexity.models import Encounter
    from worth_complexity.rulepack import ProcedureRule

__all__ = [
    "STRENGTH_RANK",
    "Bucket",
    "BucketTotal",
    "Method1Error",
    "Method1Flag",
    "Method1Summary",
    "ProcedureStatement",
    "Strength",
    "WorkRule",
    "cross_check",
    "evaluate_work_rules",
    "price_flag",
    "statements",
    "summarize",
]

Bucket = Literal["missed", "mismatched", "no_code"]
Strength = Literal["strong", "moderate", "weak"]

STRENGTH_RANK: dict[Strength, int] = {"weak": 1, "moderate": 2, "strong": 3}
"""Ordinal ranking of :data:`Strength`, so callers can sort or threshold on it
without hard-coding the three string values."""

_BUCKETS: tuple[Bucket, ...] = ("missed", "mismatched", "no_code")

_PROCEDURE_PERFORMED = "procedure performed"
_NARRATIVE_SECTIONS = frozenset({"findings", "description of procedure"})

_PERFORMED_VERB = re.compile(
    r"\b(?:was performed|performed|was undertaken|carried out)\b", re.IGNORECASE
)


class Method1Error(WorthComplexityError):
    """A statement or a rule pack could not be cross-checked."""


@dataclass(frozen=True, slots=True)
class ProcedureStatement:
    """One sentence, or one PROCEDURE PERFORMED line, naming a step done."""

    encounter_id: str
    text: str
    """The sentence or the PROCEDURE PERFORMED line, verbatim."""
    section: str
    """``"procedure performed"`` | ``"findings"`` | ``"description of procedure"``."""
    source_ref: SourceRef


@dataclass(frozen=True, slots=True)
class BucketTotal:
    count: int
    dollars: Decimal | None
    priced_count: int


@dataclass(frozen=True, slots=True)
class Method1Summary:
    n_flags: int
    by_bucket: dict[str, BucketTotal]
    """Keyed ``"missed"``, ``"mismatched"``, ``"no_code"``."""


@dataclass(frozen=True, slots=True)
class Method1Flag:
    """One separate-procedure statement, checked against the claim."""

    encounter_id: str
    statement: str
    section: str
    rule_id: str
    """The :class:`~worth_complexity.rulepack.ProcedureRule` that matched."""
    candidate_codes: tuple[str, ...]
    """The code(s) this step could be billed under. Empty for ``no_code``."""
    submitted_codes: tuple[str, ...]
    """The codes the claim actually carries (or the OR log panel, absent a claim)."""
    bucket: Bucket
    reason: str
    vehicle_exists: bool
    """``candidate_codes`` non-empty: whether a code exists to check against at all."""
    evidence_strength: Strength
    priced: Decimal | None
    """``None`` until :func:`price_flag` attaches a fee-schedule amount."""
    pricing_trace: tuple[str, ...]
    source_ref: SourceRef
    replaces: str | None = None
    """The billed code this flag's candidate would supersede (decision 4,
    CONTRACT-PACKS.md), e.g. the next E/M level by time replacing the level
    actually billed. ``None`` for a flag that prices at the candidate's own
    fee-schedule amount rather than a difference — every surgical-pack flag,
    and any work-rule flag whose rule does not name a ``replaces`` code.
    ``pipeline._price_method1_flag`` reads this to price the difference
    between the candidate and the replaced code, floored at 0, instead of
    the candidate's amount outright."""


@dataclass(frozen=True, slots=True)
class WorkRule:
    """One threshold rule over structured facts.

    The visit and episode classes' Method 1 is not a note-phrase cross-check
    the way the surgical pack's ``procedures`` block is: there is no separate
    procedure to name, only a structured fact, "documented 42 minutes", that
    either does or does not cross a billing threshold. A ``WorkRule`` is that
    threshold, declared as data in a pack's ``work_rules`` block (parsed by
    ``rulepack.py``) and evaluated by :func:`evaluate_work_rules`, which
    produces the same :class:`Method1Flag` the surgical cross-check does so
    every downstream consumer, pricing, the signature, the population card,
    the work queue, stays common to all three classes (decision 4,
    CONTRACT-PACKS.md).
    """

    rule_id: str
    when: Mapping[str, object]
    """A condition over ``facts``: ``{"fact": "...", "min"|"max"|"equals"|"flag": ...}``,
    or ``{"all_of": [...]}`` / ``{"any_of": [...]}`` of clauses in that
    shape, nesting one level. ``min``/``max`` compare numerically, ``equals``
    compares the fact to a value, ``flag`` checks the fact truthy (or, given
    an explicit ``false``, falsy)."""
    billed_any_of: tuple[str, ...]
    """Gates the rule on the claim: it fires only when at least one of these
    codes is already submitted. Empty means the rule is not gated by what
    was billed."""
    candidate_codes: tuple[str, ...]
    """The code(s) this threshold's work could be billed under, in
    preference order. Empty means no billing vehicle exists at all, the
    same convention :class:`ProcedureRule` uses."""
    replaces: str | None
    """The billed code the candidate would supersede, priced as the
    difference (decision 4). ``None`` when the candidate prices at its own
    fee-schedule amount."""
    bucket_if_absent: Bucket
    """The bucket when the condition holds and no candidate is already
    submitted (or ``candidate_codes`` is empty, so there is nothing to check
    against)."""
    bucket_if_present: Bucket | None
    """The bucket when a candidate is already submitted. ``None`` means the
    work was captured on the claim, so no flag is produced at all."""
    statement: str
    """A format string over ``facts``, e.g. ``"Total documented time
    {total_documented_minutes} min supports the next E/M level by time"``."""
    section: str
    evidence_strength: Strength
    note: str = ""


class _WhenError(Method1Error):
    """A rule's ``when`` clause could not be evaluated against ``facts``."""


def _when_matches(when: Mapping[str, object], facts: Mapping[str, Decimal | str | bool]) -> bool:
    """Evaluate one ``WorkRule.when`` condition tree against a fact mapping.

    A referenced fact this extract did not supply reads as "condition not
    met" rather than raising: the fact mapping is deliberately partial (a
    visit extract's facts and an episode extract's do not overlap), and a
    pack should be free to name facts a given extract omits without every
    run on that extract crashing.
    """
    if "all_of" in when:
        clauses = when["all_of"]
        if not isinstance(clauses, list) or not clauses:
            msg = f"'all_of' must be a non-empty list of clauses: {when!r}"
            raise _WhenError(msg)
        return all(_when_matches(clause, facts) for clause in clauses)
    if "any_of" in when:
        clauses = when["any_of"]
        if not isinstance(clauses, list) or not clauses:
            msg = f"'any_of' must be a non-empty list of clauses: {when!r}"
            raise _WhenError(msg)
        return any(_when_matches(clause, facts) for clause in clauses)

    fact_name = when.get("fact")
    if not isinstance(fact_name, str):
        msg = f"'when' clause names no fact: {when!r}"
        raise _WhenError(msg)
    value = facts.get(fact_name)

    if "min" in when:
        return (
            value is not None
            and not isinstance(value, bool)
            and _as_decimal(value) >= _as_decimal(when["min"])
        )
    if "max" in when:
        return (
            value is not None
            and not isinstance(value, bool)
            and _as_decimal(value) <= _as_decimal(when["max"])
        )
    if "equals" in when:
        target = when["equals"]
        if isinstance(value, bool) or isinstance(target, bool):
            return bool(value) == bool(target)
        if value is None:
            return False
        if isinstance(value, Decimal):
            return value == _as_decimal(target)
        return str(value) == str(target)
    if "flag" in when:
        want = when["flag"]
        want = True if want is None else bool(want)
        return bool(value) == want
    msg = f"'when' clause has none of min/max/equals/flag: {when!r}"
    raise _WhenError(msg)


def _as_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _primary_fact(when: Mapping[str, object]) -> str:
    """The fact a flag's synthetic source ref names: this clause's own fact,
    or its first nested clause's, so a flag from an ``all_of``/``any_of``
    rule still points at something specific."""
    fact = when.get("fact")
    if isinstance(fact, str):
        return fact
    for key in ("all_of", "any_of"):
        clauses = when.get(key)
        if isinstance(clauses, list) and clauses:
            return _primary_fact(clauses[0])
    return "work_rule"


def _fact_source_ref(rule: WorkRule) -> SourceRef:
    """Where a work-rule flag's evidence lives: not a note span, a
    structured field. ``file`` names the synthetic ``facts`` mapping rather
    than a delivered file, since no single file backs a flat fact — each
    extract's own ``facts()`` derives it from whichever tables it reads."""
    return SourceRef("facts", "", 0, _primary_fact(rule.when))


def evaluate_work_rules(
    encounter: Encounter,
    facts: Mapping[str, Decimal | str | bool],
    claim: Claim | None,
    rules: tuple[WorkRule, ...],
) -> tuple[Method1Flag, ...]:
    """Check every threshold rule in a pack's ``work_rules`` block against
    one encounter's facts and claim.

    The same bucketing question the note-phrase cross-check asks, asked of a
    structured threshold instead of a documented statement: a rule whose
    condition holds and whose candidate is not already submitted produces a
    flag in ``bucket_if_absent``; one whose candidate is already submitted
    produces nothing unless ``bucket_if_present`` names a bucket for that
    case too. ``submitted_codes`` falls back to the OR-log-equivalent panel,
    ``encounter.procedures``, when no claim has been linked, the same
    convention :func:`cross_check` uses.
    """
    if claim is not None:
        submitted_codes = claim.codes
    else:
        submitted_codes = tuple(dict.fromkeys(cpt for cpt, _ in encounter.procedures))
    submitted = frozenset(submitted_codes)

    out: list[Method1Flag] = []
    for rule in rules:
        if not _when_matches(rule.when, facts):
            continue
        if rule.billed_any_of and not submitted.intersection(rule.billed_any_of):
            continue

        candidate_hit = submitted.intersection(rule.candidate_codes)
        if candidate_hit:
            if rule.bucket_if_present is None:
                continue
            bucket = rule.bucket_if_present
            reason = f"candidate {sorted(candidate_hit)[0]} already submitted"
        else:
            bucket = rule.bucket_if_absent
            reason = rule.note or f"work rule {rule.rule_id} matched"

        try:
            statement = rule.statement.format(**facts)
        except (KeyError, IndexError, ValueError) as exc:
            msg = f"{rule.rule_id}: statement cannot be formatted from facts: {exc}"
            raise Method1Error(msg) from exc

        out.append(
            Method1Flag(
                encounter_id=encounter.encounter_id,
                statement=statement,
                section=rule.section,
                rule_id=rule.rule_id,
                candidate_codes=rule.candidate_codes,
                submitted_codes=submitted_codes,
                bucket=bucket,
                reason=reason,
                vehicle_exists=bool(rule.candidate_codes),
                evidence_strength=rule.evidence_strength,
                priced=None,
                pricing_trace=(),
                source_ref=_fact_source_ref(rule),
                replaces=rule.replaces,
            )
        )
    return tuple(out)


@lru_cache(maxsize=1)
def _procedure_rules() -> tuple[ProcedureRule, ...]:
    """The shipped pack's ``procedures`` block, cached.

    Reloading and re-validating the pack JSON on every note would make
    Method 1 the slowest part of a run for no benefit: the pack is content
    addressed and shipped inside this package, so it cannot change under a
    running process.
    """
    return rulepack.load().procedures


def _performed_lines(
    note: Note, start: int, end: int, section_name: str
) -> list[ProcedureStatement]:
    """Every non-empty, non-negated line of a PROCEDURE PERFORMED section."""
    out: list[ProcedureStatement] = []
    text = note.text
    offset = start
    for raw_line in text[start:end].splitlines(keepends=True):
        stripped = raw_line.strip()
        if stripped:
            line_start = offset + raw_line.index(stripped)
            line_end = line_start + len(stripped)
            if not _modified(text, line_start, start):
                match = Match(
                    pattern_id="procedure-performed-line",
                    value=Decimal(0),
                    section=section_name,
                    start=line_start,
                    end=line_end,
                    evidence=stripped,
                )
                out.append(
                    ProcedureStatement(
                        encounter_id=note.encounter_id,
                        text=stripped,
                        section=section_name,
                        source_ref=source_ref(note, match),
                    )
                )
        offset += len(raw_line)
    return out


def statements(note: Note) -> tuple[ProcedureStatement, ...]:
    """Extract candidate procedure statements from one note.

    Every non-empty line of PROCEDURE PERFORMED is a statement: it is the
    surgeon's own summary of what was done, and it names the primary step
    even on notes too terse to describe anything in FINDINGS or DESCRIPTION
    OF PROCEDURE. From FINDINGS and DESCRIPTION OF PROCEDURE, only sentences
    that mention a step the rule pack's ``procedures`` block knows how to
    price become statements; that section is prose written for a colleague,
    not a checklist, and pulling in every sentence would flood the
    cross-check with steps that have no code to check against and are not
    the point of Method 1 anyway.

    INDICATION and the preamble are never read: INDICATION is why the
    patient is in theatre, not what was done to them today, the same
    section-scope discipline ``notes.py`` applies to markers. A negated or
    historical statement, "no adhesiolysis was required", "adhesiolysis was
    performed at a prior admission", is excluded the same way a marker match
    is: by looking back to the start of the clause for a negation or
    historicity trigger.
    """
    out: list[ProcedureStatement] = []
    for section in sections(note.text):
        if section.name == _PROCEDURE_PERFORMED:
            out.extend(_performed_lines(note, section.start, section.end, section.name))

    rules = _procedure_rules()
    patterns = tuple(
        rulepack.Pattern(pattern_id=rule.rule_id, regex=regex, value=Decimal(0))
        for rule in rules
        for regex in rule.patterns
    )
    if patterns:
        seen: set[tuple[str, str]] = set()
        for match in find(note, patterns, allowed=_NARRATIVE_SECTIONS, excluded=frozenset()):
            key = (match.section, match.evidence)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                ProcedureStatement(
                    encounter_id=note.encounter_id,
                    text=match.evidence,
                    section=match.section,
                    source_ref=source_ref(note, match),
                )
            )
    return tuple(out)


def _matching_rule(
    stmt: ProcedureStatement, rules: tuple[ProcedureRule, ...]
) -> ProcedureRule | None:
    """The first rule whose pattern fires on this statement, in pack order."""
    for rule in rules:
        if stmt.section not in rule.sections:
            continue
        if any(pattern.search(stmt.text) for pattern in rule.patterns):
            return rule
    return None


def _evidence_strength(
    stmt: ProcedureStatement, rule: ProcedureRule, panel_codes: frozenset[str]
) -> Strength:
    """How much this statement, on its own, supports the flag it produced.

    ``strong`` when the step is named in PROCEDURE PERFORMED, the surgeon's
    own summary line, or when it is named elsewhere *and* the OR log's own
    procedure panel already lists one of its candidate codes (the biller's
    own coding, agreeing with the note). ``moderate`` when it is named in
    FINDINGS or DESCRIPTION OF PROCEDURE with a verb that says the step was
    actually carried out rather than merely observed. Otherwise ``weak``.
    """
    if stmt.section == _PROCEDURE_PERFORMED:
        return "strong"
    if panel_codes.intersection(rule.candidate_codes):
        return "strong"
    if stmt.section in _NARRATIVE_SECTIONS and _PERFORMED_VERB.search(stmt.text):
        return "moderate"
    return "weak"


def cross_check(
    encounter: Encounter,
    stmts: tuple[ProcedureStatement, ...],
    claim: Claim | None,
    rules: tuple[ProcedureRule, ...],
) -> tuple[Method1Flag, ...]:
    """Check each documented statement against what was actually submitted.

    ``submitted_codes`` comes from the 837 claim when one has been linked to
    this encounter; a partner that has not started delivering claims yet
    falls back to the OR log panel (``encounter.procedures``), which is
    Epic's own record of what the surgical team intended to bill and the
    best available answer until the 837 arrives.

    Bucketing: a candidate code already present among the submitted codes
    means the step was captured, so no flag is produced at all. Failing
    that: no candidate code exists → ``no_code``, nothing to check against.
    A candidate exists but was not submitted, and none of the codes it is
    bundled with were submitted either → ``missed``, a code that should have
    been billed. A candidate exists, was not submitted, but one of the codes
    it bundles into under NCCI was → ``mismatched``: the work is already
    paid for as part of that code, so the gap is expected, not lost revenue.
    """
    if claim is not None:
        submitted_codes = claim.codes
    else:
        submitted_codes = tuple(dict.fromkeys(cpt for cpt, _ in encounter.procedures))
    submitted = frozenset(submitted_codes)
    panel_codes = frozenset(cpt for cpt, _ in encounter.procedures)

    out: list[Method1Flag] = []
    for stmt in stmts:
        rule = _matching_rule(stmt, rules)
        if rule is None:
            continue
        candidates = rule.candidate_codes
        if candidates and submitted.intersection(candidates):
            continue

        if not candidates:
            bucket: Bucket = "no_code"
            reason = "no candidate code"
        else:
            candidate = candidates[0]
            bundled_hit = next((code for code in rule.bundled_with if code in submitted), None)
            if bundled_hit is not None:
                bucket = "mismatched"
                reason = f"bundled with {bundled_hit} under rule ncci/{candidate}-{bundled_hit}"
            else:
                bucket = "missed"
                reason = (
                    f"candidate code {candidate} not submitted and not bundled "
                    "with any submitted code"
                )

        out.append(
            Method1Flag(
                encounter_id=encounter.encounter_id,
                statement=stmt.text,
                section=stmt.section,
                rule_id=rule.rule_id,
                candidate_codes=candidates,
                submitted_codes=submitted_codes,
                bucket=bucket,
                reason=reason,
                vehicle_exists=bool(candidates),
                evidence_strength=_evidence_strength(stmt, rule, panel_codes),
                priced=None,
                pricing_trace=(),
                source_ref=stmt.source_ref,
            )
        )
    return tuple(out)


def price_flag(flag: Method1Flag, *, amount: Decimal | None, trace: tuple[str, ...]) -> Method1Flag:
    """Return a copy of ``flag`` carrying a fee-schedule amount and its trace.

    Pricing needs the fee schedule and the payer's multiplier, neither of
    which this module has, so it is always done by the caller and attached
    here rather than computed. ``amount`` stays ``None`` for a ``no_code``
    flag: there is no reference to price it against.
    """
    return replace(flag, priced=amount, pricing_trace=trace)


def summarize(flags: tuple[Method1Flag, ...]) -> Method1Summary:
    """Count flags and sum their priced dollars, one total per bucket."""
    by_bucket: dict[str, BucketTotal] = {}
    with money_context():
        for bucket in _BUCKETS:
            bucket_flags = [f for f in flags if f.bucket == bucket]
            priced = [f.priced for f in bucket_flags if f.priced is not None]
            dollars = sum(priced, start=Decimal(0)) if priced else None
            by_bucket[bucket] = BucketTotal(
                count=len(bucket_flags), dollars=dollars, priced_count=len(priced)
            )
    return Method1Summary(n_flags=len(flags), by_bucket=by_bucket)
