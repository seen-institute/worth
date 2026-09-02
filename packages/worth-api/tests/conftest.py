"""Shared fixtures: one run, one serialized result, one app, per session."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from worth_api.export import DeliveredFile, RunResult, delivered_files, run_result
from worth_api.main import create_app
from worth_api.settings import Settings
from worth_complexity.cli import FIXTURE, FIXTURE_LOCALITY
from worth_complexity.pipeline import Run, run


@pytest.fixture(scope="session")
def fixture_run() -> Run:
    return run(FIXTURE / "clinical", FIXTURE / "remittance", locality=FIXTURE_LOCALITY)


@pytest.fixture(scope="session")
def result(fixture_run: Run) -> RunResult:
    return run_result(fixture_run, FIXTURE)


@pytest.fixture(scope="session")
def files() -> list[DeliveredFile]:
    return delivered_files(FIXTURE)


@pytest.fixture(scope="session")
def client() -> TestClient:
    return TestClient(create_app(Settings(dataset_dir=FIXTURE)))
