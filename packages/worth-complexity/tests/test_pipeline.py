"""End to end, on the synthetic dataset that ships with the package."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from worth_complexity import SUPPRESSION_THRESHOLD, run
from worth_complexity.adequacy import PricingError
from worth_complexity.cli import FIXTURE, FIXTURE_LOCALITY

if TYPE_CHECKING:
    from worth_complexity.models import IndexRecord
    from worth_complexity.pipeline import Run

CLINICAL = FIXTURE / "clinical"
REMITTANCE = FIXTURE / "remittance"

# The generator undervalues the five study codes by this factor relative to what
# the fee schedule's fitted complexity relation pays. The pipeline is supposed to
# recover it.
GENERATOR_VALUATION_FACTOR = Decimal("0.70")

# Each synthetic payer pays this multiple of the Medicare fee schedule, and the
# pipeline is supposed to recover that too, from the comparator cohort alone.
GENERATOR_MULTIPLIERS = {
    "87726": Decimal("2.05"),
    "60054": Decimal("1.82"),
    "04412": Decimal("1.00"),
    "80141": Decimal("0.88"),
}


@pytest.fixture(scope="module")
def result(fixture_run: Run) -> Run:
    return fixture_run


def test_the_dataset_is_the_shape_the_partner_brief_describes(result: Run) -> None:
    assert len(result.study) == 300
    assert len({o.scored.encounter.primary_cpt for o in result.study}) == 5
    assert {o.scored.encounter.facility_npi for o in result.observations} == {"1234567893"}


def test_every_encounter_links_to_its_remittance(result: Run) -> None:
    """Happy path. Real data will not do this, which is why the rate is published."""
    assert result.linkage.rate == Decimal("1.0000")
    assert result.linkage.unlinked == ()
    assert result.linkage.orphan_lines == ()


def _by_code(result: Run) -> dict[str, IndexRecord]:
    return {rec.code: rec for rec in result.records}


def test_codes_inside_the_fitted_range_recover_the_undervaluation_the_generator_applied(
    result: Run,
) -> None:
    """The end-to-end check that matters.

    The generator sets each study code's payment to 70% of what the fee
    schedule's fitted complexity relation pays at the code's mean score, times
    the payer's multiple. Scoring, note reading, linkage, pricing, the fitted
    curve, the multiplier and the division are all independent of that
    assumption, so recovering 0.70 means the arithmetic between them holds.

    For every code whose cases all sit inside the curve's fitted range. The one
    that loses cases off the top is the subject of the next test.
    """
    records = _by_code(result)
    for code in ("58558", "58563", "58570", "58661"):
        ratio = records[code].adequacy
        assert ratio is not None
        assert abs(ratio.value - GENERATOR_VALUATION_FACTOR) < Decimal("0.03"), code


def test_low_complexity_codes_are_no_longer_distorted_by_the_intercept(result: Run) -> None:
    """The old per-payer curve, fitted on the same 835 dollars it was later
    divided into, put the two single-digit codes far below 0.70 because the
    fit was dominated by its intercept at the bottom of the range. The schedule
    curve is fitted on the comparator cohort's PFS amounts across its whole
    range, and the same two codes now read where the generator put them."""
    records = _by_code(result)
    for code in ("58558", "58563"):
        ratio = records[code].adequacy
        assert ratio is not None
        assert records[code].distribution.median < 15
        assert abs(ratio.value - GENERATOR_VALUATION_FACTOR) < Decimal("0.03"), code


def test_exclusions_bias_a_code_in_the_direction_of_the_end_it_loses(
    result: Run,
) -> None:
    """The extrapolation guard is right and its cost is not random.

    Encounters scoring outside the range the schedule curve was fitted on are
    excluded rather than extrapolated, and which end they fall off decides
    which way the code's ratio moves.

    A code that loses its hardest cases reads *higher* than the undervaluation
    the generator applied, toward adequacy, the direction that hides a finding.
    That is the one to watch: as a real cohort grows, the encounters most likely
    to sit above every comparator are the ones the argument rests on.
    """
    curve = result.schedule_curve
    scored = {a.encounter_id for a in result.adequacies}
    above: dict[str, int] = {}
    below: dict[str, int] = {}
    for obs in result.study:
        if obs.scored.encounter.encounter_id in scored:
            continue
        code = obs.scored.encounter.primary_cpt
        bucket = below if obs.score.value < curve.x_min else above
        bucket[code] = bucket.get(code, 0) + 1

    records = _by_code(result)
    assert above, "no code lost a case off the top; this test has nothing to check"
    for code in above:
        ratio = records[code].adequacy
        assert ratio is not None
        assert ratio.value > GENERATOR_VALUATION_FACTOR, code
    for code in below:
        ratio = records[code].adequacy
        assert ratio is not None
        assert ratio.value < GENERATOR_VALUATION_FACTOR, code


def test_the_schedule_curve_is_fitted_on_comparator_encounters_only(result: Run) -> None:
    """One curve, no payer in it, from the comparator cohort only: a curve that
    included the codes under study would define adequacy partly in terms of the
    thing being tested."""
    curve = result.schedule_curve
    assert curve.n == len(result.comparator)
    assert curve.curve_id == f"schedule/{result.reference.label}"
    assert curve.slope > 0
    assert set(result.priced) == {o.scored.encounter.encounter_id for o in result.comparator}


def test_every_comparator_amount_names_the_cms_archive_it_came_from(result: Run) -> None:
    """The denominator's receipts: each priced amount chains back to a hash a
    referee can re-download and re-check."""
    for p in result.priced.values():
        for derivation in (p.at_date, p.at_reference):
            chain = derivation.source.rvu.chain()
            assert chain[-1].role == "archive"
            assert len(chain[-1].sha256) == 64
        assert p.at_reference.rule_year == result.reference.rule_year
        assert p.at_reference.quarter == result.reference.quarter
        assert p.at_reference.locality == result.locality


def test_each_payer_s_multiple_of_the_schedule_is_recovered_from_its_comparators(
    result: Run,
) -> None:
    """The generator pays each payer a fixed multiple of Medicare. The
    multiplier is measured on that payer's comparator encounters alone, and the
    study cohort is never consulted, so getting it back means the contract
    level has been separated from the schedule's complexity relation."""
    assert set(result.multipliers) == set(GENERATOR_MULTIPLIERS)
    assert result.thin_payers == ()
    for payer, m in result.multipliers.items():
        assert m.n >= SUPPRESSION_THRESHOLD
        assert abs(m.value - GENERATOR_MULTIPLIERS[payer]) < Decimal("0.02"), payer
        assert m.q1 <= m.value <= m.q3


