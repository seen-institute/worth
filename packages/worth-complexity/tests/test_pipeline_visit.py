"""End to end on the committed visit-synthetic dataset (CONTRACT-PACKS.md).

Mirrors ``test_pipeline.py``'s shape, scoped to the visit class: the class
default pack and setting resolve with nothing passed, MENOPAUSE and MATERNITY
bill one flat code and score a flat Method 0 line, the comparator cohort's
slope rises, Method 1 flags land in all three buckets, and the maternity
cohort's dominant signature is Method 1.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
from typing import TYPE_CHECKING

from worth_complexity import population
from worth_fees import PlaceOfService

if TYPE_CHECKING:
    from worth_complexity.pipeline import Run

STUDY_LINES = {"MENOPAUSE", "MATERNITY"}
COMPARATOR_LINES = {"ENDOCRINOLOGY", "CARDIOLOGY", "NEPHROLOGY"}


def _service_line(run: Run) -> dict[str, str]:
    return {
        o.scored.encounter.encounter_id: o.scored.encounter.service_line for o in run.observations
    }


def test_the_class_default_pack_and_setting_resolve_with_nothing_passed(
    visit_fixture_run: Run,
) -> None:
    """Decision 3 (pack) and this track's setting default: a caller who
    passes neither ``pack_name`` nor ``setting`` still gets ``visit-em-v1``
    at the non-facility place of service, ``CLASS_DEFAULT_PACKS`` and
    ``CLASS_DEFAULT_SETTING`` doing the resolving, not a hard-coded default
    a visit-shaped extract would silently get wrong."""
    r = visit_fixture_run
    assert r.pack.rule_pack_id == "visit-em"
    assert r.pack.encounter_class == "visit"
    assert r.pack.source == "packaged"
    assert r.setting == PlaceOfService.NON_FACILITY


def test_every_encounter_carries_the_visit_class(visit_fixture_run: Run) -> None:
    for o in visit_fixture_run.observations:
        assert o.scored.encounter.encounter_class == "visit"


def test_the_cohorts_are_the_shape_the_generator_promises(visit_fixture_run: Run) -> None:
    lines = _service_line(visit_fixture_run)
    study_lines = {lines[o.scored.encounter.encounter_id] for o in visit_fixture_run.study}
    comparator_lines = {
        lines[o.scored.encounter.encounter_id] for o in visit_fixture_run.comparator
    }
    assert study_lines == STUDY_LINES
    assert comparator_lines == COMPARATOR_LINES
    assert len(visit_fixture_run.study) == 120
    assert len(visit_fixture_run.comparator) == 108


def test_every_study_encounter_bills_the_one_flat_em_code(visit_fixture_run: Run) -> None:
    assert {o.scored.encounter.primary_cpt for o in visit_fixture_run.study} == {"99214"}


def test_maternity_encounters_signature_starts_with_m1_dominant(visit_fixture_run: Run) -> None:
    """Doc example C: a code that never captures the extra documented time
    reads as Method 1 dominant. Every maternity visit is drawn with
    documented time above the 99214->99215 threshold, so the missed
    ``em-level-by-time-99214`` flag should dominate for the cohort."""
    lines = _service_line(visit_fixture_run)
    maternity = [a for a in visit_fixture_run.adequacies if lines[a.encounter_id] == "MATERNITY"]
    assert maternity
    assert all(a.signature.pattern_id.startswith("m1-dominant") for a in maternity)


def test_menopause_and_maternity_ratios_land_in_the_undervalued_band(
    visit_fixture_run: Run,
) -> None:
    """CONTRACT-PACKS.md: "ratios in roughly 0.5-0.8"; loosened here the same
    way ``test_pipeline.py`` loosens its own generator-recovery tolerance,
    since this is a per-domain average over one shared code's Method 3 bands,
    not the exact figure the generator dialled in."""
    lines = _service_line(visit_fixture_run)
    by_line: dict[str, list[Decimal]] = defaultdict(list)
    for a in visit_fixture_run.adequacies:
        by_line[lines[a.encounter_id]].append(a.ratio.value)
    for line in STUDY_LINES:
        ratios = by_line[line]
        assert ratios
        avg = sum(ratios) / len(ratios)
        assert Decimal("0.4") < avg < Decimal("0.9"), (line, avg)


def test_the_comparator_slope_rises_with_intensity(visit_fixture_run: Run) -> None:
    card = visit_fixture_run.cards[0]
    slope = card.comparator_slope
    assert slope is not None
    assert slope.verdict == "rising"
    assert slope.slope > 0


def test_method1_flags_land_in_all_three_buckets(visit_fixture_run: Run) -> None:
    buckets = {f.bucket for a in visit_fixture_run.adequacies for f in a.method1_flags}
    assert buckets == {"missed", "mismatched", "no_code"}


def test_one_card_for_the_shared_flat_code_spanning_both_domains(
    visit_fixture_run: Run,
) -> None:
    """MENOPAUSE and MATERNITY both bill flat 99214 (CONTRACT-PACKS.md), so
    they fold into one card, keyed by code, not by domain. Its own encounter
    population still spans both service lines, and ``compare_rows(...,
    by="domains")`` still reports a domain for it -- the modal service line
    among that card's encounters."""
    assert len(visit_fixture_run.cards) == 1
    card = visit_fixture_run.cards[0]
    assert card.code == "99214"

    lines = _service_line(visit_fixture_run)
    cohort_lines = Counter(
        lines[o.scored.encounter.encounter_id]
        for o in visit_fixture_run.study
        if o.scored.encounter.primary_cpt == "99214"
    )
    assert set(cohort_lines) == STUDY_LINES

    rows = population.compare_rows(visit_fixture_run.cards, by="domains")
    assert len(rows) == 1
    assert rows[0].code == "99214"
    assert rows[0].domain in STUDY_LINES
    assert rows[0].encounter_class == "visit"


def test_linkage_is_complete(visit_fixture_run: Run) -> None:
    assert visit_fixture_run.linkage.rate == Decimal("1.0000")
    assert visit_fixture_run.linkage.unlinked == ()


def test_the_run_is_reproducible(visit_fixture_run: Run) -> None:
    from worth_complexity.cli import FIXTURE, FIXTURE_LOCALITY
    from worth_complexity.pipeline import run

    visit_fixture = FIXTURE.parent / "visit-synthetic"
    again = run(visit_fixture / "clinical", visit_fixture / "remittance", locality=FIXTURE_LOCALITY)
    assert visit_fixture_run.witness.digest == again.witness.digest
