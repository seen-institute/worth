"""Shared fixtures.

The pipeline now reads 525 notes over 396 encounters and bootstraps an interval
per code, so a full run costs real seconds. Several modules need the same run;
computing it once per session keeps the suite fast enough to stay in the loop.
"""

from __future__ import annotations

import pytest
from worth_complexity.cli import FIXTURE, FIXTURE_LOCALITY
from worth_complexity.pipeline import Run, run

VISIT_FIXTURE = FIXTURE.parent / "visit-synthetic"
EPISODE_FIXTURE = FIXTURE.parent / "episode-synthetic"
MIXED_FIXTURE = FIXTURE.parent / "mixed-synthetic"


@pytest.fixture(scope="session")
def fixture_run() -> Run:
    """One pass over the committed synthetic dataset, shared by every test."""
    return run(FIXTURE / "clinical", FIXTURE / "remittance", locality=FIXTURE_LOCALITY)


@pytest.fixture(scope="session")
def visit_fixture_run() -> Run:
    """One pass over the committed visit-synthetic dataset, shared by every
    test. ``pack_name`` and ``setting`` are both omitted deliberately: the
    class-default dispatch (decision 3, CONTRACT-PACKS.md) is what a caller
    who has never heard of the visit class gets by default, and that is
    exactly what this fixture is meant to exercise."""
    return run(VISIT_FIXTURE / "clinical", VISIT_FIXTURE / "remittance", locality=FIXTURE_LOCALITY)


@pytest.fixture(scope="session")
def episode_fixture_run() -> Run:
    """One pass over the committed episode-synthetic dataset, shared by
    every test. ``setting`` is passed explicitly as ``"non-facility"``
    (agent E's own instructions: never rely on ``CLASS_DEFAULT_SETTING`` for
    this class's own tests, even though it now resolves the same value);
    ``pack_name`` is omitted so decision 3's class-default dispatch is what
    actually resolves ``episode-rpm-v1``."""
    return run(
        EPISODE_FIXTURE / "clinical",
        EPISODE_FIXTURE / "remittance",
        locality=FIXTURE_LOCALITY,
        setting="non-facility",
    )


@pytest.fixture(scope="session")
def mixed_fixture_run() -> Run:
    """One pass over the committed mixed-synthetic dataset (CONTRACT-PACKS-
    MC.md decision 7), shared by every test. ``pack_name`` and ``setting``
    are both omitted: three anchors, three classes, each resolving its own
    packaged pack and its own class default setting with nothing passed."""
    return run(MIXED_FIXTURE / "clinical", MIXED_FIXTURE / "remittance", locality=FIXTURE_LOCALITY)
