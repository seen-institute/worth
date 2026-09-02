import { useState } from "react";

import { money, ratioBand } from "../format";
import type { Encounter } from "../types";

function MarkerTable({ encounter }: { encounter: Encounter }) {
  return (
    <>
      <p className="lane-key">
        <span>
          <i className="lane" />
          read from a field
        </span>
        <span>
          <i className="lane rule" />
          read from the note
        </span>
      </p>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>marker</th>
              <th className="num">value</th>
              <th className="num">scale</th>
              <th className="num">norm</th>
              <th className="num">weight</th>
              <th className="num">contrib</th>
            </tr>
          </thead>
          <tbody>
            {encounter.markers.map((m) => (
              <tr key={m.id}>
                <td>
                  <span
                    className={`lane ${m.provenance}`}
                    title={
                      m.provenance === "rule"
                        ? "read from the note"
                        : "read from a field"
                    }
                  />
                  {m.id}
                </td>
                <td className="num">{m.value}</td>
                <td className="num">
                  {m.low}–{m.high}
                </td>
                <td className="num">{m.norm.toFixed(4)}</td>
                <td className="num">{m.weight.toFixed(4)}</td>
                <td className="num">{m.contribution.toFixed(4)}</td>
              </tr>
            ))}
            <tr className="total">
              <td>score</td>
              <td colSpan={4} />
              <td className="num">{encounter.score}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </>
  );
}

function Evidence({ encounter }: { encounter: Encounter }) {
  const quoted = encounter.markers.filter(
    (m) => m.provenance === "rule" && m.evidence,
  );
  if (quoted.length === 0) return null;
  return (
    <section>
      <h4>Read from the note</h4>
      <div className="quotes">
        {quoted.map((m) => (
          <div className="quote" key={m.id}>
            <div className="quote-head">
              {m.id} = {m.value}
              <span className="quote-src">{m.source}</span>
            </div>
            <p>{m.evidence}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function Superseded({ encounter }: { encounter: Encounter }) {
  if (encounter.superseded.length === 0) return null;
  return (
    <section>
      <h4>Displaced by a structured field</h4>
      <pre className="trace">
        {encounter.superseded
          .map((m) => `${m.id} = ${m.value}  (${m.provenance})  ${m.source}`)
          .join("\n")}
      </pre>
    </section>
  );
}

function CaseRow({ encounter }: { encounter: Encounter }) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="case"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        <div className="case-row">
          <div>{encounter.id}</div>
          <div>
            {encounter.cpt}
            {encounter.increasedService && (
              <span
                className="mod22"
                title="Modifier 22, increased procedural services: the surgeon's own claim that this case was substantially harder than typical. Recorded beside the score, never fed into it."
              >
                -22
              </span>
            )}
          </div>
          <div className="num">{encounter.score}</div>
          <div className="num">{encounter.minutes}</div>
          <div>{encounter.payer.split(" ").slice(0, 2).join(" ")}</div>
          <div className="num">{money(encounter.realized)}</div>
          <div className="num">
            <span className={`pill ${ratioBand(encounter.ratio)}`}>
              {encounter.ratio == null ? "excl" : encounter.ratio.toFixed(2)}
            </span>
          </div>
        </div>
      </summary>
      {open && (
        <div className="case-detail">
          <div>
            <h4>Layer A derivation</h4>
            <MarkerTable encounter={encounter} />
            <Superseded encounter={encounter} />
          </div>
          <div>
            <section>
              <h4>{encounter.ratio == null ? "Excluded" : "Adequacy"}</h4>
              <pre className="trace">
                {encounter.ratio == null
                  ? "No ratio. Either the score lies outside the range the schedule curve was fitted on,\nor the payer has too few comparator encounters for a multiplier.\nThe curve is an observation, not a guess. Excluded rather than extrapolated."
                  : encounter.adequacyTrace.join("\n")}
              </pre>
              {encounter.realizedClaim !== encounter.realized && (
                <p className="page-note">
                  The claim carried {money(encounter.realizedClaim)} across all
                  lines; the ratio uses the {money(encounter.realized)} on the
                  primary procedure's line, because that is what the expected
                  payment prices.
                </p>
              )}
            </section>
            <Evidence encounter={encounter} />
            {encounter.denied.length > 0 && (
              <section>
                <h4>Denied lines</h4>
                <pre className="trace">
                  {encounter.denied
                    .map(
                      (d) =>
                        `${d.cpt}   allowed 0.00   CARC ${d.codes.join(", ")}`,
                    )
                    .join("\n")}
                </pre>
              </section>
            )}
          </div>
        </div>
      )}
    </details>
  );
}

const PAGE = 25;

export function CaseTable({
  study,
  codes,
}: {
  study: Encounter[];
  codes: string[];
}) {
  const [code, setCode] = useState("all");
  // 300 encounters at full height put ten thousand pixels between the reader and
  // anything below this panel. Show a page, offer the rest.
  const [limit, setLimit] = useState(PAGE);
  const matching = code === "all" ? study : study.filter((e) => e.cpt === code);
  const rows = matching.slice(0, limit);

  return (
    <div className="panel">
      <h3>Case by case</h3>
      <p className="note">
        Every study encounter, with the arithmetic that produced its score and
        its ratio.
      </p>
      <div className="filters">
        {["all", ...codes].map((key) => (
          <button
            key={key}
            type="button"
            aria-pressed={key === code}
            onClick={() => {
              setCode(key);
              setLimit(PAGE);
            }}
          >
            {key === "all" ? "All" : `CPT ${key}`}
          </button>
        ))}
      </div>
      <div className="case-row case-head">
        <div>encounter</div>
        <div>code</div>
        <div className="num">score</div>
        <div className="num">minutes</div>
        <div>payer</div>
        <div className="num">realized</div>
        <div className="num">ratio</div>
      </div>
      {rows.map((encounter) => (
        <CaseRow key={encounter.id} encounter={encounter} />
      ))}
      {matching.length > rows.length && (
        <button
          type="button"
          className="btn-quiet more"
          onClick={() => setLimit(matching.length)}
        >
          Show all {matching.length} encounters
        </button>
      )}
    </div>
  );
}
