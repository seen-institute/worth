"""The W3 subcommands: score, explain, slope, code, compare, queue.

Each accepts the same dataset flags as ``run`` and each of ``--json``
(default), ``--table`` and ``--csv``; every subcommand's output carries the
version keys (``rulebook_version``, ``weights_version`` and the three package
versions). ``score`` prints newline-delimited JSON for a batch.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from worth_cli.cli import main
from worth_complexity.cli import FIXTURE, FIXTURE_LOCALITY
from worth_complexity.pipeline import run as pipeline_run

if TYPE_CHECKING:
    from worth_complexity.pipeline import Run

VERSION_KEYS = {
    "rulebook_version",
    "weights_version",
    "worth_cli_version",
    "worth_complexity_version",
    "worth_fees_version",
}


@pytest.fixture(scope="module")
def fixture_run() -> Run:
    """A pre-built ``Run`` to check the CLI's output against.

    ``worth_cli.cli.main`` always builds its own ``Run`` from the arguments
    it parses, so this does not save the CLI's own work — it exists so a test
    can find, say, a valid encounter id to pass to ``explain`` without
    shelling out through ``main`` first just to discover one. No
    ``conftest.py`` here: mypy's ``strict`` config checks every package's
    ``tests`` directory with no ``__init__.py`` markers, and two files both
    named ``conftest.py`` collide as the same top-level module under that
    scheme (see ``worth-complexity/tests/conftest.py``, which already owns
    that name).
    """
    return pipeline_run(FIXTURE / "clinical", FIXTURE / "remittance", locality=FIXTURE_LOCALITY)


@pytest.fixture(scope="module")
def sample_encounter_id(fixture_run: Run) -> str:
    return fixture_run.adequacies[0].encounter_id


# ---------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------


def test_score_with_no_ids_is_newline_delimited_json_for_every_scored_encounter(
    fixture_run: Run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["score"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == len(fixture_run.adequacies)
    docs = [json.loads(line) for line in lines]
    for doc in docs:
        assert set(doc) >= VERSION_KEYS
        assert doc["expected"] == doc["m3"]["expected"]


def test_score_with_ids_filters_to_those_encounters(
    sample_encounter_id: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["score", sample_encounter_id]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    doc = json.loads(lines[0])
    assert doc["encounter_id"] == sample_encounter_id


def test_score_table_has_a_version_comment_then_a_column_table(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["score", "--table"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("# rulebook_version=")
    for key in ("weights_version=", "worth_cli_version=", "worth_complexity_version="):
        assert key in lines[0]
    assert "encounter_id" in lines[1]


def test_score_csv_has_a_comment_then_a_header_then_rows(
    fixture_run: Run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["score", "--csv"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("# rulebook_version=")
    assert lines[1].split(",")[0] == "encounter_id"
    assert len(lines) == 2 + len(fixture_run.adequacies)


# ---------------------------------------------------------------------------
# explain
# ---------------------------------------------------------------------------


def test_explain_json_carries_the_full_derivation_and_version_keys(
    sample_encounter_id: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["explain", sample_encounter_id]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert set(doc) >= VERSION_KEYS
    assert doc["encounter_id"] == sample_encounter_id
    assert "spine" in doc and "signature" in doc and "method1_flags" in doc
    assert "report" in doc and "Dollar spine" in doc["report"]


def test_explain_json_carries_marker_rows_with_a_missing_flag(
    sample_encounter_id: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Decision 6, CONTRACT-SEEDS.md: ``explain --json`` exposes one row per
    rule pack marker, present or missing, since ``Adequacy`` itself carries
    no marker detail of its own."""
    assert main(["explain", sample_encounter_id]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["markers"]
    row = doc["markers"][0]
    assert {"marker_id", "provenance", "weight", "value", "contribution", "missing", "reason"} <= (
        set(row)
    )


def test_explain_table_shows_the_spine_and_signature(
    sample_encounter_id: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["explain", sample_encounter_id, "--table"]) == 0
    out = capsys.readouterr().out
    assert "Dollar spine" in out
    assert "Signature:" in out
    assert "Witness" in out


def test_explain_unknown_encounter_fails_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["explain", "no-such-encounter"]) == 1
    err = capsys.readouterr().err
    assert "no such scored encounter" in err


# ---------------------------------------------------------------------------
# slope
# ---------------------------------------------------------------------------


def test_slope_json_carries_the_pooled_method_zero_fit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["slope", "58662", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert set(doc) >= VERSION_KEYS
    assert doc["code"] == "58662"
    assert doc["verdict"] in {"flat", "rising", "insufficient spread"}


def test_slope_csv(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["slope", "58662", "--csv"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("# rulebook_version=")
    assert lines[1].split(",")[0] == "code"
    assert lines[2].split(",")[0] == "58662"


# ---------------------------------------------------------------------------
# code
# ---------------------------------------------------------------------------


def test_code_json_is_the_full_card(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["code", "58662"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert set(doc) >= VERSION_KEYS
    assert doc["code"] == "58662"
    assert doc["encounter_class"] == "surgical"
    assert "signature_mix" in doc and "shortfall" in doc


def test_code_by_payer_table(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["code", "58662", "--by", "payer", "--table"]) == 0
    out = capsys.readouterr().out
    assert "group" in out
    assert "Payer A" in out


@pytest.mark.parametrize("by", ["site", "payer", "surgeon", "period"])
def test_code_by_every_grouping_key(by: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["code", "58662", "--by", by, "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["by"] == by
    assert doc["rows"]


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


def test_compare_codes_is_one_row_per_code(
    fixture_run: Run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["compare"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == len(fixture_run.cards)
    doc = json.loads(lines[0])
    assert set(doc) >= VERSION_KEYS
    assert doc["code"]


def test_compare_domains_groups_by_service_line(capsys: pytest.CaptureFixture[str]) -> None:
    """Decision 6 (CONTRACT-PACKS.md): ``--domains`` groups by service line
    within a class, not by dominant lever. The synthetic fixture's five
    study codes are all GYN, so every row's domain is the same value."""
    assert main(["compare", "--domains", "--table"]) == 0
    out = capsys.readouterr().out
    assert "domain" in out
    assert "GYN" in out


# ---------------------------------------------------------------------------
# queue
# ---------------------------------------------------------------------------


def test_queue_json_is_one_document_with_every_item(
    fixture_run: Run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["queue"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert set(doc) >= VERSION_KEYS
    assert len(doc["items"]) == len(fixture_run.queue)


def test_queue_filters_by_site(fixture_run: Run, capsys: pytest.CaptureFixture[str]) -> None:
    site = fixture_run.queue[0].site
    assert main(["queue", "--site", site, "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["items"]
    assert all(item["site"] == site for item in doc["items"])


def test_queue_filters_by_bucket(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["queue", "--bucket", "mismatched", "--table"]) == 0
    out = capsys.readouterr().out
    assert "# rulebook_version=" in out


# ---------------------------------------------------------------------------
# run --json still includes the new Run fields (decision: report in under a
# separate module, but W3's addition is checked here since it lives beside
# the other subcommands)
# ---------------------------------------------------------------------------


def test_run_json_includes_the_new_run_fields(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    r = doc["run"]
    for key in (
        "cards",
        "compare",
        "queue",
        "witness",
        "claims",
        "rulebook_version",
        "weights_version",
        "input_hash",
        "unbanded",
    ):
        assert key in r, key
    assert len(r["cards"]) == 5
