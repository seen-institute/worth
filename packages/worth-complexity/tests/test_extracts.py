"""Dispatch by anchor file (decision 3, CONTRACT-PACKS.md), the surgical
adapter that wraps ``cases.py`` + ``markers.py`` unchanged, and
``CombinedExtract`` for a directory naming more than one anchor (decision 1,
CONTRACT-PACKS-MC.md)."""

from __future__ import annotations

from pathlib import Path

import pytest
from worth_complexity.cli import FIXTURE
from worth_complexity.extracts import (
    READERS,
    CombinedExtract,
    ExtractError,
    SurgicalExtract,
    read_extract,
)

CLINICAL = FIXTURE / "clinical"
VISIT_CLINICAL = FIXTURE.parent / "visit-synthetic" / "clinical"
EPISODE_CLINICAL = FIXTURE.parent / "episode-synthetic" / "clinical"
MIXED_CLINICAL = FIXTURE.parent / "mixed-synthetic" / "clinical"


def _merge_two(tmp_path: Path, a: Path, b: Path) -> Path:
    """A tiny two-class combined directory built from two committed
    fixtures' own ``clinical/`` directories, via
    ``worth_complexity.synthetic.mixed``'s own merge helpers -- visit and
    episode share identical schemas on every table they both carry
    (``patient_lds.txt``, ``problem_list.txt``), so this needs no union-schema
    merge, unlike surgical+visit's ``encounter_dx.txt``."""
    from worth_complexity.synthetic.mixed import merge_clinical

    out = tmp_path / "clinical"
    merge_clinical({"visit": a, "episode": b}, out)
    return out


def test_or_log_dispatches_to_the_surgical_reader() -> None:
    extract = read_extract(CLINICAL)
    assert isinstance(extract, SurgicalExtract)
    assert extract.encounter_class == "surgical"


def test_the_surgical_adapter_matches_cases_py_directly() -> None:
    """The adapter changes no behaviour: it wraps ``cases.read_extract`` +
    ``cases.encounters`` + ``markers.extract``, unchanged."""
    from worth_complexity import cases, markers

    extract = read_extract(CLINICAL)
    raw = cases.read_extract(CLINICAL)
    expected_encounters = cases.encounters(raw)
    assert extract.encounters() == expected_encounters
    assert extract.markers(expected_encounters) == markers.extract(raw, expected_encounters, None)
    assert extract.notes == raw.notes
    assert extract.tables == raw.tables


def test_file_hashes_covers_every_table_and_note_and_nothing_else() -> None:
    extract = read_extract(CLINICAL)
    hashes = extract.file_hashes()
    names = {name for name, _ in hashes}
    assert {"or_log.txt", "or_log_proc.txt", "or_staff.txt", "encounter_dx.txt"} <= names
    assert all(len(sha) == 64 for _, sha in hashes)
    # every table and every note, exactly once
    assert len(hashes) == len(extract.tables) + len(extract.notes)
    assert len(hashes) == len(set(hashes))


def test_surgical_facts_is_empty() -> None:
    """The surgical class's Method 1 is the note-phrase ``procedures``
    cross-check, not a threshold work-rules block."""
    extract = read_extract(CLINICAL)
    assert extract.facts("anything") == {}


def test_no_anchor_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ExtractError, match="no recognised extract anchor"):
        read_extract(tmp_path)


def test_two_anchors_read_as_a_combined_extract(tmp_path: Path) -> None:
    """Decision 1 (CONTRACT-PACKS-MC.md): more than one anchor no longer
    refuses -- it reads a class per anchor and wraps them. ``notes``/
    ``tables``/``file_hashes`` are the union, ``encounters()`` concatenates."""
    directory = _merge_two(tmp_path, VISIT_CLINICAL, EPISODE_CLINICAL)
    extract = read_extract(directory)
    assert isinstance(extract, CombinedExtract)
    assert extract.encounter_class == "mixed"
    assert extract.classes == ("episode", "visit")

    visit_only = read_extract(VISIT_CLINICAL)
    episode_only = read_extract(EPISODE_CLINICAL)
    expected_n = len(visit_only.encounters()) + len(episode_only.encounters())
    assert len(extract.encounters()) == expected_n
    assert {e.encounter_class for e in extract.encounters()} == {"visit", "episode"}
    assert extract.notes == visit_only.notes  # episode carries no note dataset
    # ``patient_lds.txt`` and ``problem_list.txt`` are read by both classes'
    # readers from the identical merged file; ``tables`` dedupes by
    # (name, sha256), so the combined count is short by exactly those two.
    assert len(extract.tables) == len(visit_only.tables) + len(episode_only.tables) - 2


