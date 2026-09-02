import type { MarkerRule, NoteEngine, RulePack } from "../types";

function Weights({ pack }: { pack: RulePack }) {
  const sum = (source: string) =>
    pack.markers
      .filter((m) => m.provenance === source)
      .reduce((a, m) => a + m.weight, 0);
  const fields = sum("structured");
  const notes = sum("rule");

  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            <th>marker</th>
            <th>source</th>
            <th className="num">weight</th>
            <th className="num">scale</th>
            <th>where it is read</th>
          </tr>
        </thead>
        <tbody>
          {pack.markers.map((m) => (
            <tr key={m.id}>
              <td>{m.id}</td>
              <td>{m.provenance === "rule" ? "note" : "field"}</td>
              <td className="num">{m.weight.toFixed(2)}</td>
              <td className="num">
                {m.anchorLow} to {m.anchorHigh}
              </td>
              <td>
                {m.provenance === "rule"
                  ? m.sections.join(", ")
                  : m.patterns.length
                    ? "the operating-room record, the note if the field is empty"
                    : "the operating-room record"}
              </td>
            </tr>
          ))}
          <tr className="total">
            <td>total</td>
            <td />
            <td className="num">{(fields + notes).toFixed(2)}</td>
            <td className="num" />
            <td>
              {fields.toFixed(2)} from fields, {notes.toFixed(2)} from notes
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function Patterns({ marker }: { marker: MarkerRule }) {
  return (
    <div className="rule-block">
      <div className="rule-head">
        <span className="rule-name">{marker.id}</span>
        <span className="rule-meta">
          weight {marker.weight.toFixed(2)} · scale {marker.anchorLow} to{" "}
          {marker.anchorHigh}
        </span>
      </div>
      {marker.note && <p className="rule-note">{marker.note}</p>}
      <table>
        <thead>
          <tr>
            <th>match</th>
            <th className="num">value</th>
            <th>expression</th>
          </tr>
        </thead>
        <tbody>
          {marker.patterns.map((p) => (
            <tr key={p.id}>
              <td>{p.id}</td>
              <td className="num">{p.value === null ? "captured" : p.value}</td>
              <td className="regex">{p.regex}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="rule-scope">
        Reads {marker.sections.join(", ")}. Never reads{" "}
        {marker.excludedSections.join(", ")}.
      </p>
    </div>
  );
}

export function ScorePage({
  pack,
  engine,
}: {
  pack: RulePack;
  engine: NoteEngine;
}) {
  const narrative = pack.markers.filter((m) => m.patterns.length > 0);

  return (
    <div className="page">
      <section className="intro">
        <h1>How the complexity score is made.</h1>
        <p>
          One number per encounter, 0–100. Ordinal: it ranks cases against the
          reference distribution for their code. It is never divided into
          payment.
        </p>
        <p>
          Everything below is Layer A. Facts come from structured fields plus
          rule-based note reading: regex, section parsing, negation detection. No AI/ML.
        </p>
        <p className="flag">
          Rule pack {pack.id} v{pack.version}, {pack.digest.slice(0, 12)},
          marked {pack.status}. The weights below are mine and Claude's best v1.
          The methodology requires them to be set by clinical
          advisory input and statistical analysis of which markers predict
          resource intensity.
        </p>
      </section>

      <section className="band">
        <div className="eyebrow">Step one. Extraction</div>
        <p className="page-note">
          Eleven markers per encounter. Eight come from discrete fields the
          hospital already populates. Three come from the operative note,
          because no system has a field for whether the ureter had to be
          dissected free.
        </p>
        <Weights pack={pack} />
        <p className="page-note">
          Some fields, like operative minutes, are present as a structured value in field data.
          However, the clinical notes could also reference it. To handle this, there is a defined
          precedence for which source to use. The structured field is preferred, and the note is
          only used if the field is empty. It is not averaged or otherwise computed from both sources.
        </p>
      </section>

      <section className="band">
        <div className="eyebrow">Step two. Normalisation</div>
        <p className="page-note">
          Every marker is scored between two fixed points, published in the rule
          pack and shown as the scale above. A case at or below the low point
          scores nothing for that marker. At or above the high point it scores
          the full weight. In between it scores proportionally. A five-hour case
          and a case with a second surgical team are put on the same footing
          that way.
        </p>
        <p className="page-note">
          Those two points are fixed. They are not the top and bottom of
          whatever cases happen to be in the data. A score set against the
          observed spread would change every time an encounter was added, so a
          figure published in November would not be comparable with itself in
          December. Fixing them costs some range and buys that comparability.
        </p>
        <p className="page-note">
          To explain further: each marker has two published anchors. Operative minutes, for example,
          run from 30 to 240. A case at 30 minutes or less contributes nothing for 
          that marker. A case at 240 minutes or more contributes the marker's full 
          weight, 22 points of the 100. A 135-minute case sits halfway between the 
          anchors, so it contributes half: 11 points. Every marker is converted the 
          same way, so a blood loss of 300 ml (anchors 0 to 600) and an ASA class of 
          2.5 (anchors 1 to 4) both become "halfway," and only then are the weights 
          applied. That's what "put on the same footing" means: minutes, millilitres, 
          counts and grades all become fractions of their own range before they are added up.
        </p>
      </section>

      <section className="band">
        <div className="eyebrow">Step three. Weighting</div>
        <p className="page-note">
          Weighted sum of the normalised markers, times one hundred, rounded.
        </p>
      </section>

      <section className="band">
        <div className="eyebrow">Reading the notes</div>
        <p className="page-note">
          Three markers have no discrete source. They are read out of the
          operative note with the expressions below. This is crude on purpose.
          patterns, section scope, negation, nothing else.
        </p>
        {narrative.map((m) => (
          <Patterns key={m.id} marker={m} />
        ))}

        <div className="rule-block">
          <div className="rule-head">
            <span className="rule-name">section parsing</span>
          </div>
          <p className="rule-note">
            A header opens a section, either standing alone or followed by a
            colon and content on the same line. Operative notes write both.
          </p>
          <p className="regex">{engine.header}</p>
        </div>

        <div className="rule-block">
          <div className="rule-head">
            <span className="rule-name">negation and historicity</span>
          </div>
          <p className="rule-note">
            A match is refused when a trigger governs it. The window runs back{" "}
            {engine.lookback} characters to the nearest clause boundary. A lone
            newline is not a boundary. Dictated notes wrap wherever the
            transcription wrapped them.
          </p>
          <p className="regex">negation {engine.negation}</p>
          <p className="regex">historical {engine.historical}</p>
          <p className="regex">boundary {engine.terminator}</p>
        </div>
      </section>

      <section className="band">
        <div className="eyebrow">What is not in here</div>
        <p className="page-note">
          Nothing from the enrichment layers. A trained classifier and a
          generative extractor are both provided for in the marker record, and
          both are kept out of a Layer A score by where the marker was read from
          rather than by anyone remembering to leave them out. The methodology
          reserves the severity a rule cannot reach, meaning a case whose note
          never uses the word, for the generative layer, reported beside the
          score and never rewriting it.
        </p>
      </section>
    </div>
  );
}
