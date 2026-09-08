"""Reading complexity out of clinical narrative, deterministically.

The methodology defines Layer A as "a transparent formula over facts extracted
from the record. Facts come from structured fields plus rule-based note reading, regex, ontologies,
negation detection, section parsing." This module is that
second half. It is crude on purpose: patterns, section scope and negation, and
nothing else. There is no model in it, so a published score stays a function of
the record and a rule anyone can read.

Three things make the difference between pattern matching and a complexity
reading, and all three are failures a plain grep would make on a real operative
note.

**Section scope.** "History of bowel resection" under INDICATION is the reason
the patient is in theatre, not work performed today. A marker that counted it
would score the case on its history. Every rule names the sections it may read
and the sections it may not.

**Negation.** "No evidence of bladder involvement" contains the word bladder. A
rule that counted it would score the surgeon for excluding a finding. Negation
is detected the way ConText does it: look back from the match to the start of
the clause, and if a negation trigger sits in that span with nothing to
terminate its scope, the match does not count.

**Historicity.** "Status post prior hysterectomy" is the same problem in a
different tense, and is handled the same way.

What is deliberately not here is the nuance the methodology assigns elsewhere:
the generative lane exists for "complexity signals that rules cannot reach". A
regex can find the phrase "dense adhesions". It cannot weigh how dense, on a
note that never uses the word. Layer A takes the part it can defend and
understates the rest, which is the safe direction to be wrong in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from worth_complexity.models import SourceRef, WorthComplexityError

if TYPE_CHECKING:
    from worth_complexity.rulepack import Pattern

# A section header: a short upper-case line, either standing alone or followed
# by a colon and the section's content on the same line. Operative notes write
# both, "FINDINGS" on its own, "ESTIMATED BLOOD LOSS: 450 mL" inline, and a
# reader that only understood the first would lose every single-line section.
_HEADER = re.compile(r"^[ \t]*([A-Z][A-Z0-9 /&'()-]{2,60}?)[ \t]*(?::[ \t]*|$)", re.M)

# Clause boundaries. A negation trigger does not reach past one of these.
#
# A lone newline is *not* one. Dictated notes wrap wherever the transcription
# software wrapped them, so "There was no\nevidence of bladder involvement" is
# one clause with a line break in the middle of it, and treating the break as a
# boundary would let the negation drop and the finding count. A blank line is a
# boundary, and so is a line that opens a list item, because those are the two
# places a new clause actually begins.
_TERMINATOR = re.compile(
    r"[.;:]"
    r"|\n[ \t]*\n"
    r"|\n[ \t]*(?:[-*\u2022]|\d+[.)])"
    r"|\b(?:but|however|although|though|except|otherwise|then|and then)\b",
    re.I,
)

_NEGATION = re.compile(
    r"\b(?:no|not|without|negative for|free of|absent|denies|denied|ruled out|"
    r"no evidence of|no sign of|there was no|there were no|unremarkable for)\b",
    re.I,
)

_HISTORICAL = re.compile(
    r"\b(?:history of|hx of|prior|previous|previously|status post|s/p|remote|"
    r"in the past|preoperatively noted)\b",
    re.I,
)

# How far back a modifier may sit and still govern the match.
_LOOKBACK = 90


class NoteError(WorthComplexityError):
    """A note is malformed, or a rule pack asks for something it cannot supply."""


@dataclass(frozen=True, slots=True)
class Note:
    """One clinical document, as delivered."""

    note_id: str
    encounter_id: str
    note_type: str
    """``operative``, ``discharge``, ``progress``. Rules scope by this."""
    service_date: date
    author_role: str
    filename: str
    sha256: str
    text: str


@dataclass(frozen=True, slots=True)
class Section:
    """A named span of a note."""

    name: str
    """Lower-cased header text, e.g. ``findings``."""
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Match:
    """One rule firing, with the span and the text it fired on."""

    pattern_id: str
    value: Decimal
    section: str
    start: int
    end: int
    evidence: str


def sections(text: str) -> tuple[Section, ...]:
    """Split a note on its headers.

    Anything before the first header belongs to a ``preamble`` section, which no
    rule pack should read: it is the letterhead and the identifiers.
    """
    heads = [(m.start(), m.end(), m.group(1).strip().lower()) for m in _HEADER.finditer(text)]
    if not heads:
        return (Section("body", 0, len(text)),)

    out: list[Section] = []
    first = heads[0][0]
    if first > 0:
        out.append(Section("preamble", 0, first))
    for i, (_, body_start, name) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(text)
        out.append(Section(name, body_start, end))
    return tuple(out)


def _modified(text: str, start: int, section_start: int) -> bool:
    """True when a negation or historical trigger governs a match at ``start``.

    The window runs back to the nearest clause terminator, so "dense adhesions"
    in "no dense adhesions" is refused while the same phrase in "adhesiolysis
    was performed; dense adhesions were encountered" is kept.
    """
    window_start = max(section_start, start - _LOOKBACK)
    window = text[window_start:start]
    last = 0
    for term in _TERMINATOR.finditer(window):
        last = term.end()
    clause = window[last:]
    return bool(_NEGATION.search(clause) or _HISTORICAL.search(clause))


def _sentence(text: str, start: int, end: int) -> str:
    """The clause a match sits in, for the evidence field."""
    left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start)) + 1
    right = min(
        (i for i in (text.find(".", end), text.find("\n", end)) if i != -1),
        default=len(text),
    )
    return " ".join(text[left:right].split())[:220]


def find(
    note: Note,
    patterns: tuple[Pattern, ...],
    *,
    allowed: frozenset[str],
    excluded: frozenset[str],
) -> tuple[Match, ...]:
    """Run a rule pack's patterns over one note, in pack order then text order."""
    scoped = [
        s
        for s in sections(note.text)
        if s.name not in excluded and (not allowed or s.name in allowed)
    ]
    out: list[Match] = []
    for pattern in patterns:
        for section in scoped:
            body = note.text[section.start : section.end]
            for m in pattern.regex.finditer(body):
                start = section.start + m.start()
                if _modified(note.text, start, section.start):
                    continue
                if pattern.value is not None:
                    captured = pattern.value
                elif m.groups():
                    captured = Decimal(m.group(1)) * pattern.scale
                else:
                    msg = f"{pattern.pattern_id}: numeric pattern captures no group"
                    raise NoteError(msg)
                out.append(
                    Match(
                        pattern_id=pattern.pattern_id,
                        value=captured,
                        section=section.name,
                        start=start,
                        end=section.start + m.end(),
                        evidence=_sentence(note.text, start, section.start + m.end()),
                    )
                )
    return tuple(out)


def source_ref(note: Note, match: Match) -> SourceRef:
    """Point at the exact characters a rule fired on."""
    line = note.text.count("\n", 0, match.start) + 1
    return SourceRef(note.filename, note.sha256, line, f"chars {match.start}-{match.end}")
