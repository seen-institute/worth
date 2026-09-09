"""``worth-cli`` against the mixed-synthetic fixture (CONTRACT-PACKS-MC.md,
"MC" track, decision 7): three anchors, three classes, one 835/837 feed.
Exercises the multi-class CLI surface -- ``run --json``'s ``classes[]`` plus
the flattened fields, ``compare`` grouped by class, ``compare --domains``
(``population.domain_rows``, decision 5), and ``code``/``slope`` finding the
class from the code's own card.
"""

from __future__ import annotations

import json

import pytest
from worth_cli.cli import main
from worth_complexity.cli import FIXTURE

MIXED_CLINICAL = FIXTURE.parent / "mixed-synthetic" / "clinical"
MIXED_REMITTANCE = FIXTURE.parent / "mixed-synthetic" / "remittance"

pytestmark = pytest.mark.skipif(
    not MIXED_CLINICAL.is_dir(),
    reason="fixtures/mixed-synthetic not built; run `just build-dataset mixed`",
)

_ARGS = ["--clinical", str(MIXED_CLINICAL), "--remittance", str(MIXED_REMITTANCE)]


def test_run_json_carries_classes_and_the_flattened_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["run", *_ARGS, "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    r = doc["run"]
    assert [c["encounter_class"] for c in r["classes"]] == ["surgical", "visit", "episode"]
    for name in ("records", "cards", "compare", "queue", "observations", "adequacies"):
        assert r[name], name
    # a single rule pack, setting, etc. have no one answer on a mixed run,
    # so run_document omits them rather than tripping Run._only_class's raise.
    assert "pack" not in r
    assert "setting" not in r


def test_run_text_report_has_one_section_per_class(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", *_ARGS]) == 0
    out = capsys.readouterr().out
    assert "CLASS: SURGICAL" in out
    assert "CLASS: VISIT" in out
    assert "CLASS: EPISODE" in out
    assert "classes     surgical, visit, episode" in out


def test_compare_groups_rows_by_class_with_a_class_column(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["compare", *_ARGS, "--table"]) == 0
    out = capsys.readouterr().out
    assert "class" in out.splitlines()[1]  # header row, after the meta comment
    surgical_at = out.find("surgical")
    visit_at = out.find("visit")
    episode_at = out.find("episode")
    assert surgical_at != -1
    assert surgical_at < visit_at < episode_at


def test_compare_domains_on_the_mixed_fixture_includes_the_visit_domains(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``population.domain_rows`` (decision 5) run once per class; the
    visit class's own two rows (MENOPAUSE, MATERNITY) must show up among
    the mixed fixture's combined domain rows."""
    assert main(["compare", "--domains", *_ARGS, "--json"]) == 0
    docs = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    domains_by_class: dict[str, set[str]] = {}
    for doc in docs:
        domains_by_class.setdefault(doc["class"], set()).add(doc["domain"])
    assert domains_by_class["visit"] == {"MENOPAUSE", "MATERNITY"}
    assert "surgical" in domains_by_class
    assert "episode" in domains_by_class


def test_code_58662_finds_the_surgical_class_on_the_mixed_fixture(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["code", "58662", *_ARGS, "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["code"] == "58662"
    assert doc["encounter_class"] == "surgical"


def test_code_99214_finds_the_visit_class_on_the_mixed_fixture(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["code", "99214", *_ARGS, "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["encounter_class"] == "visit"


def test_slope_58662_on_the_mixed_fixture(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["slope", "58662", *_ARGS, "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["code"] == "58662"


def test_queue_carries_a_class_column(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["queue", *_ARGS, "--table"]) == 0
    out = capsys.readouterr().out
    header = out.splitlines()[1]
    assert "class" in header
