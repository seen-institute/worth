import { useEffect, useState, type FormEvent } from "react";

import {
  describe,
  getLocalities,
  getVintages,
  price,
  type Derivation,
  type Locality,
  type PriceRequest,
} from "../api/client";
import { type Remote, RemoteState, useRemote } from "../useRemote";

type Setting = PriceRequest["setting"];
type Basis = PriceRequest["paymentBasis"];

const SETTINGS: { value: Setting; label: string }[] = [
  { value: "non-facility", label: "Non-facility (office)" },
  { value: "facility", label: "Facility (hospital, ASC)" },
];

const BASES: { value: Basis; label: string }[] = [
  { value: "non-qualifying-apm", label: "Standard" },
  { value: "qualifying-apm", label: "Qualifying APM" },
];

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function dollars(amount: string): string {
  const n = Number(amount);
  return Number.isFinite(n)
    ? n.toLocaleString("en-US", { style: "currency", currency: "USD" })
    : `$${amount}`;
}

function Receipts({ result }: { result: Derivation }) {
  return (
    <div className="receipts">
      {result.source.files.map((f, i) => (
        // The chain is flattened, so the archive link repeats once per file
        // that derives from it; the position is the identity here.
        <div className="receipt" key={`${i}:${f.sha256}`}>
          <div>
            <div>{f.filename}</div>
            <div className="muted">{f.role}</div>
          </div>
          <div>
            <div className="sha">sha256 {f.sha256}</div>
            <div className="muted">
              released {f.releaseDate}
              {f.note ? ` · ${f.note}` : ""}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function Result({ result }: { result: Derivation }) {
  const i = result.inputs;
  return (
    <div className="stack">
      <div className="tiles">
        <div className="tile lead">
          <div className="tile-k">Allowed amount</div>
          <div className="price-amount">
            {dollars(result.amount)}
            <small>
              {result.code}
              {result.modifier ? `-${result.modifier}` : ""} · {result.locality}{" "}
              {result.localityName} · {result.placeOfService} · CY{result.ruleYear} Q
              {result.quarter} · {result.paymentBasis}
            </small>
          </div>
          <div className="tile-s">
            A schedule figure, not a payment. Medicare pays 80% of it and the
            patient owes the rest.
          </div>
        </div>
        <div className="tile">
          <div className="tile-k">Work RVU × GPCI</div>
          <div className="tile-v">{i.workRvu}</div>
          <div className="tile-s">× {i.workGpci}</div>
        </div>
        <div className="tile">
          <div className="tile-k">PE RVU × GPCI</div>
          <div className="tile-v">{i.peRvu}</div>
          <div className="tile-s">× {i.peGpci}</div>
        </div>
        <div className="tile">
          <div className="tile-k">MP RVU × GPCI</div>
          <div className="tile-v">{i.mpRvu}</div>
          <div className="tile-s">× {i.mpGpci}</div>
        </div>
        <div className="tile">
          <div className="tile-k">Conversion factor</div>
          <div className="tile-v">{i.conversionFactor}</div>
          <div className="tile-s">dollars per adjusted RVU</div>
        </div>
      </div>

      <div className="panel">
        <h3>The arithmetic</h3>
        <p className="note">
          Every product and running total at full precision, so the amount can
          be redone on paper. No binary floating point touches the money.
        </p>
        <pre className="trace">{result.trace.join("\n")}</pre>
      </div>

      <div className="panel">
        <h3>The receipts</h3>
        <p className="note">
          The hash of every file the number came from, chained back to the CMS
          archive. Re-download, re-hash, and you do not have to trust this page.
        </p>
        <Receipts result={result} />
      </div>
    </div>
  );
}

export function PricePage() {
  const [vintages] = useRemote(getVintages);

  const [code, setCode] = useState("99213");
  const [date, setDate] = useState(today);
  const [locality, setLocality] = useState("");
  const [setting, setSetting] = useState<Setting>("non-facility");
  const [basis, setBasis] = useState<Basis>("non-qualifying-apm");
  const [modifiers, setModifiers] = useState("");

  const [localities, setLocalities] = useState<Remote<Locality[]>>({
    status: "loading",
  });
  const [result, setResult] = useState<Remote<Derivation> | null>(null);

  // The locality list belongs to a vintage, and the date picks the vintage.
  useEffect(() => {
    let live = true;
    setLocalities({ status: "loading" });
    getLocalities(date).then(
      (list) => {
        if (!live) return;
        setLocalities({ status: "ok", data: list });
        setLocality((current) =>
          list.some((l) => l.locality === current)
            ? current
            : (list.find((l) => l.locality === "CA18") ?? list[0])?.locality ?? "",
        );
      },
      (error: unknown) =>
        live && setLocalities({ status: "error", error: describe(error) }),
    );
    return () => {
      live = false;
    };
  }, [date]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setResult({ status: "loading" });
    price({
      code: code.trim(),
      locality,
      date,
      setting,
      paymentBasis: basis,
      modifiers: modifiers
        .split(/[,\s]+/)
        .map((m) => m.trim())
        .filter(Boolean),
    }).then(
      (data) => setResult({ status: "ok", data }),
      (error: unknown) =>
        setResult({ status: "error", error: describe(error) }),
    );
  };

  return (
    <div className="page">
      <section className="intro">
        <h1>Fee schedule allowances.</h1>
        <p>
          One code, one Medicare locality, one service date, one setting. This exact tool
          exists on the CMS website. The full data needed to derive these values is freely 
          provided but their tool explicitly dissalows use in the way we need. This entire 
          tab is for informational purposes, Mt. Sinai will never directly use this. The 
          data that it provides however is used to dollar denominate the the 'complexity 
          adjusted expected price' of a case, i.e. the denominator of the adequacy ratio.
        </p>
      </section>

      <section className="band">
        <div className="eyebrow">The query</div>
        <form className="price-form" onSubmit={submit}>
          <label>
            code
            <input
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="99213"
              maxLength={5}
              required
            />
          </label>
          <label>
            service date
            <input
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              required
            />
          </label>
          <label>
            locality
            <select
              value={locality}
              onChange={(e) => setLocality(e.target.value)}
              disabled={localities.status !== "ok"}
              required
            >
              {localities.status === "ok" &&
                localities.data.map((l) => (
                  <option key={l.locality} value={l.locality}>
                    {l.locality} · {l.name}
                  </option>
                ))}
            </select>
          </label>
          <label>
            setting
            <select
              value={setting}
              onChange={(e) => setSetting(e.target.value as Setting)}
            >
              {SETTINGS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            basis
            <select
              value={basis}
              onChange={(e) => setBasis(e.target.value as Basis)}
            >
              {BASES.map((b) => (
                <option key={b.value} value={b.value}>
                  {b.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            modifiers
            <input
              value={modifiers}
              onChange={(e) => setModifiers(e.target.value)}
              placeholder="26, TC"
            />
          </label>
          <button
            type="submit"
            className="btn"
            disabled={localities.status !== "ok" || result?.status === "loading"}
          >
            {result?.status === "loading" ? "Pricing" : "Price it"}
          </button>
        </form>
        {localities.status === "error" && (
          <p className="remote error">
            No localities for that date. {localities.error}
          </p>
        )}
      </section>

      {result?.status === "error" && (
        <section className="band">
          <div className="eyebrow">Refused</div>
          <p className="remote error">{result.error}</p>
          <p className="page-note">
            A refusal is a result. Most ways of getting this wrong produce a
            plausible number instead of an error, which is exactly what an
            explicit refusal is there to prevent.
          </p>
        </section>
      )}

      {result?.status === "ok" && (
        <section className="band">
          <div className="eyebrow">The answer</div>
          <Result result={result.data} />
        </section>
      )}

      <section className="band">
        <div className="eyebrow">Pinned releases</div>
        <RemoteState state={vintages} what="the pinned releases" />
        {vintages.status === "ok" && (
          <div className="scroll">
            <table>
              <thead>
                <tr>
                  <th>release</th>
                  <th>governs service dates</th>
                  <th>published</th>
                  <th>priced from</th>
                  <th className="num">codes</th>
                  <th className="num">localities</th>
                </tr>
              </thead>
              <tbody>
                {vintages.data.map((v) => (
                  <tr key={v.key}>
                    <td>{v.label}</td>
                    <td>
                      {v.effectiveFrom} to {v.effectiveTo}
                    </td>
                    <td>{v.releasedOn}</td>
                    <td>
                      {v.origin}
                      {v.coverage === "fixture"
                        ? ", committed sample only"
                        : ", full CMS release"}
                    </td>
                    <td className="num">{v.codes}</td>
                    <td className="num">{v.localities}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="page-note">
          Exactly one release is in force on any given date, and a
          first-quarter claim is never priced at third-quarter rates.
        </p>
      </section>
    </div>
  );
}
