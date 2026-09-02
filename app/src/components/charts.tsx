import { useState } from "react";
import {
  CartesianGrid,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { dollars } from "../format";
import { usePalette, type Palette } from "../theme";
import type { Encounter, MultiplierRow, ScheduleCurve } from "../types";

interface Point {
  x: number;
  y: number;
  id?: string;
  cpt?: string;
  payer?: string;
}

interface TipProps {
  active?: boolean;
  payload?: { payload: Point }[];
}

const HEIGHT = 320;

function axisProps(palette: Palette) {
  return {
    tick: { fill: palette["--muted"], fontSize: 11 },
    stroke: palette["--rule"],
    tickLine: false,
  } as const;
}

function label(text: string, palette: Palette) {
  return { value: text, fill: palette["--muted"], fontSize: 11 } as const;
}

/* ------------------------------------------------------------------ curve */

function CurveTip({ active, payload }: TipProps) {
  const point = payload?.[0]?.payload;
  if (!active || !point) return null;
  return (
    <div className="tip">
      {point.id && (
        <div>
          <b>{point.cpt}</b> · {point.id}
          {point.payer ? ` · ${point.payer}` : ""}
        </div>
      )}
      <div>
        score {point.x} · {dollars(point.y)}
      </div>
    </div>
  );
}

export function CurveChart({
  curve,
  encounters,
  multipliers,
}: {
  curve: ScheduleCurve;
  encounters: Encounter[];
  multipliers: MultiplierRow[];
}) {
  const palette = usePalette();
  const multiple = new Map(multipliers.map((m) => [m.payer, m.value]));

  // Comparators are plotted at what the fee schedule pays them, the points the
  // line was fitted on. Study encounters are plotted at their realized payment
  // divided by their payer's multiple, so both sit on the Medicare scale.
  const comparator: Point[] = encounters
    .filter((e) => e.cohort === "comparator" && e.pfs)
    .map((e) => ({ x: e.score, y: e.pfs?.referenceAmount ?? 0, id: e.id, cpt: e.cpt }));
  const study: Point[] = encounters
    .filter((e) => e.cohort === "study" && multiple.has(e.payer))
    .map((e) => ({
      x: e.score,
      y: e.realized / (multiple.get(e.payer) ?? 1),
      id: e.id,
      cpt: e.cpt,
      payer: e.payer,
    }));

  return (
    <div className="panel">
      <h3>The fee schedule's complexity relation</h3>
      <p className="note">
        Every comparator encounter priced through the Medicare Physician Fee
        Schedule at CMS {curve.release}, {curve.locality} {curve.localityName},{" "}
        {curve.setting} setting, and a line fitted by least squares across the
        whole comparator cohort (n={curve.n}, R² {curve.r2.toFixed(3)}). No
        payer in it. Study encounters are plotted at their realized payment
        divided by their payer's multiple of the schedule, so where a study
        point sits below the line, the payer paid less for that measured
        complexity than the schedule pays for it elsewhere in medicine.
      </p>

      <div className="legend">
        <span>
          <i className="swatch" style={{ background: palette["--accent"] }} />
          Comparator, at the fee schedule
        </span>
        <span>
          <i
            className="swatch diamond"
            style={{ background: palette["--coral"] }}
          />
          Study, realized ÷ payer multiple
        </span>
        <span>
          <i className="rule-line" />
          Fitted curve
        </span>
      </div>

      <ResponsiveContainer width="100%" height={HEIGHT}>
        <ScatterChart margin={{ top: 8, right: 16, bottom: 24, left: 8 }}>
          <CartesianGrid stroke={palette["--rule"]} />
          <XAxis
            type="number"
            dataKey="x"
            domain={[0, 100]}
            label={{
              ...label("Layer A complexity score", palette),
              position: "bottom",
              dy: 8,
            }}
            {...axisProps(palette)}
          />
          <YAxis
            type="number"
            dataKey="y"
            tickFormatter={dollars}
            label={{
              ...label("Medicare PFS dollars", palette),
              angle: -90,
              position: "insideLeft",
            }}
            {...axisProps(palette)}
          />
          <Tooltip
            content={<CurveTip />}
            cursor={{ stroke: palette["--rule-strong"] }}
          />
          <ReferenceLine
            segment={[
              { x: curve.xMin, y: curve.intercept + curve.slope * curve.xMin },
              { x: curve.xMax, y: curve.intercept + curve.slope * curve.xMax },
            ]}
            stroke={palette["--body"]}
            strokeDasharray="5 4"
            strokeWidth={2}
            ifOverflow="extendDomain"
          />
          <Scatter
            isAnimationActive={false}
            name="Comparator"
            data={comparator}
            fill={palette["--accent"]}
          />
          <Scatter
            isAnimationActive={false}
            name="Study"
            data={study}
            fill={palette["--coral"]}
            shape="diamond"
          />
        </ScatterChart>
      </ResponsiveContainer>
      <p className="page-note">
        Receipt: CMS {curve.release}, archive sha256 {curve.archiveSha256}.
      </p>
    </div>
  );
}

/* --------------------------------------------------------------- adequacy */

function RatioTip({ active, payload }: TipProps) {
  const point = payload?.[0]?.payload;
  if (!active || !point) return null;
  return (
    <div className="tip">
      <div>
        <b>{point.cpt}</b> · {point.id}
      </div>
      <div>
        score {point.x} · ratio {point.y.toFixed(2)}
      </div>
    </div>
  );
}

export function AdequacyChart({ study }: { study: Encounter[] }) {
  const palette = usePalette();
  const scored = study.filter((e) => e.ratio != null);
  const below = scored
    .filter((e) => (e.ratio ?? 0) < 1)
    .map((e) => ({ x: e.score, y: e.ratio as number, id: e.id, cpt: e.cpt }));
  const at = scored
    .filter((e) => (e.ratio ?? 0) >= 1)
    .map((e) => ({ x: e.score, y: e.ratio as number, id: e.id, cpt: e.cpt }));

  return (
    <div className="panel">
      <h3>Adequacy by case</h3>
      <p className="note">
        Every scored study encounter. One means paid consistently with
        equivalent measured complexity. The ratio falls as complexity rises,
        because the code pays one amount across its whole range.
      </p>

      <div className="legend" style={{ marginTop: 14 }}>
        <span>
          <i className="swatch" style={{ background: palette["--accent"] }} />
          At or above parity
        </span>
        <span>
          <i className="swatch" style={{ background: palette["--coral"] }} />
          Below parity
        </span>
      </div>

      <ResponsiveContainer width="100%" height={HEIGHT}>
        <ScatterChart margin={{ top: 8, right: 16, bottom: 24, left: 8 }}>
          <CartesianGrid stroke={palette["--rule"]} />
          <XAxis
            type="number"
            dataKey="x"
            domain={[0, 100]}
            label={{
              ...label("Layer A complexity score", palette),
              position: "bottom",
              dy: 8,
            }}
            {...axisProps(palette)}
          />
          <YAxis
            type="number"
            dataKey="y"
            domain={[0, "auto"]}
            tickFormatter={(v: number) => v.toFixed(2)}
            label={{
              ...label("Ratio", palette),
              angle: -90,
              position: "insideLeft",
            }}
            {...axisProps(palette)}
          />
          <Tooltip
            content={<RatioTip />}
            cursor={{ stroke: palette["--rule-strong"] }}
          />
          <ReferenceLine y={1} stroke={palette["--body"]} strokeWidth={1.5} />
          <Scatter
            isAnimationActive={false}
            name="Below parity"
            data={below}
            fill={palette["--coral"]}
          />
          <Scatter
            isAnimationActive={false}
            name="At parity"
            data={at}
            fill={palette["--accent"]}
          />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ---------------------------------------------------------------- slope */

function SlopeTip({ active, payload }: TipProps) {
  const point = payload?.[0]?.payload;
  if (!active || !point) return null;
  return (
    <div className="tip">
      <div>
        <b>{point.id}</b>
      </div>
      <div>
        score {point.x} · {dollars(point.y)}
      </div>
    </div>
  );
}

export function SlopeChart({
  study,
  codes,
  thinStrata,
}: {
  study: Encounter[];
  codes: string[];
  thinStrata: string[];
}) {
  const palette = usePalette();
  const [code, setCode] = useState(codes[0] ?? "");
  const rows = study.filter((e) => e.cpt === code);
  const payers = [...new Set(rows.map((e) => e.payer))].sort();
  const series = [
    palette["--accent"],
    palette["--coral"],
    palette["--body"],
    palette["--muted"],
  ];

  return (
    <div className="panel">
      <h3>Method 0. Payment against complexity, within one code</h3>
      <p className="note">
        One code, one payer at a time. A flat band is the finding: the code does
        not differentiate.{" "}
        {thinStrata.length > 0 &&
          "No stratum in this cohort reaches the eight-encounter minimum, so no slope is fitted. Method 0 needs volume; the MVP calls for twenty-four months."}
      </p>

      <div className="chart-head">
        <select
          aria-label="Procedure code"
          value={code}
          onChange={(event) => setCode(event.target.value)}
        >
          {codes.map((c) => (
            <option key={c} value={c}>
              CPT {c}
            </option>
          ))}
        </select>
      </div>

      <ResponsiveContainer width="100%" height={HEIGHT}>
        <ScatterChart margin={{ top: 8, right: 16, bottom: 40, left: 8 }}>
          <CartesianGrid stroke={palette["--rule"]} />
          <XAxis
            type="number"
            dataKey="x"
            domain={[0, 100]}
            label={{
              ...label("Layer A complexity score", palette),
              position: "bottom",
              dy: 8,
            }}
            {...axisProps(palette)}
          />
          <YAxis
            type="number"
            dataKey="y"
            tickFormatter={dollars}
            label={{
              ...label("Allowed", palette),
              angle: -90,
              position: "insideLeft",
            }}
            {...axisProps(palette)}
          />
          <Tooltip
            content={<SlopeTip />}
            cursor={{ stroke: palette["--rule-strong"] }}
          />
          <Legend
            verticalAlign="bottom"
            align="center"
            wrapperStyle={{
              fontSize: 11.5,
              color: palette["--body"],
              paddingTop: 22,
            }}
          />
          {payers.map((payer, i) => (
            <Scatter
              key={payer}
              isAnimationActive={false}
              name={payer}
              fill={series[i % series.length]}
              data={rows
                .filter((e) => e.payer === payer)
                .map((e) => ({ x: e.score, y: e.realized, id: e.id }))}
            />
          ))}
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}
