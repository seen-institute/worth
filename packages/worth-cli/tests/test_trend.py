"""``worth-cli trend`` — ratio over time, per code (decision 7, CONTRACT-SEEDS.md).

Same dataset flags and three formats (``--json`` default, ``--table``,
``--csv``) as every other W3/S2 view; see ``test_w3_subcommands.py`` for the
shared fixture-run rationale this file borrows.
"""

from __future__ import annotations

import json
from datetime import date
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
    """A pre-built ``Run`` to check the CLI's own output against. ``main``
    always builds its own ``Run``, so this exists only so a test can look up
    a code without shelling out through ``main`` first."""
    return pipeline_run(FIXTURE / "clinical", FIXTURE / "remittance", locality=FIXTURE_LOCALITY)


@pytest.fixture(scope="module")
def sample_code(fixture_run: Run) -> str:
    return fixture_run.trends[0].code


def test_trend_json_is_one_series_per_code(
    fixture_run: Run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["trend"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert set(doc) >= VERSION_KEYS
    assert len(doc["series"]) == len(fixture_run.trends)
    series = doc["series"][0]
    # The fixture's 60 cases per code are five a month, so "auto" granularity
    # steps up to quarters, where every point clears the suppression floor.
    assert len(series["points"]) == 4
    assert series["granularity"] == "quarter"
    assert all(not p["suppressed"] for p in series["points"])


def test_trend_filters_to_one_code(sample_code: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["trend", sample_code]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert [s["code"] for s in doc["series"]] == [sample_code]


def test_trend_table_lists_period_columns(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["trend", "--table"]) == 0
    out = capsys.readouterr().out
    assert "period" in out
    assert "ratio" in out
    assert "2026-Q1" in out


def test_trend_csv_has_a_version_comment(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["trend", "--csv"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# rulebook_version=")


def test_trend_by_payer_breaks_points_out_per_payer(
    sample_code: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["trend", sample_code, "--by", "payer", "--table"]) == 0
    out = capsys.readouterr().out
    assert "payer" in out


def test_trend_with_a_policy_date_reports_pre_and_post(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["trend", "--policy-date", "2026-07-01", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    series = doc["series"][0]
    assert series["policy_date"] == "2026-07-01"
    assert series["pre"] is not None or series["post"] is not None


def test_trend_with_no_policy_date_reports_none_for_pre_and_post(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["trend", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    series = doc["series"][0]
    assert series["pre"] is None
    assert series["post"] is None


def test_run_json_includes_trends(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert "trends" in doc["run"]
    assert doc["run"]["trends"]


def test_a_hand_parsed_policy_date_matches_the_pipelines_own() -> None:
    """Sanity check the CLI's own date parsing against the engine's, so a
    future refactor of either can't silently disagree about the format."""
    r = pipeline_run(
        FIXTURE / "clinical",
        FIXTURE / "remittance",
        locality=FIXTURE_LOCALITY,
        policy_date=date(2026, 7, 1),
    )
    assert r.trends[0].policy_date == date(2026, 7, 1)
