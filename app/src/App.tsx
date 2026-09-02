import { useCallback, useEffect, useRef, useState } from "react";

import {
  describe,
  getDataset,
  getHealth,
  getLatestRun,
  getScoring,
  runPipeline,
} from "./api/client";
import { DataPage } from "./components/DataPage";
import { DownloadsPage } from "./components/DownloadsPage";
import { FileCards } from "./components/FileCards";
import { Masthead, SyntheticBar } from "./components/Masthead";
import { Output } from "./components/Output";
import { Pipeline, type RunState } from "./components/Pipeline";
import { PricePage } from "./components/PricePage";
import { ScorePage } from "./components/ScorePage";
import { StatusPage } from "./components/StatusPage";
import type { Dataset } from "./types";
import { RemoteState, useRemote } from "./useRemote";
import { useRoute } from "./useRoute";

function bundleCount(dataset: Dataset, kind: "notes" | "edi"): number {
  const f = dataset.files.find((x) => x.kind === kind);
  return f && f.kind !== "table" ? f.count : 0;
}

export function App() {
  const [route, navigate] = useRoute();
  const [health] = useRemote(getHealth);
  const [dataset, retryDataset] = useRemote(getDataset);
  const [scoring, retryScoring] = useRemote(getScoring);

  const [run, setRun] = useState<RunState | null>(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [complete, setComplete] = useState(false);
  const output = useRef<HTMLElement | null>(null);

  // The server remembers its last run. Pick it up so a reload does not lose
  // the output; it shows as finished rather than replaying.
  useEffect(() => {
    let live = true;
    getLatestRun().then(
      (result) => {
        if (live && result && run === null) setRun({ result, fresh: false });
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
    // Once, at mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const start = useCallback(async () => {
    setRunning(true);
    setRunError(null);
    setComplete(false);
    try {
      const result = await runPipeline();
      setRun({ result, fresh: true });
    } catch (error) {
      setRunError(describe(error));
    } finally {
      setRunning(false);
    }
  }, []);

  const jump = useCallback(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    output.current?.scrollIntoView({
      behavior: reduced ? "auto" : "smooth",
      block: "start",
    });
  }, []);

  const finish = useCallback(() => {
    setComplete(true);
    requestAnimationFrame(jump);
  }, [jump]);

  const version =
    health.status === "ok"
      ? `worth-complexity ${health.data.worthComplexity}`
      : "worth-complexity";

  const period =
    dataset.status === "ok"
      ? `${dataset.data.periodStart?.slice(0, 7) ?? ""} to ${dataset.data.periodEnd?.slice(0, 7) ?? ""}`
      : "";

  return (
    <>
      <Masthead route={route} onNavigate={navigate} version={version} />

      <SyntheticBar />

      <main className="wrap">
        {route === "status" && <StatusPage />}

        {route === "score" &&
          (scoring.status === "ok" ? (
            <ScorePage
              pack={scoring.data.rulePack}
              engine={scoring.data.noteEngine}
            />
          ) : (
            <RemoteState
              state={scoring}
              what="the rule pack"
              onRetry={retryScoring}
            />
          ))}

        {route === "data" &&
          (dataset.status === "ok" ? (
            <DataPage dataset={dataset.data} />
          ) : (
            <RemoteState
              state={dataset}
              what="the dataset"
              onRetry={retryDataset}
            />
          ))}

        {route === "price" && <PricePage />}

        {route === "downloads" &&
          (dataset.status === "ok" ? (
            <DownloadsPage dataset={dataset.data} />
          ) : (
            <RemoteState
              state={dataset}
              what="the dataset"
              onRetry={retryDataset}
            />
          ))}

        {route === "run" && dataset.status !== "ok" && (
          <RemoteState
            state={dataset}
            what="the dataset"
            onRetry={retryDataset}
          />
        )}

        {route === "run" && dataset.status === "ok" && (
          <>
            <section className="intro">
              <h1>One run of the worth-complexity pipeline.</h1>
              <p>
                The data is {dataset.data.encounters} surgical encounters at one
                made-up hospital over {period}. {dataset.data.study} study
                encounters across {dataset.data.codes.length} benign
                gynaecological codes, and {dataset.data.comparator} comparator
                encounters in general surgery, orthopaedics and urology.
              </p>
              <p>
                The files below are the three shapes a partner delivers. Five
                delimited extracts from Epic Clarity,{" "}
                {bundleCount(dataset.data, "notes")} clinical notes as free
                text, and {bundleCount(dataset.data, "edi")} 835 remittances.
                Each card opens to the data as received, fetched from the server
                when you open it.
              </p>
              <p>
                Layer A reads both kinds. Discrete fields give operative time,
                ASA class and team composition. The notes give the markers no
                field carries: which structures were involved, how dense the
                adhesions were, what happened during the case. The note reading
                is pattern matching with section scope and negation. An organ
                named under INDICATION is not counted as work performed today,
                and a finding that was ruled out is not counted at all. No model
                sits in the path.
              </p>
              <p>
                Ingest runs the pipeline on the server and renders the output.
                It is staged the way the methodology stages its own confidence.
                The reference distribution and the Method 0 slope come first,
                both available from a first validated extract. The adequacy
                index comes after, because it depends on a cross-specialty
                comparison the methodology describes as the part that will take
                longer. Payers are blinded throughout.
              </p>
              <p>
                <strong>The dataset is made up.</strong> Every figure here is
                computed from it, and the rule pack is marked{" "}
                {health.status === "ok" ? health.data.rulePack.status : "provisional"}.
              </p>
            </section>

            <section className="band">
              <div className="eyebrow">The extract, as delivered</div>
              <FileCards files={dataset.data.files} />
            </section>

            <section className="band">
              <div className="eyebrow">Pipeline</div>
              <Pipeline
                run={run}
                running={running}
                error={runError}
                onRun={start}
                onDone={finish}
                onJump={jump}
              />
            </section>

            {complete && run && (
              <section className="band" ref={output}>
                <div className="eyebrow">Output</div>
                <Output
                  summary={run.result.summary}
                  records={run.result.records}
                  curve={run.result.scheduleCurve}
                  multipliers={run.result.multipliers}
                  thinPayers={run.result.thinPayers}
                  encounters={run.result.encounters}
                  thinStrata={run.result.thinStrata}
                />
              </section>
            )}
          </>
        )}

        <footer>
          <p>{version}</p>
        </footer>
      </main>
    </>
  );
}
