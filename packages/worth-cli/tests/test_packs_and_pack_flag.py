"""``worth-cli packs`` and the ``--pack``/``--rulepack-dir`` flags (layer 2).

No conftest.py here for the same reason ``test_w3_subcommands.py`` has none:
mypy's ``strict`` config checks every package's ``tests`` directory with no
``__init__.py`` markers, and two files named ``conftest.py`` would collide as
the same top-level module.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from worth_cli.cli import main
from worth_complexity.rulepack import _PACK_DIR

SHIPPED_PACK = _PACK_DIR / "surgical-v1.json"


def test_packs_lists_the_packaged_pack_as_a_table(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["packs"]) == 0
    out = capsys.readouterr().out
    assert "surgical-v1" in out
    assert "packaged" in out


def test_packs_json_carries_the_packaged_pack(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["packs", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    packs = doc["packs"]
    assert any(p["name"] == "surgical-v1" and p["source"] == "packaged" for p in packs)


def test_packs_with_rulepack_dir_also_lists_an_external_pack(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    candidate = tmp_path / "candidate-v9.json"
    candidate.write_bytes(SHIPPED_PACK.read_bytes())

    assert main(["packs", "--rulepack-dir", str(tmp_path), "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    packs = doc["packs"]
    match = next(p for p in packs if p["name"] == "candidate-v9")
    assert match["source"] == "external"
    assert match["path"] == str(candidate.resolve())


def test_run_with_a_pack_path_reports_an_external_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--pack`` given a path that exists is loaded directly, not resolved by name;
    the text report's banner names both the source and the path (Meridian and any
    other caller of the text report can see where the pack came from)."""
    candidate = tmp_path / "candidate-v9.json"
    candidate.write_bytes(SHIPPED_PACK.read_bytes())

    assert main(["run", "--pack", str(candidate)]) == 0
    out = capsys.readouterr().out
    assert f"source: external {candidate.resolve()}" in out


def test_score_json_carries_rule_pack_source_and_path_for_an_external_pack(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    candidate = tmp_path / "candidate-v9.json"
    candidate.write_bytes(SHIPPED_PACK.read_bytes())

    assert main(["score", "--pack", str(candidate)]) == 0
    first_line = capsys.readouterr().out.splitlines()[0]
    doc = json.loads(first_line)
    assert doc["rule_pack_source"] == "external"
    assert doc["rule_pack_path"] == str(candidate.resolve())


def test_a_pack_value_that_is_not_a_file_is_treated_as_a_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["run", "--pack", "surgical-v1"]) == 0
    out = capsys.readouterr().out
    assert "source: packaged" in out


def test_a_pack_name_shadowing_a_packaged_one_is_refused(tmp_path: Path) -> None:
    (tmp_path / "surgical-v1.json").write_bytes(SHIPPED_PACK.read_bytes())
    with pytest.raises(Exception, match=r"surgical-v1\.json"):
        main(["run", "--pack", "surgical-v1", "--rulepack-dir", str(tmp_path)])
