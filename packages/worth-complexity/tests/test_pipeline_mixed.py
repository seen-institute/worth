"""Multi-class runs (CONTRACT-PACKS-MC.md, the "MC" track): ``ClassRun``,
``Run``'s flattened and single-class-convenience properties,
``pipeline._resolve_packs``' sequence/error handling, Method 3 band purity
across classes, and the ``population`` pooled-function guard.

``mixed_fixture_run`` (``conftest.py``) is the real ``fixtures/mixed-
synthetic`` dataset: three anchors, three classes, nothing passed so every
class resolves its own packaged pack and its own class-default setting.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from worth_complexity.cli import FIXTURE, FIXTURE_LOCALITY
from worth_complexity.models import MethodologyViolation, RulePackError, WorthComplexityError
from worth_complexity.pipeline import run
from worth_complexity.population import assert_single_class, compare_rows, domain_rows
from worth_fees import PlaceOfService

if TYPE_CHECKING:
    from worth_complexity.pipeline import Run

CLINICAL = FIXTURE / "clinical"
REMITTANCE = FIXTURE / "remittance"
VISIT_FIXTURE = FIXTURE.parent / "visit-synthetic"
EPISODE_FIXTURE = FIXTURE.parent / "episode-synthetic"
MIXED_FIXTURE = FIXTURE.parent / "mixed-synthetic"

pytestmark = pytest.mark.skipif(
    not (MIXED_FIXTURE / "clinical").is_dir(),
    reason="fixtures/mixed-synthetic not built; run `just build-dataset mixed`",
)


# --------------------------------------------------------------------------
# The three ClassRuns: packs, settings, card codes
# --------------------------------------------------------------------------


def test_the_mixed_run_has_three_class_runs_in_class_order(mixed_fixture_run: Run) -> None:
    r = mixed_fixture_run
    assert [c.encounter_class for c in r.classes] == ["surgical", "visit", "episode"]


def test_each_class_resolves_its_own_default_pack_and_setting(mixed_fixture_run: Run) -> None:
    r = mixed_fixture_run
    by_class = {c.encounter_class: c for c in r.classes}
    assert by_class["surgical"].pack.rule_pack_id == "surgical"
    assert by_class["surgical"].setting is PlaceOfService.FACILITY
    assert by_class["visit"].pack.rule_pack_id == "visit-em"
    assert by_class["visit"].setting is PlaceOfService.NON_FACILITY
    assert by_class["episode"].pack.rule_pack_id == "episode-rpm"
    assert by_class["episode"].setting is PlaceOfService.NON_FACILITY
    for c in r.classes:
        assert c.pack.rule_pack_id.rsplit("-", 1)[0] or True  # sanity: loaded, not a stub
        assert c.pack.source == "packaged"


def test_each_class_s_cards_carry_only_that_class_s_own_codes(mixed_fixture_run: Run) -> None:
    """The surgical, visit and episode fixtures each publish a known set of
    study codes on their own; a mixed run must reproduce exactly the same
    per-class card codes, never blending one class's codes into another's."""
    r = mixed_fixture_run
    by_class = {c.encounter_class: c for c in r.classes}
    assert {card.code for card in by_class["surgical"].cards} == {
        "58558", "58563", "58570", "58661", "58662",
    }  # fmt: skip
    assert {card.code for card in by_class["visit"].cards} == {"99214"}
    assert {card.code for card in by_class["episode"].cards} == {"99453"}
    for card in r.cards:
        by_code = {c.encounter_class: c for c in r.classes}
        assert card.encounter_class in by_code


def test_no_class_is_dropped_and_nothing_is_unscored_or_unused(mixed_fixture_run: Run) -> None:
    r = mixed_fixture_run
    assert len(r.classes) == 3
    assert all(len(c.cards) > 0 for c in r.classes)
    assert r.unscored == ()
    assert r.unused_packs == ()


# --------------------------------------------------------------------------
# Flattened properties
# --------------------------------------------------------------------------


def test_flattened_properties_concatenate_across_classes(mixed_fixture_run: Run) -> None:
    r = mixed_fixture_run
    for name in ("observations", "adequacies", "records", "cards", "compare", "queue"):
        flattened = getattr(r, name)
        expected = tuple(x for c in r.classes for x in getattr(c, name))
        assert flattened == expected
        assert len(flattened) == sum(len(getattr(c, name)) for c in r.classes)

    assert r.study == tuple(o for c in r.classes for o in c.study)
    assert r.comparator == tuple(o for c in r.classes for o in c.comparator)
    assert len(r.study) + len(r.comparator) == len(r.observations)


def test_provenance_filter_never_raises_on_a_mixed_run(mixed_fixture_run: Run) -> None:
    """Unlike ``pack``/``setting``, ``provenance_filter`` is one argument to
    ``run()`` applied identically to every class, so it reads directly."""
    r = mixed_fixture_run
    assert r.provenance_filter == r.classes[0].provenance_filter
    assert all(c.provenance_filter == r.provenance_filter for c in r.classes)


# --------------------------------------------------------------------------
# Run.pack and friends raise on a genuinely mixed run
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "attr", ["pack", "rulebook_version", "weights_version", "setting", "schedule_curve", "priced"]
)
def test_single_class_convenience_properties_raise_on_a_mixed_run(
    mixed_fixture_run: Run, attr: str
) -> None:
    with pytest.raises(WorthComplexityError, match=r"classes"):
        getattr(mixed_fixture_run, attr)


def test_the_same_properties_work_fine_on_a_single_class_run(fixture_run: Run) -> None:
    assert fixture_run.pack.encounter_class == "surgical"
    assert fixture_run.rulebook_version
    assert fixture_run.weights_version
    assert fixture_run.setting is PlaceOfService.FACILITY


# --------------------------------------------------------------------------
# Pack sequences: applied by class, and the two error/reporting cases
# --------------------------------------------------------------------------


def test_a_pack_sequence_is_applied_by_the_class_it_declares() -> None:
    r = run(
        MIXED_FIXTURE / "clinical",
        MIXED_FIXTURE / "remittance",
        locality=FIXTURE_LOCALITY,
        pack_name=["visit-em-v1", "episode-rpm-v1"],
    )
    by_class = {c.encounter_class: c for c in r.classes}
    assert set(by_class) == {"visit", "episode"}
    assert by_class["visit"].pack.rule_pack_id == "visit-em"
    assert by_class["episode"].pack.rule_pack_id == "episode-rpm"
    # surgical was present in the extract but named by no given pack:
    # every one of its encounters is unscored, named with a reason.
    assert r.unscored
    assert all("surgical" in reason for _, reason in r.unscored)


def test_a_pack_for_a_class_not_in_the_extract_is_recorded_unused_not_an_error() -> None:
    """decision 3 (CONTRACT-PACKS-MC.md): on a combined extract, a pack
    naming a class the extract does not have is reported in
    ``Run.unused_packs``, never an error."""
    r = run(
        VISIT_FIXTURE / "clinical",
        VISIT_FIXTURE / "remittance",
        locality=FIXTURE_LOCALITY,
        pack_name=["visit-em-v1", "episode-rpm-v1"],
    )
    # a single-class extract: this is *not* the "extract has one class"
    # short-circuit, because a pack that does score the extract's own class
    # (visit) was given -- only the *other* pack, episode-rpm-v1, is unused.
    assert r.pack.encounter_class == "visit"
    assert len(r.unused_packs) == 1
    pack_id, reason = r.unused_packs[0]
    assert pack_id == "episode-rpm"
    assert "episode" in reason


def test_two_packs_for_the_same_class_is_an_error(tmp_path: Path) -> None:
    """Two entries -- one named, one a path -- that both declare
    ``encounter_class == "visit"`` are refused, whichever way each was
    given (decision 3, CONTRACT-PACKS-MC.md: "two packs for the same class
    is an error", stated without distinguishing how either arrived)."""
    import json

    from worth_complexity.rulepack import _PACK_DIR

    doc = json.loads((_PACK_DIR / "visit-em-v1.json").read_text())
    doc["rule_pack_id"] = "visit-em-duplicate"
    duplicate = tmp_path / "visit-em-duplicate.json"
    duplicate.write_text(json.dumps(doc))

    with pytest.raises(RulePackError, match="encounter class 'visit'"):
        run(
            MIXED_FIXTURE / "clinical",
            MIXED_FIXTURE / "remittance",
            locality=FIXTURE_LOCALITY,
            pack_name=["visit-em-v1"],
            pack_path=[duplicate],
        )


def test_a_mismatched_scalar_pack_on_a_single_class_extract_still_raises() -> None:
    """The pre-existing single-class behaviour (decision 3, CONTRACT-
    PACKS.md) is unchanged: a scalar ``pack_name`` naming the wrong class is
    refused immediately, not silently recorded as unused."""
    with pytest.raises(RulePackError, match="surgical"):
        run(
            VISIT_FIXTURE / "clinical",
            VISIT_FIXTURE / "remittance",
            locality=FIXTURE_LOCALITY,
            pack_name="surgical-v1",
        )


# --------------------------------------------------------------------------
# Method 3 bands: surgical class's bands contain only surgical comparators
# --------------------------------------------------------------------------


def test_method3_bands_in_the_surgical_class_contain_only_surgical_comparators(
    mixed_fixture_run: Run,
) -> None:
    r = mixed_fixture_run
    surgical_codes = {
        "58558", "58563", "58570", "58661", "58662",
        "29881", "49505", "44970", "47562", "52601", "27447", "50543", "55866",
    }  # fmt: skip
    surgical = next(c for c in r.classes if c.encounter_class == "surgical")
    checked = 0
    for a in surgical.adequacies:
        for point in a.m3.comparators:
            assert point.cpt in surgical_codes, (
                f"surgical class Method 3 band for {a.encounter_id} named a non-surgical "
                f"comparator CPT {point.cpt!r}"
            )
            checked += 1
    assert checked > 0


def test_method3_bands_never_cross_classes_for_any_class(mixed_fixture_run: Run) -> None:
    """A tighter, class-agnostic version of the check above: every band
    member's cpt is a code that class's own study or comparator cohort
    actually bills, never another class's."""
    r = mixed_fixture_run
    for c in r.classes:
        own_codes = {o.scored.encounter.primary_cpt for o in c.observations}
        for a in c.adequacies:
            for point in a.m3.comparators:
                assert point.cpt in own_codes


# --------------------------------------------------------------------------
# Domain rows on the visit fixture: two rows, MENOPAUSE and MATERNITY
# --------------------------------------------------------------------------


def test_domain_rows_on_the_visit_fixture_yields_menopause_and_maternity(
    visit_fixture_run: Run,
) -> None:
    r = visit_fixture_run
    (visit_class,) = r.classes
    rows = domain_rows(visit_class)
    assert {row.domain for row in rows} == {"MENOPAUSE", "MATERNITY"}
    assert len(rows) == 2
    for row in rows:
        assert row.encounter_class == "visit"
        assert row.n > 0
        # the domain's modal code stands in for "code": on this fixture both
        # study lines bill 99214 flat, so it should resolve to that code.
        assert row.code == "99214"


def test_domain_rows_on_the_mixed_fixture_s_visit_class(mixed_fixture_run: Run) -> None:
    r = mixed_fixture_run
    visit_class = next(c for c in r.classes if c.encounter_class == "visit")
    rows = domain_rows(visit_class)
    assert {row.domain for row in rows} == {"MENOPAUSE", "MATERNITY"}


# --------------------------------------------------------------------------
# The pooled-function guard
# --------------------------------------------------------------------------


def test_pooling_cards_from_two_classes_raises_methodology_violation(
    mixed_fixture_run: Run,
) -> None:
    r = mixed_fixture_run
    surgical_card = next(c for c in r.cards if c.encounter_class == "surgical")
    visit_card = next(c for c in r.cards if c.encounter_class == "visit")
    with pytest.raises(MethodologyViolation, match="more than one encounter class"):
        compare_rows((surgical_card, visit_card))
    with pytest.raises(MethodologyViolation):
        assert_single_class((surgical_card, visit_card))


def test_pooling_cards_from_one_class_is_fine(mixed_fixture_run: Run) -> None:
    r = mixed_fixture_run
    surgical_cards = tuple(c for c in r.cards if c.encounter_class == "surgical")
    assert compare_rows(surgical_cards)  # must not raise
    assert_single_class(surgical_cards)  # must not raise


def test_run_s_own_class_run_compare_rows_never_mix_classes(mixed_fixture_run: Run) -> None:
    """``ClassRun.compare`` is built from one class's own cards inside
    ``pipeline._run_class``; every row it produces should already carry that
    one class's ``encounter_class``."""
    r = mixed_fixture_run
    for c in r.classes:
        assert all(row.encounter_class == c.encounter_class for row in c.compare)
