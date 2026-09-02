import { useEffect, useRef, useState } from "react";

import { describe, getFile } from "../api/client";
import { kb } from "../format";
import type { DeliveredFile, FileSummary } from "../types";
import type { Remote } from "../useRemote";
import { DataTable } from "./DataTable";
import { EdiViewer } from "./EdiViewer";
import { NoteViewer } from "./NoteViewer";

function Body({ file }: { file: DeliveredFile }) {
  switch (file.kind) {
    case "table":
      return <DataTable file={file} />;
    case "notes":
      return <NoteViewer bundle={file} />;
    case "edi":
      return <EdiViewer bundle={file} />;
  }
}

function FileCard({ file }: { file: FileSummary }) {
  // The manifest draws the card closed. The contents are fetched the first time
  // someone opens it, and not before: nobody pays for 525 notes to look at a
  // hash.
  const [open, setOpen] = useState(false);
  const [contents, setContents] = useState<Remote<DeliveredFile> | null>(null);
  const [attempt, setAttempt] = useState(0);
  // Held in a ref, not state: a state dependency here would re-run the effect
  // (and its cleanup) the moment the request was marked in flight, and the
  // response would arrive to a listener that had already been told to ignore it.
  const fetched = useRef(false);

  useEffect(() => {
    if (!open || fetched.current) return;
    let live = true;
    setContents({ status: "loading" });
    getFile(file.name).then(
      (data) => {
        if (!live) return;
        fetched.current = true;
        setContents({ status: "ok", data });
      },
      (error: unknown) =>
        live && setContents({ status: "error", error: describe(error) }),
    );
    return () => {
      live = false;
    };
  }, [open, attempt, file.name]);

  return (
    <details
      className={open ? "card open" : "card"}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        <div className="card-top">
          <span className="card-name">{file.name}</span>
          <span className="card-size">
            {file.kind === "table"
              ? `${file.rows} rows · ${kb(file.bytes)}`
              : `${file.count} files · ${kb(file.bytes)}`}
          </span>
        </div>
        <p className="card-blurb">{file.blurb}</p>
        {file.kind === "table" && (
          <div className="card-hash">sha256 {file.sha256}</div>
        )}
      </summary>
      {open && (
        <div className="card-body">
          {contents?.status === "loading" && (
            <p className="remote">Fetching {file.name} as received…</p>
          )}
          {contents?.status === "error" && (
            <p className="remote error">
              Could not fetch {file.name}. {contents.error}{" "}
              <button
                type="button"
                className="btn-quiet"
                onClick={() => setAttempt((n) => n + 1)}
              >
                Try again
              </button>
            </p>
          )}
          {contents?.status === "ok" && <Body file={contents.data} />}
        </div>
      )}
    </details>
  );
}

export function FileCards({ files }: { files: FileSummary[] }) {
  return (
    <div className="cards">
      {files.map((file) => (
        <FileCard key={file.name} file={file} />
      ))}
    </div>
  );
}
