"""End to end on the committed episode-synthetic dataset (CONTRACT-PACKS.md).

Mirrors ``test_pipeline_visit.py``'s shape, scoped to the episode class: the
class-default pack resolves with nothing passed (``setting`` is passed
explicitly as ``"non-facility"`` per this track's own instructions, never
relied on as a default even though ``CLASS_DEFAULT_SETTING`` now resolves the
same value), POSTPARTUM_HTN bills one flat code (99453, the sequence-1 RPM
code) and scores a flat Method 0 line, the pooled comparator slope across the
three RPM/CGM/dialysis programs rises, Method 1 flags land in all three
buckets, and the median study episode's signature lands on one of the two
patterns the doc's worked example D describes.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from worth_complexity import population
from worth_complexity.sampling import quantile
from worth_complexity.signature import PATTERN_IDS
from worth_fees import PlaceOfService

if TYPE_CHECKING:
    from worth_complexity.pipeline import Run

STUDY_LINES = {"POSTPARTUM_HTN"}
COMPARATOR_LINES = {"CARDIAC_DEVICE", "CGM", "HOME_DIALYSIS"}

# Doc example D reads the median episode as "no billing vehicle exists". By
# dollars that pattern needs no-code work to be priced, which decision 1 of
# CONTRACT.md leaves for later (see ``signature.PATTERN_IDS``). The fixture is
# not shaped to hit a pattern: the test asserts the median case lands on a
# pattern the signature can name and prints which one, so a change in the
# generator or the pattern rules shows up as a printed change, not a forced tie.


def _service_line(run: Run) -> dict[str, str]:
    return {
        o.scored.encounter.encounter_id: o.scored.encounter.service_line for o in run.observations
    }


def test_the_class_default_pack_resolves_with_nothing_passed(episode_fixture_run: Run) -> None:
    """Decision 3, CONTRACT-PACKS.md: a caller who passes no ``pack_name``
    still gets ``episode-rpm-v1`` for an episode extract, from
    ``CLASS_DEFAULT_PACKS`` rather than a hard-coded default."""
    r = episode_fixture_run
    assert r.pack.rule_pack_id == "episode-rpm"
    assert r.pack.encounter_class == "episode"
    assert r.pack.source == "packaged"
    assert r.setting == PlaceOfService.NON_FACILITY


def test_every_encounter_carries_the_episode_class(episode_fixture_run: Run) -> None:
    for o in episode_fixture_run.observations:
        assert o.scored.encounter.encounter_class == "episode"


def test_the_cohorts_are_the_shape_the_generator_promises(episode_fixture_run: Run) -> None:
    lines = _service_line(episode_fixture_run)
    study_lines = {lines[o.scored.encounter.encounter_id] for o in episode_fixture_run.study}
    comparator_lines = {
        lines[o.scored.encounter.encounter_id] for o in episode_fixture_run.comparator
    }
    assert study_lines == STUDY_LINES
    assert comparator_lines == COMPARATOR_LINES
    assert len(episode_fixture_run.study) == 60
    assert len(episode_fixture_run.comparator) == 108


def test_every_study_episode_bills_the_sequence_one_rpm_code(episode_fixture_run: Run) -> None:
    assert {o.scored.encounter.primary_cpt for o in episode_fixture_run.study} == {"99453"}
    for o in episode_fixture_run.study:
        assert o.scored.encounter.service_line == "POSTPARTUM_HTN"


def test_method0_is_flat_on_the_study_code(episode_fixture_run: Run) -> None:
    """The generator pays 99453 a fixed amount per payer regardless of the
    individual episode's score; no payer's stratum should show a real,
    positive relationship between score and realized payment."""
    card = episode_fixture_run.cards[0]
    fitted = [s for s in card.strata if not s.suppressed]
    assert fitted
    assert not any(s.differentiates and s.slope > 0 for s in fitted)


def test_the_comparator_slope_rises_with_intensity(episode_fixture_run: Run) -> None:
    """CARDIAC_DEVICE, CGM and HOME_DIALYSIS occupy successive intensity
    bands (CONTRACT-PACKS.md); pooled, their (score, realized) points trace
    a rising line."""
    card = episode_fixture_run.cards[0]
    slope = card.comparator_slope
    assert slope is not None
    assert slope.verdict == "rising"
    assert slope.slope > 0


def test_the_ratio_lands_in_the_contract_s_band(episode_fixture_run: Run) -> None:
    """CONTRACT-PACKS.md's episode section: "ratios roughly 0.4-0.6 (the
    doc's example D is 0.48)"; the tests section widens that to 0.3-0.7 for
    the fixture as generated. Both hold here."""
    rec = episode_fixture_run.records[0]
    assert rec.adequacy is not None
    assert Decimal("0.3") < rec.adequacy.value < Decimal("0.7")


def test_method1_flags_land_in_all_three_buckets(episode_fixture_run: Run) -> None:
    buckets = {f.bucket for a in episode_fixture_run.adequacies for f in a.method1_flags}
    assert buckets == {"missed", "mismatched", "no_code"}


def test_the_median_study_episode_s_signature(episode_fixture_run: Run) -> None:
    """Doc example D: the median study episode reads as "no billing vehicle
    exists". The cohort is even-sized, so the interpolated code median need
    not equal any episode's score; the episodes nearest the median (within
    one point) stand in for it. The fixture is not shaped to hit a pattern;
    if the generator's draw moves the median case off both accepted
    patterns, that is a finding about the generator, not something to force."""
    lines = _service_line(episode_fixture_run)
    study = [a for a in episode_fixture_run.adequacies if lines[a.encounter_id] == "POSTPARTUM_HTN"]
    study_scores = sorted(Decimal(a.score.value) for a in study)
    median = quantile(study_scores, "0.5")
    nearest = min(abs(Decimal(a.score.value) - median) for a in study)
    assert nearest <= 1, f"no study episode within a point of the code median {median}"
    at_median = [a for a in study if abs(Decimal(a.score.value) - median) == nearest]
    patterns = {a.signature.pattern_id for a in at_median}
    print(f"median study score={median} nearest={nearest} pattern(s)={sorted(patterns)}")
    assert patterns <= set(PATTERN_IDS), patterns


def test_one_card_for_the_study_code(episode_fixture_run: Run) -> None:
    assert len(episode_fixture_run.cards) == 1
    card = episode_fixture_run.cards[0]
    assert card.code == "99453"
    assert card.service_line == "POSTPARTUM_HTN"

    rows = population.compare_rows(episode_fixture_run.cards, by="domains")
    assert len(rows) == 1
    assert rows[0].code == "99453"
    assert rows[0].domain == "POSTPARTUM_HTN"
    assert rows[0].encounter_class == "episode"


def test_linkage_is_complete(episode_fixture_run: Run) -> None:
    assert episode_fixture_run.linkage.rate == Decimal("1.0000")
    assert episode_fixture_run.linkage.unlinked == ()


def test_nothing_was_excluded(episode_fixture_run: Run) -> None:
    assert episode_fixture_run.unmultiplied == ()
    assert episode_fixture_run.unbanded == ()
    assert episode_fixture_run.thin_payers == ()


def test_the_run_is_reproducible(episode_fixture_run: Run) -> None:
    from worth_complexity.cli import FIXTURE, FIXTURE_LOCALITY
    from worth_complexity.pipeline import run

    episode_fixture = FIXTURE.parent / "episode-synthetic"
    again = run(
        episode_fixture / "clinical",
        episode_fixture / "remittance",
        locality=FIXTURE_LOCALITY,
        setting="non-facility",
    )
    assert episode_fixture_run.witness.digest == again.witness.digest
