"""End to end, on the synthetic dataset that ships with the package."""

from __future__ import annotations

import dataclasses
import hashlib
from datetime import date
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
    from worth_complexity.population import QueueItem

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

    The denominator is now the Method 3 band median rather than a single fitted
    line (decision 2), so the tolerance is loosened from the old curve-based
    check: a band median over 11+ neighbouring comparators carries more sampling
    noise than a line fitted on the whole comparator cohort, but should still
    land near what the generator applied.
    """
    records = _by_code(result)
    for code in ("58558", "58563", "58570", "58661"):
        ratio = records[code].adequacy
        assert ratio is not None
        assert abs(ratio.value - GENERATOR_VALUATION_FACTOR) < Decimal("0.06"), code


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
        assert abs(ratio.value - GENERATOR_VALUATION_FACTOR) < Decimal("0.06"), code


def test_bands_no_longer_exclude_the_way_curve_extrapolation_did(
    result: Run,
) -> None:
    """Decision 2's band-widening replaces the old extrapolation guard.

    The Method 2 schedule curve used to refuse a point outside its fitted
    range, which excluded a study encounter from the index entirely and,
    depending on which end it fell off, biased its code's ratio. Method 3's
    band widens instead of refusing (``method3.band_expected``), so nothing
    the old guard used to catch is excluded on that account any more, and the
    only two exclusion reasons left are an unpriceable payer and a band that
    never reaches the suppression floor even at the full 0-100 width. On this
    fixture (300 study encounters, 5 codes) that band always finds it.
    """
    assert result.extrapolated == ()
    scored = {a.encounter_id for a in result.adequacies}
    excluded = set(result.unmultiplied) | set(result.unbanded)
    assert scored.isdisjoint(excluded)
    assert len(scored) + len(excluded) == len(result.study)
    assert excluded == set(), "the fixture is large enough that nothing should be excluded"


def test_the_schedule_curve_is_fitted_on_comparator_encounters_only(result: Run) -> None:
    """One curve, no payer in it, from the comparator cohort only: a curve that
    included the codes under study would define adequacy partly in terms of the
    thing being tested."""
    curve = result.schedule_curve
    assert curve is not None  # the fixture always has comparators to fit on
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
    assert all(rec.rule_pack_source == "packaged" for rec in result.records)


def test_publishable_requires_both_a_ratified_pack_and_a_packaged_source(result: Run) -> None:
    """The gate is two independent locks, not one. A ratified status alone is not
    enough: an external pack is capped to ``provisional`` on load (layer 2), but
    that cap is the loader refusing to trust the file, not the only thing standing
    between an external pack and a publishable record. ``IndexRecord.publishable``
    checks ``rule_pack_source`` itself, on any record however constructed."""
    rec = dataclasses.replace(
        result.records[0],
        suppressed=False,
        rule_pack_status="ratified",
        rule_pack_source="packaged",
    )
    assert rec.publishable

    external = dataclasses.replace(rec, rule_pack_source="external")
    assert not external.publishable

    unratified = dataclasses.replace(rec, rule_pack_status="provisional")
    assert not unratified.publishable


def test_one_record_per_study_code_and_nothing_across_them(result: Run) -> None:
    """A blended figure has no home in the methodology; the unit is the code."""
    assert {rec.code for rec in result.records} == {
        o.scored.encounter.primary_cpt for o in result.study
    }


def test_every_adequacy_carries_its_arithmetic(result: Run) -> None:
    for a in result.adequacies:
        body = "\n".join(a.trace)
        assert "realized / m3 expected" in body
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


# ------------------------------------------------- W3: claims, Method 1/3, signature,
# ------------------------------------------------- population, witness


def test_new_stages_fire_in_dependency_order() -> None:
    """Every new W3 stage name appears, in the order it actually executes.

    See ``pipeline.py``'s module docstring: the six new names are not
    literally appended after ``records`` the way the contract's prose reads,
    because ``Adequacy`` now embeds the Method 1/3/signature results and
    ``IndexRecord`` now carries its code's population card, so those stages
    have to run first. What is checked here is that every new stage name is
    present and that the whole sequence matches :data:`STAGES` exactly, since
    that constant is itself the contract this test enforces.
    """
    from worth_complexity.pipeline import STAGES

    seen: list[str] = []
    run(CLINICAL, REMITTANCE, locality=FIXTURE_LOCALITY, progress=seen.append)
    assert tuple(seen) == STAGES
    for name in ("claims", "method1", "method3", "signature", "population", "witness"):
        assert name in seen


def test_every_scored_encounter_carries_m3_spine_and_signature(result: Run) -> None:
    for a in result.adequacies:
        assert a.m3.expected == a.expected
        assert a.spine.m3_expected == a.m3.expected
        assert a.signature.pattern_id
        assert a.rulebook_version == result.rulebook_version
        assert a.weights_version == result.weights_version
        assert a.encounter_class == "surgical"


def test_the_ratio_per_code_still_lands_near_the_generator_s_assumption(result: Run) -> None:
    """Method 3's band median, not the Method 2 curve, is now the denominator
    (decision 2); the fixture README's tolerance for the recovered value
    widens accordingly (see the codes-inside-the-fitted-range test above)."""
    for rec in result.records:
        assert rec.adequacy is not None
        assert abs(rec.adequacy.value - GENERATOR_VALUATION_FACTOR) < Decimal("0.10"), rec.code


def test_58662_carries_mismatched_flags_where_58660_is_the_candidate(result: Run) -> None:
    """The adhesiolysis procedure rule's only candidate, 58660, is bundled
    with 58662 under NCCI (the rule pack's ``bundled_with`` list), so every
    matching statement on a 58662 encounter should land in ``mismatched``
    rather than ``missed``.

    58660 is in the committed worth-fees fixture, so every flag prices at
    PFS x the payer multiplier: what equivalent documented work earns where
    the fee schedule does pay for it.
    """
    flags = [f for a in result.adequacies for f in a.method1_flags if a.cpt == "58662"]
    mismatched = [f for f in flags if f.bucket == "mismatched"]
    assert mismatched
    assert all(f.candidate_codes == ("58660",) for f in mismatched)
    assert all(f.priced is not None and f.priced > 0 for f in mismatched)
    assert all("candidate 58660 priced" in f.pricing_trace[0] for f in mismatched)


def test_price_method1_flag_prices_the_first_candidate_that_succeeds() -> None:
    """The pricing mechanism ``pipeline._price_method1_flag`` implements:
    the first candidate the schedule can price wins, at PFS x the payer
    multiplier, and the trace names that candidate."""
    from worth_complexity.adequacy import Multiplier, fixture_schedules
    from worth_complexity.method1 import Method1Flag
    from worth_complexity.models import SourceRef
    from worth_complexity.pipeline import _price_method1_flag
    from worth_fees import PlaceOfService
    from worth_fees.sources import vintage_for

    flag = Method1Flag(
        encounter_id="X",
        statement="a separate step was performed",
        section="findings",
        rule_id="adhesiolysis",
        candidate_codes=("00000", "58660"),
        submitted_codes=(),
        bucket="missed",
        reason="test",
        vehicle_exists=True,
        evidence_strength="moderate",
        priced=None,
        pricing_trace=(),
        source_ref=SourceRef("x.txt", "0" * 64, 1, "chars 0-10"),
    )
    multiplier = Multiplier(
        payer_id="p",
        payer_label="Payer A",
        n=20,
        value=Decimal("2"),
        q1=Decimal("1"),
        q3=Decimal("3"),
        trace=(),
    )
    reference = vintage_for(2026, 4)
    priced = _price_method1_flag(
        flag,
        multiplier,
        locality=FIXTURE_LOCALITY,
        setting=PlaceOfService.FACILITY,
        reference=reference,
        pool=fixture_schedules,
    )
    # 00000 is not a priceable code and is passed over; 58660, the second
    # candidate, is the one that actually prices, and only its own trace is
    # kept (a discarded failure reason is not useful once a later candidate
    # succeeds).
    assert priced.priced is not None
    assert priced.priced > 0
    assert "candidate 58660 priced" in priced.pricing_trace[0]


def test_cards_has_five_entries(result: Run) -> None:
    assert len(result.cards) == 5
    assert [c.code for c in result.cards] == sorted(c.code for c in result.cards)


def test_compare_is_sorted_by_code(result: Run) -> None:
    assert len(result.compare) == 5
    assert [row.code for row in result.compare] == sorted(row.code for row in result.compare)
    assert all(row.encounter_class == "surgical" for row in result.compare)


def test_queue_is_non_empty_and_ranked(result: Run) -> None:
    from worth_complexity.method1 import STRENGTH_RANK

    assert result.queue

    def key(item: QueueItem) -> tuple[int, Decimal]:
        rank = STRENGTH_RANK.get(item.evidence_strength, 0) if item.evidence_strength else 0
        return (-rank, -item.code_percentile)

    keys = [key(item) for item in result.queue]
    assert keys == sorted(keys)


def test_witness_digest_is_stable_across_two_runs() -> None:
    """The same files, rule pack and package produce the identical witness
    digest byte for byte (decision 6) — the property that makes a published
    run independently checkable."""
    first = run(CLINICAL, REMITTANCE, locality=FIXTURE_LOCALITY)
    second = run(CLINICAL, REMITTANCE, locality=FIXTURE_LOCALITY)
    assert first.witness.digest == second.witness.digest
    assert first.witness.input_hash == second.witness.input_hash
    assert first.input_hash == second.input_hash


def test_input_hash_covers_clinical_remittance_and_claims_files(result: Run) -> None:
    assert result.input_hash == result.witness.input_hash
    assert len(result.input_hash) == 64


# ------------------------------------------------- CONTRACT-PACKS.md: class default
# ------------------------------------------------- pack selection, mismatch refusal


def test_pack_name_none_picks_the_packaged_pack_for_the_extract_s_class(result: Run) -> None:
    """``fixture_run`` is built with no ``pack_name`` (see ``conftest.py``);
    the surgical extract should have resolved to the packaged surgical pack
    via ``pipeline.CLASS_DEFAULT_PACKS``, not by any hard-coded default."""
    assert result.pack.encounter_class == "surgical"
    assert result.pack.rule_pack_id == "surgical"
    assert result.pack.source == "packaged"


def test_a_rule_pack_whose_class_does_not_match_the_extract_is_refused(tmp_path: Path) -> None:
    """decision 3 (CONTRACT-PACKS.md): a pack scores one class only. A copy
    of the surgical pack whose ``encounter_class`` was changed to ``visit``
    must be refused against the (still surgical) fixture extract, naming
    both classes."""
    import json

    from worth_complexity.models import RulePackError
    from worth_complexity.rulepack import _PACK_DIR

    doc = json.loads((_PACK_DIR / "surgical-v1.json").read_text())
    doc["encounter_class"] = "visit"
    mismatched = tmp_path / "surgical-mismatch-v1.json"
    mismatched.write_text(json.dumps(doc))

    with pytest.raises(RulePackError, match="visit") as exc_info:
        run(
            CLINICAL,
            REMITTANCE,
            locality=FIXTURE_LOCALITY,
            pack_name="surgical-mismatch-v1",
            pack_search=[tmp_path],
        )
    assert "surgical" in str(exc_info.value)


def test_a_pack_with_no_default_for_an_unbuilt_class_names_it() -> None:
    """``CLASS_DEFAULT_PACKS`` today only resolves classes whose reader
    exists; an unrecognised class with no packaged default is named rather
    than resolving to ``None`` silently reaching :func:`rulepack.load`."""
    from worth_complexity.pipeline import CLASS_DEFAULT_PACKS

    assert CLASS_DEFAULT_PACKS == {
        "surgical": "surgical-v1",
        "visit": "visit-em-v1",
        "episode": "episode-rpm-v1",
    }


# ------------------------------------------------- CONTRACT-PACKS.md decision 4:
# ------------------------------------------------- 'replaces' pricing


def test_price_method1_flag_with_replaces_prices_the_difference() -> None:
    """A work-rule flag whose candidate supersedes an already-billed code
    prices at ``(PFS(candidate) - PFS(billed)) x multiplier``, not the
    candidate's amount outright — decision 4, CONTRACT-PACKS.md. 58662 (PFS
    745.18 at NY01 facility, 2026Q4) replacing 58660 (PFS 725.85) is a
    positive, real difference."""
    from worth_complexity.adequacy import Multiplier, fixture_schedules
    from worth_complexity.method1 import Method1Flag
    from worth_complexity.models import SourceRef
    from worth_complexity.pipeline import _price_method1_flag
    from worth_fees import PlaceOfService
    from worth_fees.sources import vintage_for

    flag = Method1Flag(
        encounter_id="X",
        statement="the next level is supported",
        section="time attestation",
        rule_id="em-level-by-time",
        candidate_codes=("58662",),
        submitted_codes=("58660",),
        bucket="missed",
        reason="test",
        vehicle_exists=True,
        evidence_strength="strong",
        priced=None,
        pricing_trace=(),
        source_ref=SourceRef("facts", "", 0, "total_documented_minutes"),
        replaces="58660",
    )
    multiplier = Multiplier(
        payer_id="p", payer_label="Payer A", n=20,
        value=Decimal("2"), q1=Decimal("1"), q3=Decimal("3"), trace=(),
    )  # fmt: skip
    reference = vintage_for(2026, 4)
    priced = _price_method1_flag(
        flag,
        multiplier,
        locality=FIXTURE_LOCALITY,
        setting=PlaceOfService.FACILITY,
        reference=reference,
        pool=fixture_schedules,
    )
    assert priced.priced is not None
    # (745.18 - 725.85) x 2 = 38.66
    assert priced.priced == Decimal("38.66")
    assert "candidate 58662 priced" in priced.pricing_trace[0]
    assert "replaces 58660 priced" in priced.pricing_trace[0]


def test_price_method1_flag_with_replaces_floors_a_negative_difference_at_zero() -> None:
    """A "candidate" that prices lower than what was billed is not a missed
    charge; the priced amount must never go negative."""
    from worth_complexity.adequacy import Multiplier, fixture_schedules
    from worth_complexity.method1 import Method1Flag
    from worth_complexity.models import SourceRef
    from worth_complexity.pipeline import _price_method1_flag
    from worth_fees import PlaceOfService
    from worth_fees.sources import vintage_for

    flag = Method1Flag(
        encounter_id="X",
        statement="the next level is supported",
        section="time attestation",
        rule_id="em-level-by-time",
        candidate_codes=("58660",),  # cheaper than what was billed
        submitted_codes=("58662",),
        bucket="missed",
        reason="test",
        vehicle_exists=True,
        evidence_strength="strong",
        priced=None,
        pricing_trace=(),
        source_ref=SourceRef("facts", "", 0, "total_documented_minutes"),
        replaces="58662",
    )
    multiplier = Multiplier(
        payer_id="p", payer_label="Payer A", n=20,
        value=Decimal("2"), q1=Decimal("1"), q3=Decimal("3"), trace=(),
    )  # fmt: skip
    reference = vintage_for(2026, 4)
    priced = _price_method1_flag(
        flag,
        multiplier,
        locality=FIXTURE_LOCALITY,
        setting=PlaceOfService.FACILITY,
        reference=reference,
        pool=fixture_schedules,
    )
    assert priced.priced == Decimal("0.00")


# --------------------------------------------------------------------------
# trends (decision 7, CONTRACT-SEEDS.md) -- on the committed fixture, 12
# months of 2026.
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def policy_run() -> Run:
    return run(CLINICAL, REMITTANCE, locality=FIXTURE_LOCALITY, policy_date=date(2026, 7, 1))


def test_trends_cover_every_study_code_at_the_finest_publishable_granularity(
    result: Run,
) -> None:
    codes = {rec.code for rec in result.records}
    trend_codes = {s.code for s in result.trends}
    assert trend_codes == codes
    for series in result.trends:
        # Five cases a month never clear the suppression floor, so "auto"
        # granularity steps up to quarters, where at least half the points
        # do; the series still spans the fixture's whole year of 2026.
        assert series.granularity == "quarter"
        assert [p.period for p in series.points] == [f"2026-Q{q}" for q in range(1, 5)]
        clear = sum(1 for p in series.points if not p.suppressed)
        assert clear * 2 >= len(series.points)


def test_run_without_a_policy_date_reports_no_pre_or_post(result: Run) -> None:
    assert result.trends
    assert all(s.pre is None and s.post is None for s in result.trends)


def test_a_policy_date_splits_every_series_pre_and_post(policy_run: Run) -> None:
    assert policy_run.trends
    for series in policy_run.trends:
        assert series.policy_date == date(2026, 7, 1)
        assert series.pre is not None or series.post is not None


def test_class_run_trends_flatten_onto_run_trends(result: Run) -> None:
    flattened = tuple(s for c in result.classes for s in c.trends)
    assert result.trends == flattened
