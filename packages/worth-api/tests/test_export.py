"""The serializer's invariants, checked against a fresh run.

These were the tests that guarded the committed ``run.json``. There is no
committed file any more; what they guard now is the payload the API sends.
The blinding test matters most: the raw 835 segments may name their payer,
because that is the partner's own remittance shown back to them, but no
computed value may.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from worth_api.export import EdiBundle, TableFile

if TYPE_CHECKING:
    from worth_api.export import DeliveredFile, RunResult
    from worth_complexity.pipeline import Run


def test_the_headline_numbers_match_the_run(result: RunResult, fresh: Run) -> None:
    assert result.summary.linkage_rate == pytest.approx(float(fresh.linkage.rate))
    assert result.summary.study == len(fresh.study)
    assert result.summary.comparator == len(fresh.comparator)
    assert result.summary.scored == len(fresh.adequacies)
    assert result.summary.excluded == len(fresh.extrapolated)


def test_the_export_names_the_rule_pack_that_produced_it(result: RunResult, fresh: Run) -> None:
    """A published figure names the exact rules behind it, forever."""
    assert result.run.digest == fresh.pack.digest
    assert result.run.status == fresh.pack.status


def test_every_study_encounter_is_exported_with_its_derivation(
    result: RunResult, fresh: Run
) -> None:
    assert len(result.encounters) == len(fresh.observations)
    for e in result.encounters:
        assert (e.ratio is None) == (e.expected is None)
        if e.cohort != "study":
            continue
        assert len(e.markers) == len(fresh.pack.markers)
        if e.ratio is not None:
            assert e.adequacy_trace, f"{e.id} carries a ratio but no arithmetic"


def test_comparator_derivations_are_not_shipped(result: RunResult) -> None:
    """They anchor the curve and are never opened; shipping them doubles the payload."""
    comparator = [e for e in result.encounters if e.cohort == "comparator"]
    assert comparator
    assert all(e.markers == [] for e in comparator)


def test_one_index_record_per_code(result: RunResult, fresh: Run) -> None:
    assert len(result.records) == len(fresh.records)
    for rec in result.records:
        assert rec.distribution.n == rec.n
        assert rec.institution and rec.period_start <= rec.period_end
        assert rec.slopes
        if rec.adequacy is not None:
            assert rec.adequacy_ci_low is not None and rec.adequacy_ci_high is not None
            assert rec.adequacy_ci_low < rec.adequacy < rec.adequacy_ci_high


def test_no_payer_is_named_in_any_computed_output(result: RunResult, fresh: Run) -> None:
    """The blinding has to hold in the artefact, not only in the console."""
    real = {o.payer_name for o in fresh.observations}
    computed = result.model_dump_json(by_alias=True)
    leaked = sorted(name for name in real if name in computed)
    assert not leaked, f"payer identity reaches the computed output: {leaked}"


def test_the_raw_delivered_files_are_shown_unaltered(files: list[DeliveredFile]) -> None:
    """The counterpart: nothing redacts the partner's own source data."""
    edi = next(f for f in files if isinstance(f, EdiBundle))
    segments = "\n".join(s for f in edi.files for s in f.segments)
    assert "N1*PR*" in segments


def test_no_figure_across_codes(result: RunResult) -> None:
    assert "cohortRatio" not in result.model_dump(by_alias=True)["summary"]


def test_the_delivered_files_are_exported_whole(files: list[DeliveredFile]) -> None:
    tables = [f for f in files if isinstance(f, TableFile)]
    edi = [f for f in files if isinstance(f, EdiBundle)]
    assert len(tables) == 5
    assert len(edi) == 1
    for table in tables:
        assert len(table.sha256) == 64
        assert all(len(row) == len(table.columns) for row in table.rows)
    assert edi[0].count == len(edi[0].files)
    assert all(f.segments for f in edi[0].files)


def test_the_curve_range_is_exported_so_the_app_cannot_extrapolate(result: RunResult) -> None:
    curve = result.schedule_curve
    assert curve.x_min < curve.x_max
    assert 0 <= curve.r2 <= 1
    assert curve.release == result.summary.reference_release
    assert len(curve.archive_sha256) == 64
    assert curve.locality == "NY01" and curve.setting == "facility"


def test_comparator_encounters_carry_their_fee_schedule_receipts(result: RunResult) -> None:
    """The denominator's inputs, per encounter: both PFS amounts and the archive hash."""
    for e in result.encounters:
        if e.cohort == "comparator":
            assert e.pfs is not None
            assert e.pfs.amount > 0 and e.pfs.reference_amount > 0
            assert len(e.pfs.archive_sha256) == 64
            assert e.ratio is None and e.expected is None
        else:
            assert e.pfs is None
            if e.ratio is not None:
                assert e.schedule_expected is not None and e.expected is not None
                assert e.expected != e.schedule_expected  # the multiplier was applied


def test_no_multiplier_reaches_an_index_record(result: RunResult, fresh: Run) -> None:
    """A payer's multiple of Medicare is a rate. It is shown to the partner in
    ``multipliers`` and the encounter trace, and appears in no record."""
    records = result.model_dump(by_alias=True)["records"]
    assert not any("multipl" in key.lower() for rec in records for key in rec)
    rendered = json.dumps(records)
    for m in fresh.multipliers.values():
        assert str(m.value) not in rendered
    assert result.multipliers and all(m.payer.startswith("Payer ") for m in result.multipliers)


def test_the_numerator_is_the_primary_line_and_the_claim_total_is_kept(
    result: RunResult,
) -> None:
    for e in result.encounters:
        assert e.realized <= e.realized_claim
    assert any(e.increased_service for e in result.encounters)


def test_every_step_names_a_stage_the_pipeline_reports(result: RunResult) -> None:
    """A step's timing comes from the stage it belongs to; an unknown stage has none."""
    assert {s.stage for s in result.steps} <= set(result.timings)


@pytest.fixture(scope="module")
def fresh(fixture_run: Run) -> Run:
    return fixture_run