def test_the_multiplier_stays_with_the_partner(result: Run) -> None:
    """A payer's multiple of Medicare is a rate. It appears in the encounter
    trace so the institution can check its own number, and in nothing that
    leaves: no index record has a field for it."""
    from dataclasses import fields

    from worth_complexity.models import IndexRecord

    names = {f.name for f in fields(IndexRecord)}
    assert not any("multipl" in name for name in names)
    rendered = "\n".join(rec.render() for rec in result.records)
    for m in result.multipliers.values():
        assert str(m.value) not in rendered


def test_the_reference_release_is_the_one_in_force_on_the_latest_service_date(
    result: Run,
) -> None:
    latest = max(o.scored.encounter.service_date for o in result.observations)
    start, end = result.reference.effective
    assert start <= latest < end
    assert all(rec.reference_release == result.reference.label for rec in result.records)
    assert all(rec.locality == "NY01" and rec.setting == "facility" for rec in result.records)


def test_a_comparator_that_cannot_be_priced_stops_the_run() -> None:
    """A curve fitted on whichever comparators happened to price is a curve
    fitted on a cohort nobody chose. The refusal names the encounter."""
    with pytest.raises(PricingError, match="cannot be priced"):
        run(CLINICAL, REMITTANCE, locality="ZZ99")


def test_encounters_outside_the_fitted_range_are_excluded_not_extrapolated(result: Run) -> None:
    scored_ids = {a.encounter_id for a in result.adequacies}
    assert set(result.extrapolated).isdisjoint(scored_ids)
    assert len(result.adequacies) + len(result.extrapolated) == len(result.study)


def test_method_zero_is_fitted_per_code_and_payer(result: Run) -> None:
    """The output the methodology calls defensible from the first validated extract."""
    for rec in result.records:
        assert len(rec.slopes) >= 2
        for stratum in rec.slopes:
            assert stratum.payer_label.startswith("Payer ")
            if not stratum.suppressed:
                assert stratum.slope_interval.low < stratum.slope_interval.high


def test_a_flat_code_reports_an_interval_that_contains_zero(result: Run) -> None:
    """A code that does not differentiate should say so, not report a small
    non-zero point estimate as if it meant something.

    The generator pays every study code one flat amount, so no stratum can
    show payment rising with complexity. A 95% interval excludes zero by
    chance one time in twenty, and twenty strata are fitted here; a stray
    negative interval at the suppression floor is that chance, not a finding,
    and the test says so rather than pretending the interval promises more.
    """
    fitted = [s for rec in result.records for s in rec.slopes if not s.suppressed]
    assert fitted
    assert not any(s.differentiates and s.slope > 0 for s in fitted)
    assert sum(1 for s in fitted if s.differentiates) <= 2


def test_a_thin_stratum_is_withheld_rather_than_published(result: Run) -> None:
    """The partner's own governance commits to cell suppression below n = 11."""
    withheld = [s for rec in result.records for s in rec.slopes if s.suppressed]
    assert withheld
    assert all(s.n < SUPPRESSION_THRESHOLD for s in withheld)


def test_payers_are_blinded_in_every_published_field(result: Run) -> None:
    """ "The payment figure goes into a complexity ratio, blinded by payer, and
    never comes back out as a rate."""
    real = {o.payer_name for o in result.observations}
    labels = {s.payer_label for rec in result.records for s in rec.slopes}
    assert labels
    assert labels.isdisjoint(real)
    assert all(label.startswith("Payer ") for label in labels)