def test_combined_file_hashes_is_the_union(tmp_path: Path) -> None:
    directory = _merge_two(tmp_path, VISIT_CLINICAL, EPISODE_CLINICAL)
    extract = read_extract(directory)
    hashes = extract.file_hashes()
    assert len(hashes) == len(set(hashes))
    assert len(hashes) == len(extract.tables) + len(extract.notes)


def test_combined_notes_for_and_facts_dispatch_by_encounter_class(tmp_path: Path) -> None:
    """Decision 1: ``notes_for``/``facts`` route to the sub-extract that
    actually owns the encounter, not the first one tried."""
    directory = _merge_two(tmp_path, VISIT_CLINICAL, EPISODE_CLINICAL)
    extract = read_extract(directory)
    visit_only = read_extract(VISIT_CLINICAL)
    episode_only = read_extract(EPISODE_CLINICAL)

    visit_enc = next(e for e in extract.encounters() if e.encounter_class == "visit")
    episode_enc = next(e for e in extract.encounters() if e.encounter_class == "episode")

    visit_note_types = frozenset({"visit"})
    assert extract.notes_for(visit_enc.encounter_id, visit_note_types) == visit_only.notes_for(
        visit_enc.encounter_id, visit_note_types
    )
    assert extract.notes_for(episode_enc.encounter_id, visit_note_types) == ()

    assert extract.facts(visit_enc.encounter_id) == visit_only.facts(visit_enc.encounter_id)
    assert extract.facts(episode_enc.encounter_id) == episode_only.facts(episode_enc.encounter_id)
    assert extract.facts("no-such-id") == {}


def test_combined_markers_dispatches_by_encounter_class(tmp_path: Path) -> None:
    directory = _merge_two(tmp_path, VISIT_CLINICAL, EPISODE_CLINICAL)
    extract = read_extract(directory)
    encounters = extract.encounters()
    marker_sets = extract.markers(encounters)
    assert set(marker_sets) == {e.encounter_id for e in encounters}


def test_three_anchors_read_as_a_combined_extract() -> None:
    """The real mixed fixture: three anchors, three classes, decision 7."""
    if not MIXED_CLINICAL.is_dir():
        pytest.skip("fixtures/mixed-synthetic not built; run `just build-dataset mixed`")
    extract = read_extract(MIXED_CLINICAL)
    assert isinstance(extract, CombinedExtract)
    assert extract.classes == ("episode", "surgical", "visit")
    assert {e.encounter_class for e in extract.encounters()} == {"surgical", "visit", "episode"}


def test_visit_anchor_dispatches_to_the_real_reader(tmp_path: Path) -> None:
    """``visit.txt`` now dispatches to ``visits.read_visit_extract``: a
    directory carrying only the anchor file fails reading the next table
    the reader needs, not the "not built yet" placeholder any more."""
    from worth_complexity.cases import ExtractError as CasesExtractError

    (tmp_path / "visit.txt").write_text(
        "visit_id|csn|patient_id|billing_account_id|service_date|service_line|specialty|"
        "cohort|site_npi|clinician_id|total_documented_minutes\n"
    )
    with pytest.raises(CasesExtractError, match=r"missing extract file: visit_proc\.txt"):
        read_extract(tmp_path)


def test_episode_anchor_dispatches_to_the_real_reader(tmp_path: Path) -> None:
    """``episode.txt`` now dispatches to ``episodes.read_episode_extract``: a
    directory carrying only the anchor file fails reading the next table
    the reader needs, not the "not built yet" placeholder any more."""
    from worth_complexity.cases import ExtractError as CasesExtractError

    (tmp_path / "episode.txt").write_text(
        "episode_id|patient_id|billing_account_id|start_date|end_date|service_line|"
        "specialty|cohort|site_npi|clinician_id\n"
    )
    with pytest.raises(CasesExtractError, match=r"missing extract file: episode_proc\.txt"):
        read_extract(tmp_path)


def test_readers_registers_all_three_anchors() -> None:
    assert set(READERS) == {"or_log.txt", "visit.txt", "episode.txt"}
