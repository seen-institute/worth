"""The rule pack is data, and its invariants are enforced at load."""

from __future__ import annotations

import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path

import pytest
from worth_complexity import RulePackError
from worth_complexity.rulepack import _PACK_DIR, available, load, load_path, loads

BASE = {
    "rule_pack_id": "t",
    "version": "0",
    "markers": [
        {
            "marker_id": "a",
            "provenance": "structured",
            "weight": "0.5",
            "anchor_low": "0",
            "anchor_high": "10",
        },
        {
            "marker_id": "b",
            "provenance": "structured",
            "weight": "0.5",
            "anchor_low": "0",
            "anchor_high": "10",
        },
    ],
}


def pack(**overrides: object) -> bytes:
    doc = json.loads(json.dumps(BASE))
    doc.update(overrides)
    return json.dumps(doc).encode()


def test_the_shipped_pack_loads_and_its_weights_sum_to_one() -> None:
    p = load("surgical-v1")
    assert sum((m.weight for m in p.markers), start=Decimal(0)) == Decimal(1)


def test_the_shipped_pack_is_marked_provisional() -> None:
    """Until weights come from clinical and statistical review, nothing is publishable."""
    assert load("surgical-v1").is_provisional


def test_the_digest_is_of_the_bytes_as_shipped() -> None:
    """A published index value names the exact rules that produced it, forever."""
    shipped = (_PACK_DIR / "surgical-v1.json").read_bytes()
    assert load("surgical-v1").digest == hashlib.sha256(shipped).hexdigest()


def test_weights_that_do_not_sum_to_one_are_refused() -> None:
    """A pack summing to 0.99 produces scores quietly 1% low, comparable with nothing."""
    doc = json.loads(json.dumps(BASE))
    doc["markers"][0]["weight"] = "0.49"
    with pytest.raises(RulePackError, match="sum to exactly 1"):
        loads(json.dumps(doc).encode())


def test_inverted_anchors_are_refused() -> None:
    doc = json.loads(json.dumps(BASE))
    doc["markers"][0]["anchor_high"] = "-1"
    with pytest.raises(RulePackError, match="anchor_high"):
        loads(json.dumps(doc).encode())


def test_a_missing_pack_is_named() -> None:
    with pytest.raises(RulePackError, match="no such rule pack"):
        load("does-not-exist")


@pytest.mark.parametrize(
    ("value", "expected"),
    [("-5", "0"), ("0", "0"), ("5", "0.5"), ("10", "1"), ("500", "1")],
)
def test_normalisation_clamps_outside_the_anchors(value: str, expected: str) -> None:
    """An eleven-hour case is not twice as complex as a five-hour one on an
    ordinal instrument, and one outlier must not run the scale past 100."""
    rule = loads(pack()).markers[0]
    assert rule.normalise(Decimal(value)) == Decimal(expected)


# ---------------------------------------------------------------------------
# Layer 2: resolution order, external directories, and shadowing
# ---------------------------------------------------------------------------


def test_the_packaged_pack_reports_its_own_source() -> None:
    p = load("surgical-v1")
    assert p.source == "packaged"
    assert p.source_path is None
    assert p.filename == "surgical-v1.json"
    assert p.declared_status is None
    assert p.status_note == ""


def test_search_directories_are_tried_in_order(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "cand-v1.json").write_bytes(pack())
    later = json.loads(pack())
    later["version"] = "9"
    (second / "cand-v1.json").write_bytes(json.dumps(later).encode())

    p = load("cand-v1", search=[first, second])
    assert p.version == "0"
    assert p.source == "external"
    assert p.source_path == str((first / "cand-v1.json").resolve())


def test_the_name_may_be_given_with_or_without_the_json_suffix(tmp_path: Path) -> None:
    (tmp_path / "cand-v1.json").write_bytes(pack())
    without_suffix = load("cand-v1", search=[tmp_path])
    with_suffix = load("cand-v1.json", search=[tmp_path])
    assert without_suffix.digest == with_suffix.digest


