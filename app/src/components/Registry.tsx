import { SyntheticTag } from "./Masthead";
import type { IndexRecord } from "../types";

/**
 * The two outputs the methodology calls defensible from the first validated
 * extract: the empirical reference distribution, and the Method 0 slope. Neither
 * needs a reference standard or a curve.
 */

const SCALE_MAX = 100;

function Strip({ record }: { record: IndexRecord }) {
  const d = record.distribution;
  const pct = (v: number) => (v / SCALE_MAX) * 100;
  return (
    <div className="strip-row">
      <div className="strip-code">{record.code}</div>
      <div className="strip-track">
        <div
          className="strip-range"
          style={{ left: `${pct(d.min)}%`, width: `${pct(d.max - d.min)}%` }}
        />
        <div
          className="strip-iqr"
          style={{ left: `${pct(d.q1)}%`, width: `${pct(d.q3 - d.q1)}%` }}
        />
        <div className="strip-median" style={{ left: `${pct(d.median)}%` }} />
      </div>
      <div className="strip-stat num">{d.median.toFixed(0)}</div>
      <div className="strip-stat num muted">
        {d.q1.toFixed(0)}–{d.q3.toFixed(0)}
      </div>
      <div className="strip-stat num muted">{record.n}</div>
    </div>
  );
}

export function ReferenceDistribution({ records }: { records: IndexRecord[] }) {
  return (
    <div className="panel">
      <h3>
        Empirical reference distribution <SyntheticTag />
      </h3>
      <p className="note">
        Layer A complexity per code, as a distribution rather than a mean. A
        measurement of what the work actually looks like, available from the
        first validated extract and needing no reference standard. Method 2 has
        nothing to compare a case against without it.
      </p>
      <div className="strip-head strip-row">
        <div className="strip-code">code</div>
        <div className="strip-track">
          <span>0</span>
          <span className="strip-mid">50</span>
          <span className="strip-end">100</span>
        </div>
        <div className="strip-stat num">median</div>
        <div className="strip-stat num">IQR</div>
        <div className="strip-stat num">n</div>
      </div>
      {records.map((r) => (
        <Strip key={r.code} record={r} />
      ))}
    </div>
  );
}

function Forest({ record }: { record: IndexRecord }) {
  const fitted = record.slopes.filter((s) => !s.suppressed);
  const bound = Math.max(
    0.0001,
    ...fitted.flatMap((s) => [Math.abs(s.ciLow), Math.abs(s.ciHigh)]),
  );
  const pct = (v: number) => ((v + bound) / (2 * bound)) * 100;

  return (
    <div className="forest">
      <div className="forest-code">CPT {record.code}</div>
      {record.slopes.map((s) => (
        <div className="forest-row" key={s.payer}>
          <div className="forest-payer">{s.payer}</div>
          <div className="forest-n num">n={s.n}</div>
          {s.suppressed ? (
            <div className="forest-track withheld">
              withheld · n below the suppression floor
            </div>
          ) : (
            <>
              <div className="forest-track">
                <div className="forest-zero" style={{ left: "50%" }} />
                <div
                  className={
                    s.differentiates ? "forest-ci differentiates" : "forest-ci"
                  }
                  style={{
                    left: `${pct(s.ciLow)}%`,
                    width: `${pct(s.ciHigh) - pct(s.ciLow)}%`,
                  }}
                />
                <div
                  className="forest-point"
                  style={{ left: `${pct(s.slope)}%` }}
                />
              </div>
              <div className="forest-value num">
                {s.slope.toFixed(3)}
                <span className="forest-ci-text">
                  [{s.ciLow.toFixed(2)}, {s.ciHigh.toFixed(2)}]
                </span>
              </div>
            </>
          )}
        </div>
      ))}
    </div>
  );
}

export function MethodZero({ records }: { records: IndexRecord[] }) {
  return (
    <div className="panel">
      <h3>
        Method 0. Does payment track complexity within a code? <SyntheticTag />
      </h3>
      <p className="note">
        One code, one payer at a time. Dollars per complexity point, with a 95%
        interval. An interval spanning the zero line means the code has not been
        shown to pay differently for harder work. That is the finding, not a
        null result. Payers are blinded. The index reports whether an
        institution's own payment matched its own work. It never reports what a
        payer pays.
      </p>
      {records.map((r) => (
        <Forest key={r.code} record={r} />
      ))}
    </div>
  );
}

export function AdequacyIndex({ records }: { records: IndexRecord[] }) {
  return (
    <div className="panel">
      <h3>
        Payment adequacy index <SyntheticTag />
      </h3>
      <p className="note">
        Realized payment over complexity-matched expected payment, both dollars,
        published per code per institution per period. No figure is reported
        across codes: each code is valued separately, fails separately and is
        petitioned separately, so a blended number names no reform lever.
      </p>
      <div className="scroll" style={{ marginTop: 14 }}>
        <table>
          <thead>
            <tr>
              <th>code</th>
              <th>period</th>
              <th className="num">n</th>
              <th className="num">scored</th>
              <th className="num">excluded</th>
              <th className="num">ratio</th>
              <th className="num">95% CI</th>
            </tr>
          </thead>
          <tbody>
            {records.map((r) => (
              <tr key={r.code}>
                <td>{r.code}</td>
                <td>
                  {r.periodStart} → {r.periodEnd}
                </td>
                <td className="num">{r.n}</td>
                <td className="num">{r.scored}</td>
                <td className="num">{r.excluded}</td>
                <td className="num">
                  {r.suppressed || r.adequacy === null
                    ? "withheld"
                    : r.adequacy.toFixed(2)}
                </td>
                <td className="num">
                  {r.adequacyCiLow === null || r.adequacyCiHigh === null
                    ? "n/a"
                    : `[${r.adequacyCiLow.toFixed(2)}, ${r.adequacyCiHigh.toFixed(2)}]`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
