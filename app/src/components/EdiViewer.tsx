import { useState } from "react";

import type { EdiBundle } from "../types";

/** Segments that carry the values the pipeline reads. Highlighted, not decorated. */
const KEY_SEGMENTS = new Set(["CLP", "SVC", "AMT", "CAS"]);

export function EdiViewer({ bundle }: { bundle: EdiBundle }) {
  const [selected, setSelected] = useState(0);
  const file = bundle.files[selected];

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
            {f.name.replace("835_", "").replace(".edi", "")}
            <small>
              {f.claims} claims · {f.lines} lines
            </small>
          </button>
        ))}
      </div>
      <div className="edi-seg">
        {file?.segments.map((segment, i) => {
          const tag = segment.split("*")[0] ?? "";
          return (
            <div key={i}>
              <span className={KEY_SEGMENTS.has(tag) ? "tag key" : "tag"}>
                {tag}
              </span>
              {segment.slice(tag.length)}
            </div>
          );
        })}
      </div>
    </div>
  );
}
