import { useMemo, useState } from "react";

import { kb } from "../format";
import type { NoteBundle } from "../types";

/** Headers, so a reader can see the sections the rules scope on. */
const HEADER = /^[ \t]*([A-Z][A-Z0-9 /&'()-]{2,60}?)[ \t]*(?::[ \t]*|$)/;

/** Sections no clinical rule in the pack may read. */
const EXCLUDED = new Set([
  "indication",
  "indications",
  "history",
  "past surgical history",
  "past medical history",
  "preoperative diagnosis",
  "review of systems",
  "family history",
  "plan",
  "disposition",
]);

export function NoteViewer({ bundle }: { bundle: NoteBundle }) {
  const [selected, setSelected] = useState(0);
  const note = bundle.files[selected];

  const lines = useMemo(() => {
    if (!note) return [];
    let excluded = false;
    return note.text.split("\n").map((line) => {
      const head = HEADER.exec(line);
      if (head) excluded = EXCLUDED.has((head[1] ?? "").trim().toLowerCase());
      return { line, header: Boolean(head), excluded };
    });
  }, [note]);

  return (
    <div className="edi">
      <div className="edi-list">
        {bundle.files.map((f, i) => (
          <button
            key={f.name}
            type="button"
            aria-current={i === selected}
            onClick={() => setSelected(i)}
          >
            {f.encounter} · {f.noteType}
            <small>
              {f.name} · {kb(f.bytes)}
            </small>
          </button>
        ))}
      </div>
      <div className="note-body">
        <p className="note-key">
          <span className="key-swatch" /> sections the Layer A rules read ·{" "}
          <span className="key-swatch off" /> history and plan, excluded by
          every rule
        </p>
        {lines.map((row, i) => (
          <div
            key={i}
            className={
              (row.header ? "note-head" : "note-line") +
              (row.excluded ? " off" : "")
            }
          >
            {row.line || " "}
          </div>
        ))}
      </div>
    </div>
  );
}
