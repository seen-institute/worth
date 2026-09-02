"""Fixture integrity, copyright hygiene, and parser strictness."""

from __future__ import annotations

import csv
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from worth_fees import fees, sources
from worth_fees.models import SourceIntegrityError, VintageError
from worth_fees.provenance import sha256_file
from worth_fees.sources import (
    FIXTURE_DIR,
    MANIFEST_NAME,
    PINNED_VINTAGES,
    load,
    parse_gpci,
    parse_pprrvu,
    vintage_for,
)

VINTAGE = PINNED_VINTAGES[(2026, 1)]
FIXTURE_RVU = FIXTURE_DIR / f"pprrvu-{VINTAGE.key}.csv"
FIXTURE_GPCI = FIXTURE_DIR / f"gpci-{VINTAGE.key}.csv"

# Every committed RVU fixture, both payment bases and all four quarters. The
# copyright guards below run over all of them: each is cut from a CMS file that
# does carry descriptors, so testing only one would leave seven unchecked.
ALL_RVU_FIXTURES = sorted(FIXTURE_DIR.glob("pprrvu*-2026q*.csv"))


def _data_rows(path: Path, header_cell: str) -> list[list[str]]:
    rows = list(csv.reader(path.read_text(encoding="latin-1").splitlines()))
    header_at = next(i for i, r in enumerate(rows) if any(header_cell in c.lower() for c in r))
    return [r for r in rows[header_at + 1 :] if r and r[0].strip()]


# ---------------------------------------------------------------------------
# Copyright hygiene. This repository is public; CPT descriptors are
# AMA-copyrighted and must never appear in it.
# ---------------------------------------------------------------------------


def test_every_rvu_fixture_is_committed() -> None:
    """Guards the guards: if the glob stops matching, the tests below pass
    vacuously and descriptors could ship unnoticed."""
    assert len(ALL_RVU_FIXTURES) == 8, [p.name for p in ALL_RVU_FIXTURES]


@pytest.mark.parametrize("fixture", ALL_RVU_FIXTURES, ids=lambda p: p.name)
def test_committed_fixture_has_no_cpt_descriptors(fixture: Path) -> None:
    rows = _data_rows(fixture, "hcpcs")
    assert rows, f"{fixture.name} has no data rows to check"
    for row in rows:
        assert row[sources._DESCRIPTION] == "", (
            f"{fixture.name}: {row[0]} still carries a descriptor"
        )


@pytest.mark.parametrize("fixture", ALL_RVU_FIXTURES, ids=lambda p: p.name)
def test_committed_fixture_data_rows_contain_no_prose(fixture: Path) -> None:
    """A blunt second guard: CMS data cells are digits and uppercase codes only.

    Any lowercase letter below the header block means descriptor text, or
    some other free text, has leaked into a file we publish.
    """
    for row in _data_rows(fixture, "hcpcs"):
        joined = ",".join(row)
        assert joined == joined.upper(), f"lowercase text in {fixture.name} row: {row[0]}"


def test_parser_does_not_expose_descriptors() -> None:
    """Even given a file with descriptors, the parsed rows must not carry them."""
    rows, _ = parse_pprrvu(FIXTURE_RVU.read_text(encoding="latin-1"))
    sample = next(iter(rows.values()))
    assert not hasattr(sample, "description")
    assert "description" not in set(sample.__slots__)


# ---------------------------------------------------------------------------
# Fixture integrity
# ---------------------------------------------------------------------------


def test_fixtures_match_their_manifest_hashes() -> None:
    schedule = load(2026, 1)
    assert schedule.sources.rvu.sha256 == sha256_file(FIXTURE_RVU)
    assert schedule.sources.gpci.sha256 == sha256_file(FIXTURE_GPCI)


def test_provenance_chains_back_to_the_cms_archive() -> None:
    schedule = load(2026, 1)
    for source in schedule.sources.files:
        chain = source.chain()
        assert len(chain) == 3, "fixture -> CMS member -> CMS archive"
        assert chain[-1].filename == VINTAGE.archive_filename
        assert chain[-1].sha256 == VINTAGE.archive_sha256
        assert all(len(link.sha256) == 64 for link in chain)


