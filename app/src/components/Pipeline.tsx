import { useEffect, useMemo, useRef, useState } from "react";

import type { RunResult } from "../types";

/** A run the server returned, and whether this page asked for it. */
export interface RunState {
  result: RunResult;
  /** True when this page pressed the button; false when the server already had it. */
  fresh: boolean;
}

const REPLAY_MS = 1600;
const FLOOR_MS = 110;

/**
 * Each step plays for a share of the replay proportional to how long its
 * pipeline stage actually took on the server, with a floor so the fast ones
 * are still visible. Steps sharing a stage split that stage's time. Reduced
 * motion collapses the whole thing to nothing.
 */
function durations(result: RunResult): number[] {
  const perStage = new Map<string, number>();
  for (const step of result.steps) {
    perStage.set(step.stage, (perStage.get(step.stage) ?? 0) + 1);
  }
  const weights = result.steps.map(
    (step) =>
      Math.max(result.timings[step.stage] ?? 0, 1) /
      (perStage.get(step.stage) ?? 1),
  );
  const total = weights.reduce((a, b) => a + b, 0);
  return weights.map((w) => FLOOR_MS + (REPLAY_MS * w) / total);
}

/**
 * The replay is provenance, not a gate.
 *
 * Ingest asks the server to run the pipeline. When the result arrives the steps
 * play back with the server's own timings, and once it has finished the same
 * control takes you to the output. A run the server already had when the page
 * opened is shown finished, because replaying it would be theatre.
 */
export function Pipeline({
  run,
  running,
  error,
  onRun,
  onDone,
  onJump,
}: {
  run: RunState | null;
  running: boolean;
  error: string | null;
  onRun: () => void;
  onDone: () => void;
  onJump: () => void;
}) {
  const steps = run?.result.steps ?? [];
  const [cursor, setCursor] = useState(-1);
  const timer = useRef<number | undefined>(undefined);
  const done = useRef(onDone);
  done.current = onDone;

  const pace = useMemo(() => (run ? durations(run.result) : []), [run]);

  // A new result starts the replay; a pre-existing one is already finished.
  useEffect(() => {
    if (!run) {
      setCursor(-1);
      return;
    }
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    setCursor(run.fresh && !reduced ? 0 : run.result.steps.length);
  }, [run]);

  const replaying = cursor >= 0 && cursor < steps.length;
  const finished = run !== null && cursor >= steps.length;

  useEffect(() => {
    if (!replaying) return;
    timer.current = window.setTimeout(
      () => setCursor((c) => c + 1),
      pace[cursor] ?? FLOOR_MS,
    );
    return () => window.clearTimeout(timer.current);
  }, [cursor, replaying, pace]);

  useEffect(() => {
    if (finished) done.current();
  }, [finished]);

  const progress =
    cursor < 0 ? 0 : Math.min(cursor + 1, steps.length) / steps.length;
  const serverMs = run
    ? Object.values(run.result.timings).reduce((a, b) => a + b, 0)
    : 0;

  return (
    <>
      <div className="run-row">
        <button
          type="button"
          className="btn"
          onClick={() => (finished ? onJump() : onRun())}
          disabled={running || replaying}
        >
          {running
            ? "Running on the server…"
            : replaying
              ? "Replaying"
              : finished
                ? "View results ↓"
                : "Ingest"}
        </button>
        {replaying && (
          <button
            type="button"
            className="btn-quiet"
            onClick={() => setCursor(steps.length)}
          >
            Skip to results
          </button>
        )}
        {finished && !running && (
          <button type="button" className="btn-quiet" onClick={onRun}>
            Run again
          </button>
        )}
        <div className="bar" aria-hidden="true">
          <i style={{ width: `${progress * 100}%` }} />
        </div>
      </div>

      {error && <p className="remote error">The run failed. {error}</p>}

      {!run && !running && !error && (
        <p className="page-note">
          Nothing has run yet. Ingest sends the request; the server reads the
          files above, scores every encounter, links the remittance, fits the
          curves and returns the output. Same inputs, same answer, every time.
        </p>
      )}

      {run && (
        <div className="steps">
          {steps.map((step, i) => (
            <div
              key={step.label}
              className={`step${i < cursor ? " done" : i === cursor ? " on" : ""}`}
            >
              <div className="step-n">{String(i + 1).padStart(2, "0")}</div>
              <div>
                <div className="step-label">{step.label}</div>
                <div className="step-detail">{step.detail}</div>
              </div>
              <div className="step-result">{step.result}</div>
            </div>
          ))}
        </div>
      )}

      {finished && (
        <p className="run-meta">
          The server ran the pipeline in {serverMs} ms
          {run.fresh ? "" : ", before this page opened"}. The replay above is
          paced to those timings.
        </p>
      )}
    </>
  );
}