def test_env_var_directories_are_tried_after_the_search_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from_search = tmp_path / "search"
    from_env = tmp_path / "env"
    from_search.mkdir()
    from_env.mkdir()
    (from_env / "cand-v2.json").write_bytes(pack())
    monkeypatch.setenv("WORTH_RULEPACK_DIR", str(from_env))

    p = load("cand-v2", search=[from_search])
    assert p.source_path == str((from_env / "cand-v2.json").resolve())


def test_env_var_is_read_fresh_at_call_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not cached from import or process start: setting it between two calls changes
    what the second call resolves, which is what lets a deployment turn a pilot pack
    on and off without restarting anything."""
    (tmp_path / "cand-v3.json").write_bytes(pack())
    monkeypatch.delenv("WORTH_RULEPACK_DIR", raising=False)
    with pytest.raises(RulePackError, match="no such rule pack"):
        load("cand-v3")

    monkeypatch.setenv("WORTH_RULEPACK_DIR", str(tmp_path))
    assert load("cand-v3").source == "external"


def test_env_var_supports_more_than_one_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty"
    holds_it = tmp_path / "holds_it"
    empty.mkdir()
    holds_it.mkdir()
    (holds_it / "cand-v4.json").write_bytes(pack())
    monkeypatch.setenv("WORTH_RULEPACK_DIR", os.pathsep.join([str(empty), str(holds_it)]))
    assert load("cand-v4").source == "external"


def test_a_packaged_pack_shadowed_by_an_external_one_is_an_error(tmp_path: Path) -> None:
    """First-match-wins has one exception: an external pack never silently overrides a
    packaged pack of the same file name. It must use a distinct name instead."""
    (tmp_path / "surgical-v1.json").write_bytes((_PACK_DIR / "surgical-v1.json").read_bytes())
    with pytest.raises(RulePackError) as exc_info:
        load("surgical-v1", search=[tmp_path])
    message = str(exc_info.value)
    assert str(_PACK_DIR / "surgical-v1.json") in message
    assert str((tmp_path / "surgical-v1.json").resolve()) in message


def test_load_path_loads_one_file_directly_as_external(tmp_path: Path) -> None:
    f = tmp_path / "whatever-this-is-named.json"
    f.write_bytes(pack())
    p = load_path(f)
    assert p.source == "external"
    assert p.source_path == str(f.resolve())
    assert p.filename == "whatever-this-is-named.json"


def test_load_path_names_a_missing_file() -> None:
    with pytest.raises(RulePackError):
        load_path(Path("/no/such/file/anywhere.json"))


def test_an_external_pack_declaring_ratified_is_capped_to_provisional(tmp_path: Path) -> None:
    f = tmp_path / "candidate-ratified.json"
    f.write_bytes(pack(status="ratified"))
    p = load_path(f)
    assert p.status == "provisional"
    assert p.declared_status == "ratified"
    assert p.status_note == (
        "external rule packs are capped at provisional; ratified packs ship with worth-complexity"
    )
    assert p.is_provisional


def test_a_packaged_pack_declaring_ratified_is_not_capped() -> None:
    """The cap is specific to ``source="external"``; :func:`loads` defaults to
    ``"packaged"``, so a caller loading packaged bytes never gets it."""
    p = loads(pack(status="ratified"))
    assert p.status == "ratified"
    assert p.declared_status is None
    assert p.status_note == ""


# ---------------------------------------------------------------------------
# Layer 2: available()
# ---------------------------------------------------------------------------


def test_available_lists_the_packaged_pack() -> None:
    """One packaged pack per encounter class (CONTRACT-PACKS.md decision 1):
    episode-rpm, surgical, visit-em, sorted by file name."""
    infos = available()
    assert [i.name for i in infos] == ["episode-rpm-v1", "surgical-v1", "visit-em-v1"]
    assert {i.encounter_class for i in infos} == {"episode", "surgical", "visit"}
    for info in infos:
        assert info.source == "packaged"
        assert info.path is None
        assert info.status == "provisional"


def test_available_lists_packaged_before_external_and_survives_a_malformed_file(
    tmp_path: Path,
) -> None:
    (tmp_path / "good-v1.json").write_bytes(pack())
    (tmp_path / "bad-v1.json").write_bytes(b"{not valid json at all")

    infos = available(search=[tmp_path])
    names = [i.name for i in infos]
    assert names[:3] == ["episode-rpm-v1", "surgical-v1", "visit-em-v1"]
    assert "good-v1" in names
    assert "bad-v1" in names

    good = next(i for i in infos if i.name == "good-v1")
    assert good.source == "external"
    assert good.status == "provisional"
    assert good.note is None

    bad = next(i for i in infos if i.name == "bad-v1")
    assert bad.status == "invalid"
    assert bad.note


# ---------------------------------------------------------------------------
# work_rules: decision 4, CONTRACT-PACKS.md
# ---------------------------------------------------------------------------

A_WORK_RULE = {
    "id": "em-level-by-time",
    "when": {"fact": "total_documented_minutes", "min": "40"},
    "billed_any_of": ["99214"],
    "candidate_codes": ["99215"],
    "replaces": "99214",
    "bucket_if_absent": "missed",
    "bucket_if_present": None,
    "statement": "Total documented time {total_documented_minutes} min supports the "
    "next E/M level by time",
    "section": "time attestation",
    "evidence_strength": "strong",
    "note": "why this threshold",
}


def test_a_work_rules_block_parses_into_typed_work_rules() -> None:
    p = loads(pack(work_rules=[A_WORK_RULE]))
    assert len(p.work_rules) == 1
    rule = p.work_rules[0]
    assert rule.rule_id == "em-level-by-time"
    assert rule.when == {"fact": "total_documented_minutes", "min": "40"}
    assert rule.billed_any_of == ("99214",)
    assert rule.candidate_codes == ("99215",)
    assert rule.replaces == "99214"
    assert rule.bucket_if_absent == "missed"
    assert rule.bucket_if_present is None
    assert rule.section == "time attestation"
    assert rule.evidence_strength == "strong"


def test_a_pack_with_no_work_rules_block_has_an_empty_tuple() -> None:
    assert loads(pack()).work_rules == ()


def test_the_shipped_surgical_pack_has_no_work_rules() -> None:
    """The surgical class's Method 1 is the note-phrase ``procedures``
    block; ``work_rules`` is the visit/episode classes' equivalent."""
    assert load("surgical-v1").work_rules == ()


