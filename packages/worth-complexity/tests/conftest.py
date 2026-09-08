"""Shared fixtures.

The pipeline now reads 525 notes over 396 encounters and bootstraps an interval
per code, so a full run costs real seconds. Several modules need the same run;
computing it once per session keeps the suite fast enough to stay in the loop.
"""

from __future__ import annotations

import pytest
from worth_complexity.cli import FIXTURE, FIXTURE_LOCALITY
from worth_complexity.pipeline import Run, run


@pytest.fixture(scope="session")
def fixture_run() -> Run:
    """One pass over the committed synthetic dataset, shared by every test."""
    return run(FIXTURE / "clinical", FIXTURE / "remittance", locality=FIXTURE_LOCALITY)
