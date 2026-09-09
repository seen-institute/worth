"""Every (scenario, class) pair in the catalog (CONTRACT-SEEDS.md, "Scenario
catalog and planted truth") actually generates, is readable, and plants what
its own ``truth.json`` claims.

Core scenarios: generate at scale 1, read back through
:func:`worth_complexity.extracts.read_extract`, and validate ``truth.json``
against :func:`worth_complexity.synthetic.truth.validate`.

Broken scenarios (CONTRACT-SEEDS.md's broken group): generation itself
always succeeds -- the corruption is applied by
:mod:`worth_complexity.synthetic.dirt` *after* the generator has already
scored and priced its own cohort, see that module's docstring. The failure
only appears once the directory is run through
:func:`worth_complexity.pipeline.run`, which is what this test asserts:
the exception's class name, its message substring, and the pipeline stage
active when it was raised, all three read back from ``truth.json``'s own
``planted.expected_failure`` rather than hand-typed here a second time. A
broken directory that happens to fail during ``pipeline.run``'s own
``extract`` stage (``read_extract`` is that stage's first call) is exactly
the "may fail at read" case the task describes -- ``pipeline.run`` covers it
without a separate direct call to ``read_extract``.

Kept to scale 1 throughout (CONTRACT-SEEDS.md decision 5: "about 24 study
encounters per code") so the full 15-scenario x 4-class sweep stays well
under a minute; see ``pytest --durations=5`` if that ever regresses.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from worth_complexity import extracts, pipeline, population
from worth_complexity.synthetic import episode, mixed, surgical, visit
from worth_complexity.synthetic import truth as truth_mod
from worth_complexity.synthetic.scenarios import SCENARIOS

#: No CY2027 CMS archive is pinned (worth-fees.sources.PINNED_VINTAGES stops
#: at CY2026 Q4); the generators clamp their own pricing to it
#: (worth_complexity.synthetic.pricing.vintage_for_date_clamped) but
#: ``pipeline.run``'s own default reference vintage
#: (``vintage_for_date(max(service_date))``) does not, so every ``pipeline.
#: run`` call over a ``policy-change`` fixture below pins it explicitly.
_POLICY_REFERENCE = (2026, 4)

if TYPE_CHECKING:
    from worth_complexity.synthetic.scenarios import Scenario

_BUILDERS = {
    "surgical": surgical.build,
    "visit": visit.build,
    "episode": episode.build,
    "mixed": mixed.build,
}
_CLASSES = ("surgical", "visit", "episode", "mixed")

_CASES = [(name, cls) for name in SCENARIOS for cls in _CLASSES]
_IDS = [f"{name}-{cls}" for name, cls in _CASES]


def _build(scenario_name: str, encounter_class: str, out: Path) -> Scenario:
    scenario = SCENARIOS[scenario_name]
    _BUILDERS[encounter_class](out, scenario=scenario, seed=1, scale=1)
    return scenario


@pytest.mark.parametrize(("scenario_name", "encounter_class"), _CASES, ids=_IDS)
def test_scenario_truth_validates(tmp_path: Path, scenario_name: str, encounter_class: str) -> None:
    """Every scenario generates and its ``truth.json`` validates, whichever
    group it is in."""
    out = tmp_path / "out"
    _build(scenario_name, encounter_class, out)

    written = truth_mod.read(out)
    truth_mod.validate(written)
    assert written.scenario == scenario_name
    assert written.encounter_class == encounter_class
    assert written.seed == 1


@pytest.mark.parametrize(("scenario_name", "encounter_class"), _CASES, ids=_IDS)
def test_scenario_purpose_renders(tmp_path: Path, scenario_name: str, encounter_class: str) -> None:
    """Every scenario's ``Scenario.purpose`` template renders to a real
    sentence: no leftover ``{placeholder}`` (a ``[bracketed clause]``
    referencing a value this seed did not plant is dropped whole rather
    than left half-filled, see :func:`worth_complexity.synthetic.truth.
    render_purpose`), and -- for the core group, where the catalog's own
    templates always plant at least one number (a ratio, a count, a
    percentage, a date, or the claim-form number itself) -- the rendered
    text actually reads one back, not just prose."""
    out = tmp_path / "out"
    scenario = _build(scenario_name, encounter_class, out)

    written = truth_mod.read(out)
    purpose = written.purpose
    assert purpose, f"{scenario_name}/{encounter_class}: empty purpose"
    assert "{" not in purpose and "}" not in purpose, (
        f"{scenario_name}/{encounter_class}: unrendered placeholder in {purpose!r}"
    )
    if scenario.group == "core":
        assert re.search(r"\d", purpose), (
            f"{scenario_name}/{encounter_class}: no number in {purpose!r}"
        )


@pytest.mark.parametrize(
    ("scenario_name", "encounter_class"),
    [(n, c) for n, c in _CASES if SCENARIOS[n].group == "core"],
    ids=[f"{n}-{c}" for n, c in _CASES if SCENARIOS[n].group == "core"],
)
def test_core_scenario_is_readable(
    tmp_path: Path, scenario_name: str, encounter_class: str
) -> None:
    """A core (non-broken) scenario's directory reads cleanly and carries at
    least one encounter."""
    out = tmp_path / "out"
    _build(scenario_name, encounter_class, out)

    extract = extracts.read_extract(out / "clinical")
    assert extract.encounter_class == encounter_class
    encounters = extract.encounters()
    assert encounters, f"{scenario_name}/{encounter_class}: no encounters read back"


@pytest.mark.parametrize(
    ("scenario_name", "encounter_class"),
    [(n, c) for n, c in _CASES if SCENARIOS[n].group == "broken"],
    ids=[f"{n}-{c}" for n, c in _CASES if SCENARIOS[n].group == "broken"],
)
def test_broken_scenario_fails_as_planted(
    tmp_path: Path, scenario_name: str, encounter_class: str
) -> None:
    """A broken scenario's directory always generates cleanly (the
    corruption lands after scoring/pricing during generation, see the
    module docstring); running it through the real pipeline raises exactly
    what ``truth.json``'s own ``planted.expected_failure`` says: the
    exception class name, a message substring, and the pipeline stage that
    was active when it was raised."""
    out = tmp_path / "out"
    _build(scenario_name, encounter_class, out)

    written = truth_mod.read(out)
    expected = written.planted["expected_failure"]

    stages: list[str] = []
    with pytest.raises(Exception) as excinfo:
        pipeline.run(
            out / "clinical",
            out / "remittance",
            locality="NY01",
            claims_dir=out / "claims",
            progress=stages.append,
        )

    exc = excinfo.value
    assert type(exc).__name__ == expected["error"], (
        f"{scenario_name}/{encounter_class}: raised {type(exc).__name__}, "
        f"planted {expected['error']}"
    )
    assert expected["message_contains"] in str(exc), (
        f"{scenario_name}/{encounter_class}: {expected['message_contains']!r} not in {exc!s}"
    )
    assert stages, f"{scenario_name}/{encounter_class}: no stage began before the raise"
    assert stages[-1] == expected["stage"], (
        f"{scenario_name}/{encounter_class}: raised during stage {stages[-1]!r}, "
        f"planted {expected['stage']!r}"
    )


# ---------------------------------------------------------------------------
# policy-change: the Over-time output (decision 7, CONTRACT-SEEDS.md)
# ---------------------------------------------------------------------------


def _class_run(run: pipeline.Run, encounter_class: str) -> pipeline.ClassRun:
    return next(c for c in run.classes if c.encounter_class == encounter_class)


def _period_rows(
    run: pipeline.Run, class_run: pipeline.ClassRun
) -> tuple[population.PeriodRow, ...]:
    """The same ``PeriodRow`` construction ``pipeline.run``'s own "trends"
    stage does, reusable here to call :func:`worth_complexity.population.
    periods` with an explicit ``granularity`` -- ``pipeline.run`` itself
    always asks for ``"auto"``, which a ~24-per-code study cohort spread
    over 24 months does not clear the suppression floor at monthly
    granularity for (CONTRACT-SEEDS.md decision 5's small fixtures), so
    asserting "24 monthly points" needs the finer bucketing requested
    directly rather than read back from ``ClassRun.trends``."""
    obs_by_id = {o.scored.encounter.encounter_id: o for o in class_run.observations}
    return tuple(
        population.PeriodRow(
            code=a.cpt,
            service_date=obs_by_id[a.encounter_id].scored.encounter.service_date,
            score=a.score.value,
            realized=a.realized,
            expected=a.expected,
            signature=a.signature,
            payer_label=run.payer_labels[obs_by_id[a.encounter_id].payer_id],
        )
        for a in class_run.adequacies
    )


def test_policy_change_visit_trends(tmp_path: Path) -> None:
    """The visit-class ``policy-change`` fixture: linkage is 100% (the
    antepartum bundle carve-out, :data:`worth_complexity.linkage.
    BUNDLE_CODES`, links every pregnancy visit to its shared claim), and
    ``pipeline.run(..., policy_date=date(2027, 1, 1))`` reads back a real
    pre/post split for 99214 (MENOPAUSE and post-date MATERNITY) and for
    the pre-date antepartum bundle code(s), with the maternity billing
    vehicle's pre ratio nowhere near its post ratio -- the whole point of
    the scenario."""
    out = tmp_path / "out"
    scenario = SCENARIOS["policy-change"]
    visit.build(out, scenario=scenario, seed=1, scale=1)

    run = pipeline.run(
        out / "clinical",
        out / "remittance",
        locality="NY01",
        claims_dir=out / "claims",
        policy_date=date(2027, 1, 1),
        reference=_POLICY_REFERENCE,
    )
    assert run.linkage.rate == 1, run.linkage.render()

    by_code = {series.code: series for series in run.trends}
    post214 = by_code["99214"]
    assert post214.pre is not None
    assert post214.post is not None
    assert post214.pre.ratio is not None
    assert post214.post.ratio is not None

    bundle_series = [by_code[c] for c in ("59425", "59426") if c in by_code]
    assert bundle_series, "no antepartum bundle code in trends"
    pre_ratios = [s.pre.ratio.value for s in bundle_series if s.pre is not None and s.pre.ratio]
    assert pre_ratios, "no pre-date bundle ratio"
    # No bundle visit is billed after the policy date -- decision 7's
    # billing-vehicle shift, not a thin cell.
    assert all(s.post is None for s in bundle_series)

    post_ratio = post214.post.ratio.value
    assert all(abs(r - post_ratio) > (post_ratio * Decimal("0.05")) for r in pre_ratios), (
        f"bundle pre ratio {pre_ratios} too close to post-date 99214 ratio {post_ratio}"
    )


@pytest.mark.parametrize("encounter_class", ["surgical", "episode"])
def test_policy_change_monthly_points(tmp_path: Path, encounter_class: str) -> None:
    """The surgical and episode ``policy-change`` fixtures span 24 real
    calendar months (CONTRACT-SEEDS.md's catalog); requesting month
    granularity directly (see :func:`_period_rows`) gives back all 24."""
    out = tmp_path / "out"
    scenario = SCENARIOS["policy-change"]
    _BUILDERS[encounter_class](out, scenario=scenario, seed=1, scale=1)

    run = pipeline.run(
        out / "clinical",
        out / "remittance",
        locality="NY01",
        claims_dir=out / "claims",
        policy_date=date(2027, 1, 1),
        reference=_POLICY_REFERENCE,
    )
    class_run = _class_run(run, encounter_class)
    rows = _period_rows(run, class_run)
    assert rows, f"{encounter_class}: no priced study encounter to build periods from"

    series = population.periods(rows, policy_date=date(2027, 1, 1), granularity="month")
    assert series, f"{encounter_class}: no period series at all"
    expected_months = [f"2026-{m:02d}" for m in range(1, 13)] + [
        f"2027-{m:02d}" for m in range(1, 13)
    ]
    for s in series:
        months = [p.period for p in s.points]
        assert months == expected_months, f"{encounter_class}/{s.code}: {months}"


# ---------------------------------------------------------------------------
# multi-site: missingness per marker per site (decision 6, CONTRACT-SEEDS.md)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("encounter_class", ["surgical", "visit", "episode"])
def test_multi_site_instrument_health_and_sites(tmp_path: Path, encounter_class: str) -> None:
    """``truth.json``'s own ``missingness_by_site`` is a pure, deterministic
    read of the generated directory (:func:`worth_complexity.synthetic.
    planted.missingness_by_site`, the same function ``build()`` itself
    calls) -- reproduced here byte-for-byte -- and the real pipeline's own
    work queue and cards see every one of the three planted sites.

    Bands are not re-checked per code/card here: a card's own cohort at one
    site can be a handful of encounters, far noisier than the whole class's
    (:func:`worth_complexity.synthetic.planted.missingness_by_site`'s own
    denominator), so a per-card, per-site value landing outside the
    class-wide band is expected sampling variance, not a planted-truth
    mismatch."""
    out = tmp_path / "out"
    scenario = SCENARIOS["multi-site"]
    _BUILDERS[encounter_class](out, scenario=scenario, seed=1, scale=1)

    truth = truth_mod.read(out)
    site_bands = truth.planted["missingness_by_site"]
    assert len(site_bands) == truth.planted["sites"] == 3

    from worth_complexity.synthetic import planted as planted_mod

    reread = planted_mod.missingness_by_site(out / "clinical", encounter_class)
    assert reread == site_bands

    # Site A (the unaffected facility) never loses a marker; at least one
    # other site does -- both B and C for surgical (adhesion/anatomic-
    # extent narrative markers and EBL), but only one of the two for visit
    # (site B's shared decision-making note) and episode (site C's time
    # log: episodes carry no notes, so site B's habit has nothing to touch
    # there -- see worth_complexity.synthetic.sites' module docstring).
    assert any(not v for v in site_bands.values()), (
        f"{encounter_class}: no fully-documented site in {site_bands}"
    )
    assert any(v for v in site_bands.values()), (
        f"{encounter_class}: no site shows any missingness: {site_bands}"
    )

    run = pipeline.run(
        out / "clinical",
        out / "remittance",
        locality="NY01",
        claims_dir=out / "claims",
    )
    class_run = _class_run(run, encounter_class)

    queue_sites = {item.site for item in class_run.queue}
    card_sites = {
        site
        for card in class_run.cards
        for marker in card.instrument_health.markers
        for site in marker.missingness_by_site
    }
    assert queue_sites | card_sites >= set(site_bands), (
        f"{encounter_class}: planted sites {set(site_bands)} not all seen in the run "
        f"(queue {queue_sites}, cards {card_sites})"
    )
