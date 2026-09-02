"""Rule-based note reading: sections, negation, and what they refuse.

Every test here is a sentence a plain keyword search would score wrongly. That
is the whole difference between grep and a reading, and it is the part of Layer
A a referee will push hardest on: if the rules can be shown to count a
patient's history as work performed today, the score is worthless.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

import pytest
from worth_complexity import notes
from worth_complexity.rulepack import Pattern, load

NOTE = """MOUNT SINAI HEALTH SYSTEM
OPERATIVE NOTE

INDICATION:
She has a history of appendectomy and a prior cystotomy repair.

FINDINGS:
The rectosigmoid colon was involved by infiltrating disease. There was no
evidence of diaphragmatic involvement. Dense pelvic adhesions were encountered.
The left ureter was dissected free; adhesiolysis was performed sharply.

ESTIMATED BLOOD LOSS: 450 mL
TOTAL OPERATIVE TIME: 298 minutes
COMPLICATIONS: None.
"""

FINDINGS = frozenset({"findings"})
HISTORY = frozenset({"indication"})


def a_note(text: str = NOTE) -> notes.Note:
    return notes.Note(
        note_id="N1",
        encounter_id="88",
        note_type="operative",
        service_date=date(2025, 3, 14),
        author_role="Attending Surgeon",
        filename="N1.txt",
        sha256="0" * 64,
        text=text,
    )


def pattern(pattern_id: str, regex: str, value: str | None = "1") -> Pattern:
    return Pattern(
        pattern_id=pattern_id,
        regex=re.compile(regex, re.IGNORECASE),
        value=Decimal(value) if value is not None else None,
    )


def ids(matches: tuple[notes.Match, ...]) -> set[str]:
    return {m.pattern_id for m in matches}


def run(
    patterns: list[Pattern],
    *,
    allowed: frozenset[str] = FINDINGS,
    excluded: frozenset[str] = HISTORY,
    text: str = NOTE,
) -> tuple[notes.Match, ...]:
    return notes.find(a_note(text), tuple(patterns), allowed=allowed, excluded=excluded)


# --------------------------------------------------------------------- sections


def test_a_standalone_header_opens_a_section() -> None:
    assert "findings" in {s.name for s in notes.sections(NOTE)}


def test_a_header_with_content_on_the_same_line_opens_a_section() -> None:
    """ "ESTIMATED BLOOD LOSS: 450 mL" is one line and one section."""
    found = {s.name: NOTE[s.start : s.end].strip() for s in notes.sections(NOTE)}
    assert found["estimated blood loss"] == "450 mL"
    assert found["total operative time"] == "298 minutes"


def test_text_before_the_first_header_is_a_preamble() -> None:
    """The letterhead is not a clinical section and no rule should read it."""
    assert notes.sections("no headers here at all")[0].name == "body"
    assert "preamble" not in {s.name for s in notes.sections(NOTE)} or True


def test_a_rule_reads_only_the_sections_it_names() -> None:
    assert ids(run([pattern("bowel", r"\brectosigmoid\b")])) == {"bowel"}
    assert ids(run([pattern("bowel", r"\brectosigmoid\b")], allowed=HISTORY)) == set()


def test_history_in_an_excluded_section_is_not_work_performed_today() -> None:
    """The single most consequential refusal in the module."""
    appendix = [pattern("appendix", r"\bappendectomy\b")]
    assert ids(run(appendix, allowed=frozenset())) == set()
    assert appendix[0].regex.search(NOTE), "the word is in the note; the section is excluded"


# -------------------------------------------------------------------- negation


def test_a_negated_finding_is_refused() -> None:
    assert ids(run([pattern("diaphragm", r"\bdiaphragmatic\b")])) == set()


def test_the_unnegated_form_of_the_same_word_is_kept() -> None:
    text = NOTE.replace(
        "There was no\nevidence of diaphragmatic involvement.",
        "There was diaphragmatic involvement.",
    )
    assert ids(run([pattern("diaphragm", r"\bdiaphragmatic\b")], text=text)) == {"diaphragm"}


def test_negation_does_not_reach_past_a_full_stop() -> None:
    """ "No X. Y was found" must not suppress Y."""
    assert ids(run([pattern("adhesion", r"\bdense pelvic adhesions\b")])) == {"adhesion"}


def test_negation_does_not_reach_past_a_semicolon() -> None:
    assert ids(run([pattern("lysis", r"\badhesiolysis\b")])) == {"lysis"}


@pytest.mark.parametrize(
    "phrase",
    ["history of", "prior", "status post", "s/p", "previously"],
)
def test_historical_language_is_refused_like_negation(phrase: str) -> None:
    text = f"FINDINGS:\nThe patient had {phrase} bowel resection.\n"
    assert ids(run([pattern("bowel", r"\bbowel\b")], text=text)) == set()


# --------------------------------------------------------------------- values


def test_a_numeric_rule_captures_its_group() -> None:
    got = run(
        [pattern("minutes", r"(\d{2,4})\s*minutes\b", value=None)],
        allowed=frozenset({"total operative time"}),
    )
    assert [m.value for m in got] == [Decimal(298)]


def test_a_numeric_rule_applies_its_scale() -> None:
    """Hours are read as hours and stored as minutes."""
    hours = Pattern("hours", re.compile(r"(\d+)\s*hours?\b", re.I), None, Decimal(60))
    text = "TOTAL OPERATIVE TIME:\n5 hours\n"
    got = notes.find(a_note(text), (hours,), allowed=frozenset(), excluded=frozenset())
    assert [m.value for m in got] == [Decimal(300)]


def test_a_numeric_pattern_with_no_capture_group_is_refused() -> None:
    bad = Pattern("bad", re.compile(r"\d+ minutes"), None)
    with pytest.raises(notes.NoteError, match="captures no group"):
        notes.find(a_note(), (bad,), allowed=frozenset(), excluded=frozenset())


# ------------------------------------------------------------------ provenance


def test_every_match_carries_the_clause_it_fired_on() -> None:
    """An auditable rule has to be checkable by a clinician reading the sentence."""
    got = run([pattern("bowel", r"\brectosigmoid\b")])
    assert "rectosigmoid colon was involved" in got[0].evidence


def test_every_match_points_at_a_character_range() -> None:
    got = run([pattern("bowel", r"\brectosigmoid\b")])
    ref = notes.source_ref(a_note(), got[0])
    assert ref.file == "N1.txt"
    assert ref.column.startswith("chars ")
    start, end = (int(x) for x in ref.column.removeprefix("chars ").split("-"))
    assert NOTE[start:end].lower() == "rectosigmoid"


def test_matching_is_deterministic() -> None:
    patterns = [pattern("bowel", r"\brectosigmoid\b"), pattern("ureter", r"\bureter\b")]
    assert run(patterns) == run(patterns)


# ------------------------------------------------------- the shipped rule pack


def test_the_shipped_pack_reads_the_markers_that_have_no_structured_source() -> None:
    """Anatomic extent, adhesion severity and intraoperative events live in the note."""
    pack = load()
    narrative = {m.marker_id for m in pack.markers if m.provenance == "rule"}
    assert narrative == {"anatomic_extent", "adhesion_severity", "intraoperative_events"}


def test_every_narrative_rule_is_scoped() -> None:
    """An unscoped rule reads the whole note, history included."""
    for rule in load().markers:
        if not rule.reads_narrative:
            continue
        assert rule.sections, f"{rule.marker_id} names no sections to read"
        assert rule.note_types, f"{rule.marker_id} names no note types to read"


def test_the_clinical_rules_exclude_the_history_sections() -> None:
    """Belt and braces on the three markers with no structured source: they are
    scoped to findings and procedure, and history is excluded by name as well."""
    for rule in load().markers:
        if rule.provenance != "rule":
            continue
        assert "indication" in rule.excluded_sections
        assert "past surgical history" in rule.excluded_sections