def test_edited_fixture_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hand-editing a fixture without rebuilding its manifest must fail loudly."""
    for name in (FIXTURE_RVU.name, FIXTURE_GPCI.name, MANIFEST_NAME):
        shutil.copy(FIXTURE_DIR / name, tmp_path / name)
    tampered = tmp_path / FIXTURE_RVU.name
    tampered.write_text(
        tampered.read_text(encoding="latin-1").replace("1.30", "9.99"), encoding="latin-1"
    )
    monkeypatch.setattr(sources, "FIXTURE_DIR", tmp_path)

    with pytest.raises(SourceIntegrityError, match="does not match the manifest"):
        load(2026, 1)


def test_unpinned_vintage_is_refused() -> None:
    with pytest.raises(VintageError, match="no pinned CMS vintage"):
        vintage_for(1999, 1)


def test_pinned_vintage_round_trips() -> None:
    assert vintage_for(2026, 1) is VINTAGE
    assert VINTAGE.archive_filename == "rvu26a-updated-12-29-2025.zip"
    assert len(VINTAGE.archive_sha256) == 64


# ---------------------------------------------------------------------------
# Parser strictness
# ---------------------------------------------------------------------------


def test_pprrvu_requires_a_header_row() -> None:
    with pytest.raises(SourceIntegrityError, match="no HCPCS header row"):
        parse_pprrvu("a,b,c\n1,2,3\n")


def test_pprrvu_rejects_an_unexpected_column_count() -> None:
    header = ",".join(["HCPCS", "MOD", "DESCRIPTION", "CODE"])
    with pytest.raises(SourceIntegrityError, match="columns, expected 32"):
        parse_pprrvu(header + "\n")


def test_pprrvu_rejects_multiple_conversion_factors() -> None:
    text = FIXTURE_RVU.read_text(encoding="latin-1")
    text = text.replace("33.4009", "33.5675", 1)
    with pytest.raises(SourceIntegrityError, match="exactly one conversion factor"):
        parse_pprrvu(text)


def test_gpci_requires_a_locality_header() -> None:
    with pytest.raises(SourceIntegrityError, match="Locality Number"):
        parse_gpci("a,b,c\n1,2,3\n")


def test_gpci_locates_columns_by_name_not_position() -> None:
    """Reordering the GPCI columns must not change the parsed values."""
    rows = list(csv.reader(FIXTURE_GPCI.read_text(encoding="latin-1").splitlines()))
    header_at = next(
        i for i, r in enumerate(rows) if any("locality number" in c.lower() for c in r)
    )
    reordered = [r[::-1] if len(r) == len(rows[header_at]) else r for r in rows]
    text = "\n".join(",".join(f'"{c}"' for c in r) for r in reordered)

    original = parse_gpci(FIXTURE_GPCI.read_text(encoding="latin-1"))
    assert parse_gpci(text) == original


def test_conversion_factor_comes_from_the_file_not_a_constant() -> None:
    schedule = load(2026, 1)
    text = FIXTURE_RVU.read_text(encoding="latin-1")
    assert str(schedule.conversion_factor) in text


# ---------------------------------------------------------------------------
# CMS checking our homework
# ---------------------------------------------------------------------------


def test_components_reproduce_cms_own_totals() -> None:
    """CMS publishes its own RVU totals; ours must equal them.

    This is the closest thing to an independent oracle that does not require
    leaving the file: if our work, PE and MP picks do not sum to CMS's own
    TOTAL columns, we are reading the wrong columns.
    """
    rows = list(csv.reader(FIXTURE_RVU.read_text(encoding="latin-1").splitlines()))
    header_at = next(i for i, r in enumerate(rows) if r and r[0].strip() == "HCPCS")
    schedule = load(2026, 1)

    checked = 0
    for row in rows[header_at + 1 :]:
        if not row or not row[0].strip():
            continue
        parsed = schedule.rvus[(row[0].strip().upper(), row[1].strip().upper())]
        assert parsed.work_rvu + parsed.pe_rvu_nonfacility + parsed.mp_rvu == Decimal(row[11])
        assert parsed.work_rvu + parsed.pe_rvu_facility + parsed.mp_rvu == Decimal(row[12])
        checked += 1
    assert checked == len(schedule.rvus)


def test_a_shifted_column_is_caught_by_the_cross_check() -> None:
    """Corrupt one component and CMS's own total no longer agrees."""
    text = FIXTURE_RVU.read_text(encoding="latin-1")
    # 99213: work 1.30, non-facility PE 1.46, MP 0.09, CMS total 2.85.
    tampered = text.replace("99213,,,A,,1.30,1.46,", "99213,,,A,,1.31,1.46,")
    assert tampered != text

    with pytest.raises(SourceIntegrityError, match="CMS's own"):
        parse_pprrvu(tampered)


def test_payable_status_codes_do_not_drift_between_modules() -> None:
    """``sources`` needs to know which statuses are payable, to check that the
    two payment bases cover the same codes. ``fees`` needs the same fact to
    decide what to price.

    The constant is duplicated rather than shared: ingest must not import the
    pricing layer, and one small frozenset in two places is cheaper than a
    module that exists only to hold it. This is the test that makes the
    duplication safe, the two must partition the status codes CMS actually
    publishes, with nothing in both and nothing in neither.
    """
    payable = set(sources._PAYABLE_STATUS)
    non_payable = set(fees._NON_PAYABLE_STATUS)

    assert not (payable & non_payable), "a status code cannot be both payable and not"

    published = {row.status_code for row in load(2026, 1).rvus.values()}
    for quarter in (2, 3, 4):
        published |= {row.status_code for row in load(2026, quarter).rvus.values()}
    assert published <= payable | non_payable, (
        f"CMS publishes status code(s) {sorted(published - payable - non_payable)} that "
        "neither module classifies; pricing would fall through to an unhandled case."
    )