def test_each_record_carries_what_the_spec_says_it_must(result: Run) -> None:
    """Code, institution, time window, N, complexity distribution, confidence interval."""
    for rec in result.records:
        assert rec.code and rec.institution
        assert rec.period_start <= rec.period_end
        assert rec.n == rec.distribution.n
        assert rec.distribution.minimum <= rec.distribution.median <= rec.distribution.maximum
        assert rec.linkage_rate > 0
        assert rec.rule_pack_digest and rec.provenance_filter
        if rec.adequacy is not None:
            assert rec.adequacy_interval is not None


def test_no_record_is_publishable_while_the_pack_is_provisional(result: Run) -> None:
    assert not any(rec.publishable for rec in result.records)


def test_one_record_per_study_code_and_nothing_across_them(result: Run) -> None:
    """A blended figure has no home in the methodology; the unit is the code."""
    assert {rec.code for rec in result.records} == {
        o.scored.encounter.primary_cpt for o in result.study
    }


def test_every_adequacy_carries_its_arithmetic(result: Run) -> None:
    for a in result.adequacies:
        body = "\n".join(a.trace)
        assert "realized / expected" in body
        assert str(a.score.value) in body


def test_the_run_is_reproducible(result: Run) -> None:
    """I-1: a value published today must be re-derivable by someone trying to
    prove it wrong. A replay digest is the cheapest possible guard on that."""

    def digest(r: Run) -> str:
        rows = sorted(
            f"{a.encounter_id}|{a.cpt}|{a.score.value}|{a.realized}|{a.expected}|{a.ratio.value}"
            for a in r.adequacies
        )
        return hashlib.sha256("\n".join(rows).encode()).hexdigest()

    assert digest(result) == digest(run(CLINICAL, REMITTANCE, locality=FIXTURE_LOCALITY))


def test_the_engine_touches_nothing_outside_the_two_directories() -> None:
    """The property that makes 'run our code in your environment' a deployment
    target rather than a rewrite."""
    import worth_complexity

    root = Path(worth_complexity.__file__).parent
    banned = {"boto3", "botocore", "requests", "urllib.request", "socket", "http.client"}
    for path in root.rglob("*.py"):
        body = path.read_text()
        for name in banned:
            assert f"import {name}" not in body, f"{path.name} imports {name}"


# ------------------------------------------------- the narrative lane, on real data


def test_the_dataset_carries_the_notes_layer_a_reads(result: Run) -> None:
    from worth_complexity import cases

    extract = cases.read_extract(CLINICAL)
    assert extract.notes, "Layer A reads narrative; a dataset without notes cannot test it"
    assert {n.note_type for n in extract.notes} == {"operative", "discharge"}
    operative = {n.encounter_id for n in extract.notes if n.note_type == "operative"}
    assert operative == {o.scored.encounter.encounter_id for o in result.observations}


def test_every_encounter_is_scored_on_both_lanes(result: Run) -> None:
    for obs in result.observations:
        lanes = {m.provenance for m in obs.scored.markers}
        assert lanes == {"structured", "rule"}, obs.scored.encounter.encounter_id


def test_narrative_markers_quote_the_text_they_fired_on(result: Run) -> None:
    """A rule pack digest does not let anyone check the rule fired on the right
    sentence. The span and the sentence do."""
    quoted = 0
    for obs in result.observations:
        for m in obs.scored.markers:
            if m.provenance != "rule" or m.value == 0:
                continue
            assert m.evidence, f"{m.marker_id} on {m.encounter_id} cites nothing"
            assert m.source_ref.file.endswith(".txt")
            assert m.source_ref.column.startswith("chars ")
            quoted += 1
    assert quoted > 0


def test_the_note_that_restates_the_operative_time_is_displaced_by_optime(
    result: Run,
) -> None:
    displaced = {m.marker_id for obs in result.observations for m in obs.scored.superseded}
    assert "operative_minutes" in displaced
    assert all(m.provenance == "rule" for obs in result.observations for m in obs.scored.superseded)


def test_no_model_lane_marker_reaches_a_layer_a_score(result: Run) -> None:
    """The property the whole provenance scheme exists to guarantee."""
    for obs in result.observations:
        assert not [m for m in obs.scored.markers if m.provenance in {"ml", "generative"}]


def test_the_structured_subset_is_a_different_number(result: Run) -> None:
    """If the two filters agreed, the note reading would not be doing anything."""
    from worth_complexity import LAYER_A_STRUCTURED_ONLY, cases, markers, rulepack, scoring

    pack = rulepack.load()
    extract = cases.read_extract(CLINICAL)
    encounters = cases.encounters(extract)
    sets = markers.extract(extract, encounters, pack)
    subset = {
        e.encounter_id: scoring.score(
            e, sets[e.encounter_id], pack, LAYER_A_STRUCTURED_ONLY
        ).score.value
        for e in encounters
    }
    full = {o.scored.encounter.encounter_id: o.score.value for o in result.observations}
    assert any(subset[k] != full[k] for k in full)