def test_all_of_nested_more_than_one_level_is_refused() -> None:
    rule = dict(A_WORK_RULE, when={"all_of": [{"all_of": [{"fact": "x", "min": "1"}]}]})
    with pytest.raises(RulePackError, match="nests more than one level"):
        loads(pack(work_rules=[rule]))


def test_an_unknown_when_key_is_refused() -> None:
    rule = dict(A_WORK_RULE, when={"fact": "x", "not_a_real_operator": "1"})
    with pytest.raises(RulePackError, match="unknown key"):
        loads(pack(work_rules=[rule]))


def test_a_when_clause_with_no_fact_and_no_nesting_is_refused() -> None:
    rule = dict(A_WORK_RULE, when={"min": "1"})
    with pytest.raises(RulePackError, match="names no fact"):
        loads(pack(work_rules=[rule]))


def test_a_when_clause_with_no_comparison_is_refused() -> None:
    rule = dict(A_WORK_RULE, when={"fact": "x"})
    with pytest.raises(RulePackError, match="none of min/max/equals/flag"):
        loads(pack(work_rules=[rule]))


def test_an_empty_all_of_is_refused() -> None:
    rule: dict[str, object] = dict(A_WORK_RULE, when={"all_of": []})
    with pytest.raises(RulePackError, match="non-empty list"):
        loads(pack(work_rules=[rule]))


def test_an_invalid_bucket_if_absent_is_refused() -> None:
    rule = dict(A_WORK_RULE, bucket_if_absent="not-a-bucket")
    with pytest.raises(RulePackError, match="bucket_if_absent"):
        loads(pack(work_rules=[rule]))


def test_an_invalid_bucket_if_present_is_refused() -> None:
    rule = dict(A_WORK_RULE, bucket_if_present="not-a-bucket")
    with pytest.raises(RulePackError, match="bucket_if_present"):
        loads(pack(work_rules=[rule]))


def test_an_invalid_evidence_strength_is_refused() -> None:
    rule = dict(A_WORK_RULE, evidence_strength="extremely-strong")
    with pytest.raises(RulePackError, match="evidence_strength"):
        loads(pack(work_rules=[rule]))
