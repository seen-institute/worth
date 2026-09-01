"""The SQL export, and keeping the Postgres schema honest about the Python.

None of these need a database. They check the emitted script, and — more
importantly — that the migration and the Python cannot silently drift apart on
the two things where a divergence would produce different money.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from worth_fees import fees
from worth_fees.models import CodeSystem
from worth_fees.sources import PINNED_VINTAGES, load
from worth_fees.sql import export

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "0001_fee_schedule.sql"
VINTAGE = PINNED_VINTAGES[(2026, 1)]


@pytest.fixture(scope="module")
def script() -> str:
    return export(load(2026, 1))


# ---------------------------------------------------------------------------
# The emitted script
# ---------------------------------------------------------------------------


def test_script_is_a_single_transaction(script: str) -> None:
    assert script.count("BEGIN;") == 1
    assert script.rstrip().endswith("COMMIT;")


def test_release_row_carries_the_pinned_hash(script: str) -> None:
    assert VINTAGE.archive_sha256 in script
    assert VINTAGE.url in script
    assert "daterange('2026-01-01', '2026-04-01', '[)')" in script


def test_copy_blocks_hold_every_row(script: str) -> None:
    schedule = load(2026, 1)
    blocks = re.findall(r"COPY (\w+) \([^)]*\) FROM STDIN;\n(.*?)\n\\\.", script, re.DOTALL)
    counts = {table: len(body.splitlines()) for table, body in blocks}
    assert counts == {"rvu": len(schedule.rvus), "gpci": len(schedule.gpcis)}


def test_provenance_chain_is_deduplicated(script: str) -> None:
    """Both files descend from one archive, which is one row, not two."""
    inserts = script[script.index("INSERT INTO source_file") : script.index("COPY rvu")]
    # Count rows whose *role* is 'archive', not references to it as a parent.
    assert len(re.findall(r"\(\s*'[^']+',\s*'archive',", inserts)) == 1
    for role in ("pprrvu_fixture", "gpci_fixture", "pprrvu", "gpci"):
        assert f"'{role}'" in inserts


def test_copy_cells_are_escaped(script: str) -> None:
    """COPY text format is tab-delimited: no cell may contain a raw tab."""
    blocks = re.findall(r"FROM STDIN;\n(.*?)\n\\\.", script, re.DOTALL)
    assert blocks
    for body in blocks:
        for line in body.splitlines():
            cells = line.split("\t")
            assert all("\n" not in c and "\r" not in c for c in cells)
            assert len(cells) >= 11


def test_no_cpt_descriptors_reach_the_sql(script: str) -> None:
    """The export must not leak what the parser never read."""
    blocks = re.findall(r"COPY rvu[^;]*;\n(.*?)\n\\\.", script, re.DOTALL)
    assert blocks
    # Columns straight from CMS. code_system (index 3) is ours and is lowercase.
    cms_columns = (0, 1, 2, 4, 5)
    for body in blocks:
        for line in body.splitlines():
            cells = line.split("\t")
            payload = "".join(cells[i] for i in cms_columns)
            assert payload == payload.upper(), f"prose in exported row: {line[:60]}"


# ---------------------------------------------------------------------------
# Schema / Python agreement
# ---------------------------------------------------------------------------


def test_migration_and_python_price_the_same_modifiers() -> None:
    """The view's modifier filter must match what expected_allowed() will price.

    If these drift, Postgres and Python return different money for the same
    code, which is the single worst failure this project can have.
    """
    sql = MIGRATION.read_text()
    match = re.search(r"AND r\.modifier IN \(([^)]*)\)", sql)
    assert match, "the view no longer filters on modifier"
    in_sql = {m.strip().strip("'") for m in match.group(1).split(",")}
    assert in_sql == set(fees._RVU_SELECTING) | {""}


def test_migration_uses_exact_numeric_types_only() -> None:
    # Strip -- comments: the header explains why these types are banned.
    ddl = "\n".join(line.split("--")[0] for line in MIGRATION.read_text().splitlines())
    assert not re.search(r"\b(real|double precision|float[48]?)\b", ddl, re.IGNORECASE)
    assert "numeric(" in ddl


def test_migration_defines_banker_rounding() -> None:
    """Postgres round() is half-away-from-zero; the schema must not rely on it."""
    sql = MIGRATION.read_text()
    assert "CREATE FUNCTION round_half_even" in sql
    assert "round_half_even(" in sql[sql.index("CREATE VIEW allowed_amount") :]


def test_code_system_values_match_the_schema_check() -> None:
    sql = MIGRATION.read_text()
    match = re.search(r"code_system IN \(([^)]*)\)", sql)
    assert match
    in_sql = {m.strip().strip("'") for m in match.group(1).split(",")}
    assert in_sql == {member.value for member in CodeSystem}


# ---------------------------------------------------------------------------
# Code system classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected", "ama"),
    [
        ("99213", CodeSystem.CPT_I, True),
        ("0001F", CodeSystem.CPT_II, True),
        ("0042T", CodeSystem.CPT_III, True),
        ("G0008", CodeSystem.HCPCS_II, False),
        ("J1885", CodeSystem.HCPCS_II, False),
        ("GXXX1", CodeSystem.UNKNOWN, False),
    ],
)
def test_code_system_classification(code: str, expected: CodeSystem, ama: bool) -> None:
    assert CodeSystem.classify(code) is expected
    assert expected.is_ama_copyrighted is ama
