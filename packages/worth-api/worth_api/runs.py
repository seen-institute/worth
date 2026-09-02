"""Running the pipeline on request, and remembering the last answer.

A run is deterministic: the same dataset, rule pack, locality, setting and
fee schedules produce the same bytes, on any machine. So the cache key is
exactly those things, and a repeat request for unchanged inputs is served
from memory. That is not an optimisation so much as a statement: if the
inputs did not change, neither did the answer.

The server holds one result at a time. Persisting runs earns its place when a
partner can upload an extract; until then a restart recomputes in under a
second.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from worth_complexity.pipeline import STAGES, run

from worth_api.dataset import manifest
from worth_api.export import RunResult, run_result

if TYPE_CHECKING:
    from worth_complexity.adequacy import SchedulePool

    from worth_api.settings import Settings


@dataclass(frozen=True, slots=True)
class RunKey:
    content_hash: str
    pack_digest: str
    locality: str
    setting: str


class Stopwatch:
    """Times the gaps between the pipeline's stage callbacks."""

    def __init__(self) -> None:
        self.timings: dict[str, int] = {}
        self._current: str | None = None
        self._started = 0.0

    def __call__(self, stage: str) -> None:
        self.stop()
        self._current = stage
        self._started = time.perf_counter()

    def stop(self) -> None:
        if self._current is not None:
            elapsed = time.perf_counter() - self._started
            self.timings[self._current] = round(elapsed * 1000)
            self._current = None


class RunService:
    def __init__(self, settings: Settings, schedules: SchedulePool) -> None:
        self._settings = settings
        self._schedules = schedules
        self._lock = threading.Lock()
        self._latest: tuple[RunKey, RunResult] | None = None

    @property
    def latest(self) -> RunResult | None:
        return self._latest[1] if self._latest else None

    def execute(self) -> RunResult:
        """Run the pipeline, unless the cached result is for these exact inputs."""
        settings = self._settings
        content_hash = manifest(settings.dataset_dir).content_hash
        with self._lock:
            latest = self._latest
            if (
                latest is not None
                and latest[0].content_hash == content_hash
                and (latest[0].locality, latest[0].setting) == (settings.locality, settings.setting)
            ):
                return latest[1]
            watch = Stopwatch()
            r = run(
                settings.clinical,
                settings.remittance,
                locality=settings.locality,
                setting=settings.setting,
                schedules=self._schedules,
                progress=watch,
            )
            watch.stop()
            assert set(watch.timings) == set(STAGES), "every stage reports once"
            result = run_result(r, settings.dataset_dir, watch.timings)
            self._latest = (
                RunKey(content_hash, r.pack.digest, r.locality, r.setting.value),
                result,
            )
            return result
