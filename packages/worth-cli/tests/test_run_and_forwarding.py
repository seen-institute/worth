"""worth-cli's own commands: run, run --json, and forwarding to price and db."""

from __future__ import annotations

import json

import pytest
from worth_cli.cli import main
from worth_complexity.cli import FILTERS, FIXTURE, FIXTURE_LOCALITY, cmd_report
from worth_complexity.pipeline import run as pipeline_run
from worth_fees import PlaceOfService


def test_run_prints_the_same_report_as_worth_complexity_cmd_report(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["run"]) == 0
    captured = capsys.readouterr().out

    expected = cmd_report(
        pipeline_run(
            FIXTURE / "clinical",
            FIXTURE / "remittance",
            locality=FIXTURE_LOCALITY,
            setting=PlaceOfService.FACILITY,
            provenance_filter=FILTERS["layer-a"],
        )
    )
    assert captured.rstrip("\n") == expected.rstrip("\n")


def test_run_json_parses_and_carries_the_version_keys(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["run", "--json"]) == 0
    raw = capsys.readouterr().out
    doc = json.loads(raw)
    assert set(doc) == {
        "worth_cli_version",
        "worth_complexity_version",
        "worth_fees_version",
        "run",
    }
    assert doc["run"]["records"]
    assert "NaN" not in raw
    assert "Infinity" not in raw


def test_run_json_is_deterministic_across_two_invocations(
    capsys: pytest.CaptureFixture[str],
) -> None:
    main(["run", "--json"])
    first = capsys.readouterr().out
    main(["run", "--json"])
    second = capsys.readouterr().out
    assert first == second
    assert json.loads(first) == json.loads(second)


def test_run_json_contains_no_float_type_anywhere_money_is_involved(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every Decimal in the run crosses as a string; json.loads never hands back a float
    for anything that started life as money, because the fixture data has no raw floats."""
    main(["run", "--json"])
    doc = json.loads(capsys.readouterr().out)

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        else:
            assert not isinstance(node, float), "a float leaked into the JSON document"

    walk(doc)


def test_price_forwards_to_worth_fees_and_behaves_identically(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["price", "99213", "CA18", "2026-03-14", "--amount"]) == 0
    out = capsys.readouterr().out.strip()
    assert float(out) > 0  # a bare decimal amount, worth-fees' own --amount output


def test_price_help_forwards_to_worth_fees_own_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["price", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "worth-fees price" in out


def test_db_help_forwards_to_worth_db_own_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["db", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "worth-db" in out
    assert "migrate" in out


def test_version_prints_all_four_packages(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    out = capsys.readouterr().out
    for name in ("worth-cli", "worth-fees", "worth-complexity", "worth-db"):
        assert name in out


def test_top_level_help_lists_all_four_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for name in ("run", "price", "db", "version"):
        assert name in out
