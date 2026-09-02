import type {
  Encounter,
  IndexRecord,
  MultiplierRow,
  ScheduleCurve,
  Summary,
} from "../types";
import { CaseTable } from "./CaseTable";
import { AdequacyChart, CurveChart, SlopeChart } from "./charts";
import { AdequacyIndex, MethodZero, ReferenceDistribution } from "./Registry";
import { SyntheticTag } from "./Masthead";

function adequacyRange(records: IndexRecord[]): string {
  // The spread across codes, not an average of them. A reader wants the shape of
  // the result before the derivation. A mean across codes would be the blended
  // figure the methodology has no place for.
  const values = records
    .map((r) => r.adequacy)
    .filter((v): v is number => v !== null)
    .sort((a, b) => a - b);
  const low = values[0];
  const high = values[values.length - 1];
  if (low === undefined || high === undefined) return "n/a";
  return `${low.toFixed(2)} – ${high.toFixed(2)}`;
}

function Tile({
  k,
  v,
  s,
  mark,
}: {
  k: string;
  v: string;
  s: string;
  mark?: boolean;
}) {
  return (
    <div className="tile">
      <div className="tile-k">
        {k} {mark && <SyntheticTag />}
      </div>
      <div className="tile-v">{v}</div>
      <div className="tile-s">{s}</div>
    </div>
  );
}

function Multipliers({
  multipliers,
  thinPayers,
}: {
  multipliers: MultiplierRow[];
  thinPayers: string[];
}) {
  return (
    <div className="panel">
      <h3>Each payer's multiple of the schedule</h3>
      <p className="note">
        Measured, not assumed: for each blinded payer, the median of realized
        payment over the fee-schedule amount across its own comparator
        encounters, each priced at the release in force on its service date.
        This is a contract level. It stays with the partner, feeds the expected
        payment, and never enters an index record.
      </p>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>payer</th>
              <th className="num">n</th>
              <th className="num">multiple</th>
              <th className="num">IQR</th>
            </tr>
          </thead>
          <tbody>
            {multipliers.map((m) => (
              <tr key={m.payer}>
                <td>{m.payer}</td>
                <td className="num">{m.n}</td>
                <td className="num">× {m.value.toFixed(4)}</td>
                <td className="num">
                  {m.q1.toFixed(4)} – {m.q3.toFixed(4)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {thinPayers.length > 0 && (
        <ul className="plain">
          {thinPayers.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function Output({
  summary,
  records,
  curve,
  multipliers,
  thinPayers,
  encounters,
  thinStrata,
}: {
  summary: Summary;
  records: IndexRecord[];
  curve: ScheduleCurve;
  multipliers: MultiplierRow[];
  thinPayers: string[];
  encounters: Encounter[];
  thinStrata: string[];
}) {
  const study = encounters.filter((e) => e.cohort === "study");

  return (
    <div className="stack">
      <div className="tiles">
        <Tile
          k="Adequacy Range"
          v={adequacyRange(records)}
          s={`across ${records.length} codes`}
          mark
        />
        <Tile
          k="Encounters"
          v={`${summary.study} / ${summary.comparator}`}
          s="study / comparator"
        />
        <Tile
          k="Linkage"
          v={`${(summary.linkageRate * 100).toFixed(2)}%`}
          s="encounters matched to remittance"
        />
        <Tile
          k="Withheld"
          v={`${summary.suppressedStrata} / ${summary.strata}`}
          s="strata below the suppression floor"
        />
        <Tile
          k="Priced in"
          v={`${summary.locality} · ${summary.setting}`}
          s={`${summary.localityName}, schedule curve at CMS ${summary.referenceRelease}`}
        />
      </div>

      <div className="tier">
        <h2>Defensible from the first validated extract</h2>
        <p>
          No reference standard, no curve, nothing normative. Both outputs below
          are available as soon as one partner's data validates.
        </p>
      </div>
      <ReferenceDistribution records={records} />
      <MethodZero records={records} />

      <div className="tier research">
        <h2>Research stage. Depends on the cross-specialty curve.</h2>
        <p>
          The function converting a complexity differential into an
          expected-payment differential is the part the methodology says will
          take longer, to be derived with clinical advisors and biostatisticians
          rather than asserted. Here it is the Medicare fee schedule's own
          relation, read off the comparator cohort, times each payer's measured
          multiple of that schedule. Everything below rests on it.
        </p>
      </div>
      <AdequacyIndex records={records} />
      <CurveChart curve={curve} encounters={encounters} multipliers={multipliers} />
      <Multipliers multipliers={multipliers} thinPayers={thinPayers} />
      <AdequacyChart study={study} />
      <SlopeChart study={study} codes={summary.codes} thinStrata={thinStrata} />
      <CaseTable study={study} codes={summary.codes} />
    </div>
  );
}
