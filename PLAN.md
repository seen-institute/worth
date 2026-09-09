# Plan: the console as a full-stack app

Status: agreed plan, 2026-09-03. Nothing here is built. Every fork is decided; the
reasoning for each is kept so the choice can be defended later.

## 1. Current State

- `app/` is a static React 19 + Vite bundle. Everything it shows comes from
  `app/src/data/run.json`, a 2.6 MB file written by
  `packages/worth-complexity/tools/export_run.py` and compiled into the bundle.
  Roughly 1.1 MB of that is the delivered files (notes, 835s, five tables) and
  1.1 MB is the encounters with their derivations.
- The "Ingest" button on the Run tab is a timer. It replays thirteen step labels over
  four seconds and then reveals numbers that were computed at export time.
- `worth-fees` and `worth-complexity` are stdlib-only by design. Neither imports the
  other.
- **The fee schedule is not in the pipeline.** `realized` is the 835 allowed amount.
  `expected` is read off a straight line fitted, per payer, through the comparator
  cohort's own 835 amounts against their Layer A scores. No RVU, GPCI or conversion
  factor enters any number the console shows. The worth-fees README says the package
  exists "to eventually help generate a complexity matched expected payment"; that
  eventually has not happened.
- Postgres exists only as a dev convenience: `compose.yaml` starts `postgres:16`, the
  initdb hook applies `migrations/0001_fee_schedule.sql`, and `worth-fees export-sql`
  emits a psql load script. No Python code connects to it. The committed fee fixture
  covers eight codes; `export-sql --full` covers all ~19k but needs the cached CMS
  archive.
- Only CY2026 vintages are pinned (Q1 to Q4). The synthetic encounters are dated
  January 2024 to December 2025, so `vintage_for_date` raises for every one of them.
- None of the thirteen synthetic codes (five study, eight comparator) is in the
  committed fee fixture. The generator puts modifier `22` on the harder cases;
  `worth-fees` rejects `22` as unrecognised. The generator bundles every secondary
  line to $0.00, so on this dataset the primary line and the whole claim are the
  same number.
- The synthetic payers carry contract multipliers of 2.05, 1.82, 1.00 and 0.88 times
  a Medicare-like base. This matters for Fork G.
- `test_export.py` guards `run.json` against drifting from a fresh run.
- A full pipeline run over the synthetic dataset takes about 0.4 s.

## 2. Current State going

```
browser ──── Amplify Hosting (static app/dist) ───┐
   │                                              │  /api/* rewrite (same origin, no CORS)
   │                                              ▼
   └────────────────────────────────────── worth-api (Python, FastAPI, container)
                                             │      │
                          reads + serves ────┘      └──── Postgres (full CMS vintages)
                          the dataset dir                        │
                          (mssm-synthetic by default)            │ FeeSchedule built from rows
                                                                 ▼
                                   worth_complexity.pipeline.run(..., schedules=, locality=)
                                                │
                                                └── worth_fees.expected_allowed(schedule=...)
```

Four responsibilities move server-side:

1. **Host files.** The API owns a dataset directory (default: the packaged
   `mssm-synthetic` fixture, overridable by env) and serves a manifest plus each file
   on demand. The console stops carrying file contents in the bundle and fetches a
   card's contents when it is opened.
2. **Run the pipeline.** "Run" in the UI is a request. The server calls
   `worth_complexity.pipeline.run()` on the dataset and returns the same payload shape
   the console already renders.
3. **Price.** The server loads the pinned CMS vintages into Postgres at boot, builds a
   `FeeSchedule` per vintage from the rows, and passes those schedules into the run.
   The same schedules answer the standalone Price tab.
4. **Put the fee schedule in the denominator.** See section 4. This is the one part of
   the plan that changes what the numbers mean.

`worth-fees` stays stdlib-only with no driver. `worth-complexity` gains one
dependency, `worth-fees` (Fork J), and stays free of any driver or framework.
Everything with a third-party dependency lives in the new package.

## 3. Forks on the plumbing

### Fork A. Where the Python runs (decided: container on App Runner)

Amplify Hosting serves static files and Node SSR. It cannot run a Python process or a
Postgres server. "Hosted on Amplify" means Amplify serves the console and proxies
`/api/*` to an App Runner service in the same account. The same image runs in
`compose.yaml` locally, so "Postgres on bootup" means one thing in both places. If
monthly cost turns out to matter more than parity, the FastAPI app can be mounted on
Lambda with Mangum without changing routes; that is the fallback, not the plan.

### Fork B. What a price lookup is in the console (decided: both)

A standalone Price tab (`worth-fees price --json` behind a form) ships first because
it needs nothing from section 4. The fee schedule inside the adequacy ratio is the
real requirement and is planned in section 4.

### Fork C. What answers a price query (decided: Postgres)

The committed CSV fixture holds eight codes; the console has to price any of ~19k and
the pipeline has to price every comparator code. Postgres holds the full pinned
vintages. The API builds a `FeeSchedule` from rows and `expected_allowed` gains one
keyword, `schedule=`, so a caller can hand it a schedule instead of letting it load
the fixture. A drift test prices every fixture code both ways and asserts identical
amounts and traces.

### Fork D. What "Run" does on the wire (decided by default: synchronous)

`POST /api/runs` computes and returns in under a second. The server records
wall-clock time per stage so the console's step replay shows real timings against
real results instead of a fixed 150 ms cadence. Streaming is out of scope until a
partner-scale extract makes a run long enough to watch.

### Fork E. The Python to TypeScript contract (decided: generated from OpenAPI)

Pydantic response models. `openapi-typescript` writes `app/src/api/schema.d.ts`,
committed, with a CI step that regenerates and fails on a diff. The console cannot
drift from what the server sends, in the same spirit as the rule pack rendered from
the JSON the scorer loads.

### Fork F. Where infrastructure is defined (decided: separate `infra/` CDK stack)

Deployed by a GitHub Actions job on `main`. One App Runner service, one RDS instance,
one secret. Amplify Hosting gets an environment variable with the API origin and a
rewrite rule. Preview branches on Amplify all point at the same API. Amplify Gen 2's
per-branch backends were rejected because they would mean a Postgres instance per
preview branch.

## 4. The fee schedule in the denominator

The framing, settled on 2026-09-03: **the numerator is the actual payment from the
835. The denominator is the complexity-adjusted expected payment, and the fee
schedule is what turns a complexity level into dollars.**

```
adequacy = realized_835 / expected(score)
expected(score) comes from the fee schedule, adjusted for the encounter's complexity
```

This replaces today's per-payer curve fitted on comparator 835 amounts. Method 0,
linkage, the reference distribution and blinding are unchanged.

### How the fee schedule turns complexity into dollars

The comparator cohort supplies the relation. For every comparator encounter, price
its primary code through `expected_allowed` (facility setting, the institution's
locality, the vintage governing its service date) and pair that Medicare PFS amount
with its Layer A score. Fit one least-squares line through those pairs with the
existing `curve.fit`. That line is the fee schedule's own implicit price of
complexity in specialties whose valuation is not in dispute, on the Medicare scale,
with no payer in it. Every point on it carries a `worth-fees` derivation, so the
schedule curve's trace names CMS archive hashes the way the 835 side names file
hashes.

### Fork G. Whether the payer's contract level is normalised out (decided: yes, per-payer multiplier)

The 835 is in the payer's contract dollars. The fee schedule curve is in Medicare
dollars. The one remaining choice is what sits between them.

1. **Normalise by the payer's own comparator multiplier** (chosen).
   For each payer, take its comparator encounters and compute
   `realized_835 / PFS(primary code)` per encounter; the median is that payer's
   contract multiplier `m_p`. Then for a study encounter:

   ```
   expected(score) = m_p × schedule_curve(score)
   adequacy        = realized_835 / expected(score)
   ```

   Both sides are now in the same payer's dollars. The fee schedule supplies the
   complexity-to-dollar relation; the comparator cohort supplies only a scalar per
   payer. `1.00` keeps its current meaning: paid consistently with what this payer
   pays for equally complex work elsewhere in medicine. The multiplier is reported
   per blinded payer with its interquartile range, and a payer with fewer comparator
   encounters than the suppression floor gets no multiplier and no ratio.
   The schedule curve is fitted once across all comparator encounters, so it has
   the whole cohort behind it rather than a quarter of it per payer.

2. **No normalisation** (rejected). `expected(score) = schedule_curve(score)` and the 835 is
   divided straight into Medicare-scale dollars. Simpler to state. But on the
   synthetic dataset the same 0.70 undervaluation would read as 1.44 at the 2.05×
   payer and 0.62 at the 0.88× payer, so the ratio measures contract level first and
   adequacy second, and it stops being comparable across payers or institutions.
   Reasonable only if the methodology wants "relative to Medicare" as the explicit
   unit, in which case it should say so on every figure.

Under either option the index still reports per code, never across codes, is still
suppressed below the floor, still excludes rather than extrapolates outside the
fitted range, and still names no payer.

**What leaves and what stays.** The methodology notes are explicit that WORTH "does
not build rate tables" and that "the payment figure goes into a complexity ratio,
blinded by payer, and never comes back out as a rate". A payer's multiplier `m_p` is
a payer's contract level as a multiple of Medicare, which is exactly how commercial
contracts are written. So it is an internal step of the arithmetic, not an output:

- **Index records** (per code, per institution, per period: n, distribution, Method 0
  strata, ratio, interval) are what leave the partner environment and accumulate in
  the registry. They never carry `m_p`, the schedule curve's intercept in dollars, or
  any per-payer dollar figure.
- **Encounter derivations** (marker rows, the adequacy trace including `m_p` and the
  priced comparator amounts) stay with the partner. They exist so the institution
  can check its own number by hand, and they are shown in the console because the
  console runs on the partner's own data. The serializer marks them as such, and the
  no-payer-name test grows a no-multiplier-in-index-records test.

**Vintages inside the curve.** A 24-month comparator window spans rule years, and the
conversion factor and some RVUs change at the year boundary, so comparator PFS amounts
priced at their own service dates are in mixed-year dollars. Default: price each
comparator encounter twice, at its service-date vintage for `m_p` (contract versus the
schedule actually in force) and at the run's single reference vintage for the curve
(a statement about one fee schedule). The reference vintage is a run parameter shown
in the banner. This is a biostatistics question to put to the co-investigator, not
something to settle in code; the default keeps both amounts in the trace so it can
be re-fitted either way.

### Fork H. Inputs the data does not carry (decided)

- **Locality.** One institution is one locality. `or_log.facility_npi` maps to
  nothing in the data and the patient ZIP is the wrong geography. A run parameter,
  `locality`, defaulted in the API's settings, shown in the run banner, refused
  rather than guessed when absent.
- **Setting.** `PlaceOfService.FACILITY` by default; a run parameter for extracts
  that are not all OR.
- **Vintage.** From the service date. **Decided: move the synthetic dataset into
  CY2026** (one constant in the generator, one rebuild) so the pinned vintages
  cover it. Pinning CY2024 and CY2025 waits until a real extract's dates say which
  years matter.
- **Fixture coverage.** Widen the committed `worth-fees` fixture to the thirteen
  synthetic codes so the pipeline's tests and CI price them offline. `build_fixture`
  already takes a code list.

### Fork I. Panels and modifier 22 (decided: primary code on both sides, record 22)

`realized_835` becomes the allowed amount on the primary code's line rather than the
sum of all lines, and `expected` prices the primary code, so a secondary procedure
does not bias the ratio. On the synthetic dataset the two numerators are identical
because secondary lines are bundled to zero; on real data the difference is reported
as a per-encounter field so nobody has to ask where the rest of the claim went.
Modifier `22` is stripped before pricing and recorded on the encounter as a
structured fact: it is the surgeon's own complexity claim and belongs beside the
score. The multiple-procedure reduction stays out of scope.

### Fork J. The seam (decided: `worth-complexity` imports `worth-fees`)

`worth-complexity` declares `worth-fees` as a dependency (workspace source) and calls
`expected_allowed(schedule=...)` directly. It still imports no driver: `run()` takes
`schedules: Mapping[tuple[int, int], FeeSchedule] | None`, and `None` means
`worth_fees.sources.load()` from the committed fixture, which is what tests and the
CLI use. The API passes Postgres-built schedules.

One consequence to settle when the dependency lands: `worth_complexity/money.py` is a
byte-for-byte copy of `worth_fees.money`, guarded by `test_money_drift.py`, on the
grounds that an auditor should be able to read either package without installing the
other. That ground is gone once one imports the other. Default is to leave the copy
and its test alone, since nothing forces the change; the alternative is a one-line
import and deleting both files.

## 5. Repository layout after the work

```
packages/
  worth-fees/            + `schedule=` on expected_allowed; fixture widened to 13 codes
  worth-complexity/      + dependency on worth-fees; schedules/locality/setting on run()
                         + schedule curve, payer multiplier, new ratio in adequacy.py
                         + modifier 22 and primary-line realized on the encounter
                         - tools/export_run.py
  worth-api/             NEW. FastAPI + uvicorn + psycopg. Depends on both packages.
    worth_api/
      main.py            app factory, lifespan (dataset, db pool, fee-schedule load)
      settings.py        WORTH_DATASET_DIR, DATABASE_URL, WORTH_LOCALITY, WORTH_SETTING
      dataset.py         manifest, file serving, path containment
      export.py          pydantic models + the serializer moved from export_run.py
      runs.py            run + cache keyed on (dataset hash, pack digest, locality, setting, vintages)
      fees.py            FeeSchedule from Postgres rows; price endpoint
      db.py              pool, migration check, idempotent vintage load via psql
    tests/               ports of test_export.py, contract tests, db drift test
    Dockerfile           python:3.13-slim + postgresql-client + uv sync --frozen
app/
  src/api/client.ts      fetch wrappers; base URL from VITE_API_BASE (default /api)
  src/api/schema.d.ts    generated from /openapi.json, committed
  src/data/run.json      DELETED
infra/                   CDK: App Runner, RDS Postgres 16, secrets, VPC connector
compose.yaml             db (as today) + api (bind-mounted, --reload)
amplify.yml              build with VITE_API_BASE=/api; rewrite /api/<*> -> API origin
justfile                 up / down / api-dev / app-dev / types / test
.github/workflows/ci.yml + postgres service, + docker build, + types diff check
```

The API package joins the uv workspace so `uv sync`, ruff, and `mypy --strict` cover
it. It gets its own entry in `[tool.mypy] files`.

## 6. API surface

All JSON. All under `/api`. No auth for now: the dataset is synthetic, there is no
upload, and the console is already public.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/health` | package versions, rule pack digest, database status, vintages loaded, configured locality and setting |
| GET | `/api/dataset` | manifest: each file's name, kind, blurb, bytes, sha256, row or file count; study and comparator counts from `cases.encounters()` (cheap, no scoring) |
| GET | `/api/dataset/files/{name}` | one table (columns + rows), the note bundle, or the 835 bundle, in the shapes `types.ts` already has |
| GET | `/api/dataset/raw/{path}` | the bytes as received, `X-Sha256` header, for download links |
| GET | `/api/rulepack` | rule pack + note engine, so the Score tab needs no run |
| POST | `/api/runs` | body `{locality?, setting?}`; runs the pipeline; returns steps (with measured durations), records, encounters, curves, multipliers, thinStrata, summary |
| GET | `/api/runs/latest` | the cached result, or 404 before the first run |
| GET | `/api/fees/vintages` | pinned vintages and whether each is loaded |
| GET | `/api/fees/localities?date=` | valid locality codes for the vintage governing that date |
| POST | `/api/fees/price` | `{code, locality, date, setting, modifiers, paymentBasis}` in; amount, trace, sources out; the package's typed errors map to 422 with the message intact |

Path containment on the file endpoints: resolve against the dataset root and refuse
anything outside it. The dataset directory is read-only to the process.

## 7. Postgres on boot

Local, `just up`:

1. `docker compose up --wait` starts `db` (schema applied by initdb on an empty
   volume, as today) and `api` (`depends_on` with the healthcheck).
2. The API's lifespan connects, checks `to_regclass('fee_schedule_release')` and
   applies `0001_fee_schedule.sql` if it is missing. This is what makes prod work
   without an initdb hook.
3. For each pinned vintage: `SELECT 1 FROM fee_schedule_release WHERE release_id = $1`.
   If absent, render the load script with `worth_fees.sql.export()` and execute it.
   As built: the script's statements run verbatim through the driver, and its
   `COPY ... FROM STDIN` blocks are streamed through the driver's COPY, rather than
   shelling out to `psql`. Same artefact, different transport, and it works on a
   laptop without `psql` installed. A fixture-loaded release is upgraded in place
   when the archive becomes available.
4. Full vintages need the CMS archive. The Dockerfile fetches and hash-verifies it at
   image build (`fetch_archive` for each vintage), so runtime has no network egress.
   Without the archive the loader falls back to the committed fixture and says so in
   `/api/health`, and a run whose comparator codes are not all priceable reports that
   rather than a partial curve.

Production: identical, against RDS Postgres 16 (pinned to match `compose.yaml`). The
load runs once per fresh database and is a no-op on every later boot.

## 8. Frontend changes

- `App.tsx` fetches `/api/dataset` and `/api/rulepack` on mount; the intro paragraph
  and the Data tab read counts from the manifest.
- `FileCards` fetches `/api/dataset/files/{name}` when a card opens. The card header
  (name, size, hash, blurb) comes from the manifest.
- `Pipeline` gets a real `Ingest` button: POST, then replay the returned steps using
  their measured durations (scaled so the shortest is visible). "Skip to results"
  stays. The step list changes: "Fit the reference curves" becomes "Price the
  comparator cohort" and "Fit the schedule curve", plus "Derive payer multipliers"
  under Fork G option 1.
- The run banner shows locality, setting and the vintages used, because a fee-schedule
  dollar is meaningless without them.
- `CurveChart` plots the schedule curve in Medicare dollars with comparator points;
  the case table's expected column and adequacy trace show the multiplier step.
- Each encounter shows modifier 22 when present, and the primary-line versus
  whole-claim realized amounts when they differ.
- New `Price` route. Form, result, trace and sources rendered from the
  `FeeDerivation` JSON. Locality select populated from `/api/fees/localities`.
- Loading and error states on every fetch. Hash routing stays; the only rewrite the
  host needs is `/api/*`.
- Vite dev server proxies `/api` to `localhost:8000` so nothing in the app knows a
  host name.

## 9. Tests and guardrails

- `test_export.py` moves to `worth-api/tests/` almost verbatim, run against the
  serializer's output from a fresh run instead of a committed file. The invariants it
  checks (no payer name in computed output, comparator derivations not shipped, files
  exported whole) all still apply.
- Contract: `app/src/api/schema.d.ts` is generated and committed; CI regenerates from
  the running app and fails on a diff.
- Fee drift: price every committed fixture code through `load()` and through the
  database-backed schedule; assert equal amounts and equal traces.
- Generator recovery: the synthetic generator sets comparator 835 amounts from the
  real PFS amount of each code times the payer multiplier, and study amounts at 0.70
  of the schedule relation, so the pipeline recovers 0.70 per code under Fork G
  option 1 the way it does today. The generator gains a dependency on `worth-fees`
  for that, which is fine: it is a tool, not the engine.
- Adequacy invariants, unchanged in spirit: never across codes, suppressed below the
  floor, excluded rather than extrapolated, payer never named. Plus: every expected
  amount's trace names a CMS archive sha256; a run with an unpriceable comparator
  code fails loudly rather than fitting on the rest.
- API tests use FastAPI's test client. Tests needing Postgres are marked `db` and run
  in CI against a `postgres:16` service container; they skip locally when
  `DATABASE_URL` is unset.
- The stdlib-only constraint on the two numeric packages is untouched.
  `worth-api/pyproject.toml` is the only place a third-party runtime dependency
  appears.

## 10. Deployment

- `infra/`: CDK stack with an ECR image built from `packages/worth-api/Dockerfile`, an
  App Runner service (1 vCPU, 2 GB), RDS Postgres 16 `db.t4g.micro` in a private
  subnet, a VPC connector, and a Secrets Manager entry for `DATABASE_URL`.
  As built: CDK builds and pushes the image itself as a Docker asset, so there is
  no separate push step; and the secret is the RDS-generated one, injected as
  `PGPASSWORD`, with the API assembling the URL from libpq-style variables. A
  second stack, `WorthConsole`, defines the Amplify app with the rewrite rule, for
  when the app is created from code rather than by hand.
- GitHub Actions: on `main`, `cdk deploy WorthApi` via an OIDC role.
- `amplify.yml`: unchanged build, plus `VITE_API_BASE=/api`. Amplify rewrite rule:
  `/api/<*>` to `https://<app-runner-host>/api/<*>` with status 200 (proxy). Deep links
  still need no rule because routing is hash-based.

**This is the public demonstration stack, on synthetic data.** It is not the
registry deployment. The methodology notes and the Milken note describe a federated
model where the engine runs where the data lives and only the computed index leaves;
the CIO brief describes a Seen-owned VPC under BAA with KMS, TLS 1.3, least-privilege
IAM and access logging. What carries over to that environment is the container: no
runtime egress, the CMS archives baked in at build, the dataset directory as a
parameter, read-only. What does not carry over is Amplify, the public App Runner
endpoint and the absence of auth. A BAA stack is a separate deliverable and is not in
this plan.

## 11. Order of work

Each phase is one reviewable change. Nothing in a later phase is needed to leave the
earlier one working. All five phases landed on 2026-09-03 (uncommitted); the stacks
are synthesised, not deployed.

1. **`worth-api` without a database.** Package skeleton, dataset manifest and file
   endpoints, serializer moved to pydantic models, `POST /runs`. Ported tests.
   `compose.yaml` gains the `api` service. Tree still builds `run.json`.
2. **Console over the API.** Client, fetch-on-open cards, real Run button, generated
   types, Vite proxy. Delete `run.json`, `export_run.py`, and the old drift test.
3. **Postgres and the Price tab.** `schedule=` on `expected_allowed`, loader on boot,
   `/api/fees/*`, Price tab, drift test, CI service container.
4. **The fee schedule in the denominator.** In this order, each its own review:
   a. `worth-fees`: fixture widened to the thirteen codes.
   b. Generator: dates into CY2026, amounts from real PFS times payer multiplier.
   c. `worth-complexity`: dependency on `worth-fees`; `schedules`, `locality`,
      `setting` on `run()`; primary-line realized and modifier 22 on the encounter.
   d. `adequacy.py`: schedule curve, payer multipliers, the new ratio and trace;
      `cli.py` report updated; tests.
   e. API and console: new fields through the serializer and the generated types;
      charts, case table, banner.
5. **Ship.** Dockerfile, `infra/`, Amplify rewrite and env var, README updates.

## 12. Deliberately out of scope

- Uploading a partner extract. The architecture allows it (the dataset directory is
  already a parameter) but it needs auth, storage and PHI handling that this plan does
  not touch.
- Persisting runs. Runs are deterministic on a fixed dataset and parameters, so an
  in-memory cache keyed on content hash is equivalent. A `run` table earns its place
  when uploads do.
- Streaming progress.
- The multiple-procedure reduction.
- Pinning CY2024 and CY2025 vintages, until real dates require them.
- Auth of any kind.
- The facility side. `worth-fees` prices the Medicare Physician Fee Schedule only, so
  the ratio is professional-fee adequacy. The Milken note is clear that the facility
  fee "fails separately and simultaneously" (OPPS, the inpatient-only list, ASC), and
  a real 835 feed carries institutional claims alongside professional ones. The
  parser should route institutional 835s (revenue-code lines) away from the ratio
  rather than sum them; pricing them is a second package.
- ~~Method 1 (the 837 against the code's valuation basis) and Method 2's adjustment
  factor.~~ Built 2026-09-09 — see the dated section at the end of this file. Both
  are in scope now: Method 1's pricing (the fee schedule times the payer multiplier)
  and Method 2's adjustment (the compression component of the dollar spine) are
  wired into the pipeline.

## 13. Alignment with the methodology notes, the Milken note and the CIO brief

Checked on 2026-09-03 against *WORTH Methodology Notes v4* (July 2026), *The Missing
Price Signal* (Milken, August 2026) and *WORTH Registry: Data Requirements &
Architecture v4* (Mount Sinai CIO, July 2026).

**Where the plan matches the documents**

- The ratio. v4 defines the index as "realized payment divided by complexity-matched
  expected payment, both in dollars. Realized payment is the 835 allowed amount.
  Expected payment is what the fee schedule already pays for this same measured
  complexity in uncontested specialties." Section 4 implements that sentence. The
  denominator is built from comparator codes' PFS amounts only; the study code's own
  PFS amount never enters it, which is what Milken's point that "a Medicare rate that
  encodes undervaluation is incapable of serving as the corrective benchmark" requires.
- No pairing. Method 3 in v4 is "what the fee schedule pays when this measured level
  of complexity appears elsewhere in medicine", explicitly not an asserted procedure
  pair. The schedule curve is fitted across the whole comparator cohort on the Layer
  A score, so pair selection never arises.
- The score is never a denominator. Unchanged; `ComplexityScore` still raises.
- Method 0 leads, the reference distribution stands beside it, both "available from
  the first validated extract". Unchanged in the pipeline and in the console's order.
- Suppression at n = 11 matches the CIO brief's "cell suppression below n = 11".
- The registry unit. v4: "a payment adequacy index per CPT code, per institution,
  per period", with n, the distribution and an interval. That is `IndexRecord`.
- Modifier 22. v4 folds it into Method 2 as the manual thing Method 2 systematises.
  Recording it on the encounter as a structured fact (Fork I) is the right first step.
- The container. v4's "the engine runs where the data lives" and the Milken note's
  "computation travels to the data" are why Fork A's container, with no runtime
  egress and the dataset as a parameter, is the right shape even though the demo
  stack around it is public.

**Where the plan goes beyond the documents and Joey has to defend it**

- The payer multiplier. v4 describes the Method 2 curve as observing "how realized
  payment actually scales with measured complexity" in uncontested specialties, and
  the cardinality section says expected payment is "what the fee schedule already
  pays". Fork G reconciles the two by factoring realized comparator payment into the
  fee schedule's complexity relation times a per-payer contract level. Milken's
  Appendix A supports the factorisation (commercial contracts are "predominantly
  expressed as a multiple of the corresponding Medicare rate"), but neither document
  names a multiplier. It belongs in v5, with the rule that it never leaves the
  partner environment.
- The numerator is the primary code's line, not the whole claim (Fork I). v4 says
  "the 835 allowed amount" without specifying. State the choice and why.
- The curve's vintage handling (section 4) is a choice the documents do not make.

**Where the documents say something the plan should not be mistaken for**

- Professional fee only. Everything priced here is MPFS. Milken's use case for
  complex benign GYN surgery is as much about OPPS and the inpatient-only list as
  about the physician fee. The console should say "professional-fee adequacy" where
  it currently says "adequacy".
- The demo stack is not the registry. See section 10.
- The CIO brief describes the AI story differently from v4: Comprehend Medical
  extraction and an ML scorer with an F1 gate, versus v4's Layer A with "no trained
  or generative model in the scoring path" and enrichment beside it. The console's
  "no model sits in the path" language follows v4 and the built rule pack. Worth
  reconciling before the CIO's team reads both.
- The structured stream. The CIO brief routes it through Healthix as CCDA, FHIR or
  flat files and asks whether OpTime timestamps survive that path. The dataset here
  is five Clarity-shaped tables, which the Data tab already labels as an assumption.
  Nothing in this plan changes that, and the file-hosting design does not care what
  shape the tables arrive in.

## 2026-09-09: outputs build

W3 (integration), following W1a (Method 1) and W1b (Method 3, signature,
population, witness) landing their own modules. Decisions 1, 2, 5, 6, 7 and
11 from `CONTRACT.md`, in worth's own words rather than the contract's:

- **Decision 1 — Method 1 is priced, not just flagged.** `missed` and
  `mismatched` statements get a dollar figure the same way anything else in
  this codebase does: the fee schedule's own amount for the candidate code,
  at the run's reference release, times the payer's measured multiple.
  `no_code` stays unpriced on purpose — there is no code to look up a price
  for, and inventing one would be the kind of number a referee finds first.
- **Decision 2 — the denominator moved.** The payment adequacy ratio used to
  divide by a single fitted line's value at the encounter's own score. It now
  divides by the median of what comparator encounters *scored near this one*
  actually got paid, widened until there are enough of them to publish. The
  old line is still fitted and still reported — it is what tells you whether
  a code's own curve position is doing the work (compression) or whether the
  center itself is off — but it no longer decides the number everyone reads
  first.
- **Decision 5 — every output can name the formula that produced it.**
  `rulebook_version` (`rule_pack_id@version`) and `weights_version` sit next
  to `rule_pack_digest` on every record, every card, every CLI document. The
  digest proves the bytes; the version is what a person says out loud.
- **Decision 6 — a run can be checked without re-running it.** The witness is
  a hash of a hash: one over the exact files read, one over the outputs
  themselves, folded into an envelope that also names the rulebook and the
  package. Anyone holding the same three inputs gets the same hex string.
  Anyone holding a key gets a signature too.
- **Decision 7 — Method 0's pooled slope is measured on the fee schedule's
  own scale.** Realized dollars divided by the payer's multiplier before
  fitting, so a code that happens to see more of an expensive payer's cases
  does not read as more complexity-sensitive than it is. Per-payer strata are
  still kept, unchanged, beside it.
- **Decision 11 — friction is reported, never subtracted.** A denial or a
  downcode is a fact about the payer's processing, not about whether the fee
  schedule pays the work correctly, and the two questions get separate
  numbers rather than one number that answers neither cleanly.

What actually shipped: `worth_complexity.pipeline.run` now links claims,
prices Method 1 flags, computes Method 3's band, builds the dollar spine and
its signature, folds every study encounter into a per-code population card,
and seals the run with a witness — six new stages, reordered from the
contract's literal "append at the end" phrasing into the order the data
actually depends on (`Adequacy` embeds Method 1/3/signature, so those run
before it; `IndexRecord` carries its code's card, so population runs before
records). `worth-cli` gained six subcommands — `score`, `explain`, `slope`,
`code`, `compare`, `queue` — each in `--json`, `--table` or `--csv`, plus a
bare `worth` alias.

One honest limitation, found rather than papered over: the only procedure
rule with a candidate code (`adhesiolysis` → 58660) names a CPT the committed
`worth-fees` fixture does not carry, so every Method 1 flag in the packaged
demo run comes back priced `None` with the reason on the record, rather than
a positive dollar figure. The pricing arithmetic itself is exercised directly
in `packages/worth-complexity/tests/test_pipeline.py`, against a candidate
the fixture does price. Widening the fixture would need a change to another
package's sha256-manifest-pinned data, outside this track's ownership.

- **Layer 2 of rule pack storage**, built alongside this track for Meridian to
  target concurrently: `rulepack.load` now resolves a pack from `--pack`/
  `--rulepack-dir` search directories, then `WORTH_RULEPACK_DIR`, then the
  packaged `rulepacks/`, and every `RulePack` carries `source`
  (`"packaged"`/`"external"`), `source_path` and `filename` so a run always
  says where its formula came from. Published packs stay the only source a
  `ratified` pack may come from — an external pack is capped to
  `provisional` on load, whatever it declares — and `IndexRecord` now checks
  `rule_pack_source == "packaged"` independently of `rule_pack_status` in
  `publishable`, so the packaged-only rule holds even if a record were ever
  built by hand. `rulepack.available()` lists every pack either layer can
  see (a malformed external file comes back `invalid` rather than raising),
  and `worth-cli packs` is the new subcommand onto it.

- **Groundwork for the visit and episode rule-pack track** (CONTRACT-PACKS.md,
  agent G): a clinical extract is now dispatched by which anchor file it
  holds (`extracts.read_extract`; `or_log.txt` → surgical, `visit.txt` and
  `episode.txt` placeholders raising "not built yet" until agents V and E
  land their readers), and `pipeline.run` picks the packaged pack for the
  extract's own class (`CLASS_DEFAULT_PACKS`) when `pack_name` is not given,
  refusing a mismatch by name. `Encounter` gained `encounter_class`,
  `clinician_id`, `window_start`/`window_end`, all defaulted so no existing
  call site changed. `method1.WorkRule` and `evaluate_work_rules` give the
  visit and episode classes a Method 1 built from a pack's `work_rules`
  block — threshold rules over structured facts instead of note-phrase
  statements — producing the same `Method1Flag`, now with a `replaces`
  field priced as the candidate-minus-billed difference, floored at 0.
  `CodeCard` gained `service_line`, and `compare_rows(..., by="domains")`
  groups codes by it within a class (`worth-cli compare --domains`, which
  used to group by dominant lever, now groups by service line instead — a
  deliberate redefinition of that flag for this track, not an addition
  beside it). `worth-fees FIXTURE_CODES` gained the E/M, care-management,
  digital E/M, RPM, cardiac-device, home-dialysis and CGM codes the visit
  and episode packs' comparators and work rules will need, rebuilt offline
  from the cached CMS archives for all four 2026 quarters; 99417 (prolonged
  service) turned out to carry CMS status I, "not valid for Medicare
  purposes", in every 2026 quarter, so it was dropped rather than invented,
  and G2212 (Medicare's replacement) was added and prices normally instead.

- **Multi-class runs** (CONTRACT-PACKS-MC.md, agent MC): a real partner
  delivery is one extract with OR cases, clinic visits and monitoring
  episodes side by side, sharing one 835/837 feed, so `extracts.read_extract`
  no longer refuses a directory naming more than one anchor — it reads a
  class per anchor and wraps them in `CombinedExtract`
  (`encounter_class == "mixed"`, `classes`; `notes`/`tables`/`file_hashes`
  the deduplicated union; `notes_for`/`facts`/`markers` dispatched by the
  encounter's own class). `pipeline.run` links remittance and claims once
  over every encounter regardless of class, then runs one full scoring pass
  — pack, setting, pricing, the comparator curve, multipliers, Method 0/1/3,
  signatures, cards, compare rows, the queue — per class present, in the
  fixed order surgical/visit/episode. `Run` is now the shared
  linkage/claims/payer-blinding/witness context plus `classes:
  tuple[ClassRun, ...]`, one `ClassRun` per class carrying what used to live
  directly on `Run`; `adequacies`/`records`/`cards`/`compare`/`queue`/
  `observations` are properties that flatten across every class so an
  existing single-class caller (and Meridian's serializer) see no
  difference, and `pack`/`rulebook_version`/`weights_version`/`setting` and
  the rest of the old per-run fields still read as the one class's value on
  a single-class run, raising `WorthComplexityError` naming `run.classes` on
  a genuinely mixed one. `pack_name`/`pack_path` may now be a sequence, one
  pack per class it declares (two for the same class refused; a pack for a
  class the extract lacks recorded in `Run.unused_packs` rather than an
  error, unless the extract has exactly one class, where a mismatch is still
  refused as before); a class with no pack resolved lists its encounters in
  `Run.unscored` instead of being dropped. `population.domain_rows` replaces
  a class's per-code cards with one row per `service_line`, reusing
  `code_card`'s own per-encounter computations (`worth-cli compare
  --domains`); a new `population.assert_single_class` guard refuses to pool
  cards from two classes into any of this module's cross-code functions.
  `worth-cli`'s `--pack` and `--setting` both became repeatable
  (`CLASS=VALUE` or a bare value for every class); `cmd_report` now prints
  one section per class. `tools/make_mixed_dataset.py` composes the three
  single-class generators' own output into `fixtures/mixed-synthetic/`
  (`just build-dataset mixed`) — class-prefixed 835/837 filenames and
  offset envelope control numbers, union-schema merges for tables whose
  column names differ by class (`encounter_dx.txt`) — without regenerating
  any of the three single-class fixtures it composes.

- **Domain rows fit within codes, never across them.** `compare --domains` reports a service
  line's Method 0 slope as the n-weighted mean of its member codes' own normalized slopes. A
  line fitted through several codes measures how the schedule prices those codes against each
  other, which reads as "rising" for a domain whose every code is flat; the first mixed fixture
  showed exactly that and it was wrong.
- **Fixtures are never shaped to a pattern.** The episode generator briefly forced a score
  tie at the study median so the median case would land on the doc's "no billing vehicle"
  signature. Removed: by dollars that pattern needs no-code work to be priced (decision 1
  leaves it unpriced), so the median episode reads `center-mispriced` and the test prints
  which pattern it landed on rather than asserting one. Pricing no-code work against a
  reference family is the open follow-up.

## 2026-09-10: seed suite — decisions 6 and 7 (agent S2)

Pipeline behaviour the seed scenarios (CONTRACT-SEEDS.md) need, alongside
agent S1's generators-into-the-package track and ahead of Meridian's own
seed-suite work.

- **Decision 6 — missing markers default to zero, not to a refusal.**
  `scoring.score` no longer raises `MissingMarkerError` for a marker the rule
  pack lists but the extract did not produce, or produced with an
  implausible value: it scores at zero, and `ScoredEncounter.missing` (marker
  ids) plus the new `ScoredEncounter.marker_rows` (one `MarkerRow` per rule,
  `missing: bool` and a reason) record it, alongside the derivation trace. A
  structured value outside a plausibility guard — operative minutes outside
  `[0, 1440]`, EBL over 5000, ASA outside 1..6, any `*_minutes` marker
  negative, an episode's device-reading volume over 5000 — is rejected the
  same way, `"rejected: <why>"` rather than `"absent"`. `MissingMarkerError`
  survives only for the encounter where every *structured* marker the pack
  admits is gone: `pipeline.run` catches it per encounter, adds it to
  `Run.unscored` with the reason, and keeps going rather than failing the
  whole run. `population.instrument_health` and `EncounterRow.missing_markers`
  now compute site missingness straight from those flags instead of
  inferring it from which markers happened to be present.
- **Decision 6, the remittance side — reversals and replacements are real,
  not refused.** `x12.parse_835` no longer raises on `CLP02 == 22`; every
  `RemitLine` instead carries `claim_seq`, the ordinal of the CLP occurrence
  it came from (renumbered by `x12.read_directory` to stay ordered across
  files). `linkage.link` nets a reversal against the earlier remit for the
  same account — both excluded from `realized` — and recognizes a later
  corrected claim, if any, as the one that counts; `Linkage.reasons` tallies
  why every unlinked encounter stayed that way (`"no remittance"`,
  `"reversed without correction"`, `"duplicate account"` — two encounters
  sharing a billing account link neither). `claims.py` reads CLM05-3, the
  claim frequency type code: a frequency-7 claim (a replacement) supersedes
  whatever was on file for its account in `link_claims`, regardless of
  submission order, ahead of the existing earliest-wins rule for accidental
  duplicates. `LQ*HE` RARC parsing already existed and needed only
  confirming (it does: multiple `LQ` segments on one line all carry through).
- **Decision 7 — an over-time output exists.** `population.periods(rows, *,
  policy_date, granularity="month")` returns one `PeriodSeries` per code:
  `points` (one `PeriodPoint` per calendar period — `n`, `ratio`, its
  interval, and `suppressed` when `n < 11`, independently per point), `pre`/
  `post` (`PeriodSummary`: distribution, ratio, signature mix, computed only
  when `policy_date` is given and only on sides that actually have data) and
  `by_payer` (the same points, split by payer). `pipeline.run(policy_date:
  date | None = None)` wires it in as a new `"trends"` stage, between
  `adequacy` and `population`; `ClassRun.trends`/`Run.trends` carry the
  per-class and flattened output. `worth-cli trend [CODE] [--policy-date
  YYYY-MM-DD] [--by payer]` is the new subcommand (registered next to
  `compare`), in all three formats; `worth-cli run --json` carries `trends`
  too; `cmd_report` prints a short "Over time" section per class, only when
  that class's trends actually span more than one period. Verified against
  the committed `mssm-synthetic` fixture: twelve months of 2026, twelve
  points per study code, several suppressed at the fixture's small monthly
  counts — the same shape the policy-change scenario's planted truth expects
  once agent S1's `synth` CLI can generate it directly.

No published fixture number moved: the committed `mssm-synthetic`,
`visit-synthetic`, `episode-synthetic` and `mixed-synthetic` datasets carry a
complete structured marker for every encounter and no reversed remittance
line, so decision 6's default-to-zero path and its remittance-reversal
handling are both new code paths the existing fixtures never exercise —
every existing test's assertions on those fixtures still hold unchanged, and
the new behaviour is proven by hand-built rows in `test_scoring.py`,
`test_x12.py`, `test_cases_and_linkage.py` and `test_claims.py` instead.

## 2026-09-10: seed suite — generators into the package, finished (agent S1)

Closes out agent S1's own track (CONTRACT-SEEDS.md): every class now has the
scenario-aware entry point `synthetic/surgical.py` shipped first, and
`worth-cli synth` exists.

- **`build(out_dir, *, scenario, seed=None, scale=1)`** added to `visit.py`,
  `episode.py` and `mixed.py`, alongside the `build_fixture` each already
  had. `build_records`/`build_episodes` gained an explicit `study_n`/
  `study_scale` parameter separate from `comparator_scale` (surgical's
  `build_encounters` already had this split) so the scenario path can target
  `scenarios.SCENARIO_STUDY_PER_CODE` per code without touching the
  comparator cohort; `build_fixture`'s own call sites were updated to pass
  the equivalent counts, verified byte-identical
  (`test_synthetic_baseline.py` still passes unchanged). `mixed.build` runs
  the same scenario through all three per-class `build()`s into scratch
  directories at one seed, then merges exactly as `build_fixture` merges the
  packaged fixtures — a broken scenario is therefore broken independently by
  all three classes, so the merged directory's planted failure does not
  depend on which class's anchor a reader reaches first.
- **`worth-cli synth --scenario S --class C [--seed N] [--scale K] DIR`** and
  **`worth-cli synth --list`** added (`packages/worth-cli/worth_cli/cli.py`,
  appended after `packs`, the `trend` subcommand untouched). Prints the
  output directory and the written `truth.json`.
- **Fee fixture**: `59426`/`59425` (the policy-change scenario's antepartum
  bundle) are in all four 2026 quarters' `pprrvu-*.csv`; `59400`/`59510`
  were tried and dropped (both carry an `NA` non-facility practice-expense
  indicator — unpriceable at NY01 non-facility, the visit class's setting)
  rather than kept unpriceable, with the reason recorded next to
  `FIXTURE_CODES` in `worth-fees/worth_fees/cli.py`.
- **Lint/type debt** the move left behind is gone: `ruff check`/`format` and
  `mypy` are clean across `synthetic/` and its tests. The one intentional
  ignore is `ARG001` for `dirt.py`'s corruption functions (`pyproject.toml`,
  `per-file-ignores`): every one takes a keyword-only `rng` for uniform
  dispatch from `dirt.apply`, even when it does not use one, by the module's
  own documented convention — dropping the parameter on the no-rng-needed
  functions would break that single call site's uniformity for no benefit.
  `write_remittance`/`write_claims` (`edi.py`) and `omit_secondary_codes`
  (`knobs.py`) take `Sequence[...]` instead of `list[...]` now (mypy's list
  invariance note) since none of them mutate the list itself, only read it
  or attributes of its items.
- **Tests**: `tests/test_synthetic_scenarios.py` is new — every (scenario,
  class) pair in the catalog (15 x 4 = 60) generates at scale 1 and its
  `truth.json` validates; every core scenario also reads cleanly through
  `extracts.read_extract`; every broken scenario, run through the real
  `pipeline.run`, raises exactly the exception class, message substring and
  pipeline stage its own `truth.json` planted (the stage read from `run`'s
  `progress` callback, not inferred from the exception type). One bug this
  surfaced and fixed: `planted.py`'s `broken/no-anchor` `message_contains`
  named `cases.read_table`'s message ("missing extract file") but the actual
  failure comes from `extracts.read_extract`'s own anchor-dispatch check
  ("no recognised extract anchor file") — `dirt.remove_anchor` deletes
  whichever anchor is present and the directory is read through the generic
  dispatcher before any class-specific reader opens it.
- **Known gap, not closed here**: `Scenario.policy_date` and
  `Scenario.multi_site` are threaded through every class's `build()` and
  accepted without error, but neither changes what is generated yet — no
  class spans multiple calendar periods around a policy date, or varies
  documentation completeness by facility NPI. `synthetic/README.md`
  ("Known gaps") has the detail; closing it is follow-on work, most useful
  once Meridian's Over-time tab (decision 7) has a real two-period dataset
  to render against.

## 2026-09-10: `policy-change` and `multi-site` planted for real (agent S1b)

Closes the known gap above: both scenarios now generate the narratives
CONTRACT-SEEDS.md's catalog names, for all four classes, instead of a
baseline-shaped dataset with an accepted-but-inert knob.

- **`policy-change`**: every class's service window widens to 24 months
  (2026-01 through 2027-12); comparator encounters stay inside the original
  CY2026 window (`worth_complexity.adequacy.price_comparators` prices each
  one at the real, unclamped vintage in force on its own date, which has no
  CY2027 fallback — only the study cohort needs to span the policy date).
  Visit class: a pre-date pregnancy's 6-10 prenatal visits are grouped
  (`visit.build_pregnancy`) into one antepartum-care claim (CPT `59426`/
  `59425`) submitted once, on an account every one of that pregnancy's
  visit rows shares, while each visit still extracts and scores as its own
  encounter; post-date, visits bill `99214` individually as today.
  `worth_complexity.linkage.link` gained a bundle carve-out
  (`BUNDLE_CODES`, `LinkedEncounter.bundle_size`): a claim whose lines
  carry a global/antepartum code is a legitimate multi-encounter bundle,
  not the "duplicate account" ambiguity two encounters sharing an account
  ordinarily are — every encounter it covers links, each with its own
  share of the claim's allowed amount, recorded on `bundle_size` and in
  `rule` (`"account_id/bundle:N"`). Surgical/episode: a flat 4%
  fee-schedule step (`pricing.FEE_SCHEDULE_STEP`) on every priced base
  amount once the encounter's own service date reaches the policy date.
  No CY2027 CMS archive is pinned, so every scenario-aware pricing helper
  now calls `pricing.vintage_for_date_clamped` (falls back to the 2026 Q4
  vintage past it) instead of `worth_fees.sources.vintage_for_date`
  directly — a generator-side clamp only, `vintage_for_date` itself still
  raises for a real caller, and a caller running the real pipeline over one
  of these fixtures still needs `reference=(2026, 4)` explicit
  (`pipeline.run`'s own default reference vintage is not clamped).
  `knobs.stratify_study_months` (round-robin, one study case per calendar
  month) replaces a plain uniform date draw for the study cohort in
  surgical and episode, so the Over-time output's monthly points do not
  have real gaps at this small a fixture size. `truth.json` plants
  `periods: 2`, `policy_date`, and per study line a `ratio` pre/post band
  pair (`study_pre`/`study_post` for surgical/episode; `maternity_pre`/
  `maternity_post` for visit, read back from a real `pipeline.run` over the
  just-generated directory, since a bundle visit's realized share is not a
  fixed multiple of anything `pricing.py` already has a closed-form band
  for), and `distribution_shift: false` (menopause is unaffected).
- **`multi-site`**: three facility NPIs (`sites.SITE_NPIS`) — the existing
  single-site NPI (site A, unaffected) plus two new ones. Site B withholds
  the note a rule-based marker would read 60% of the time (the surgical
  operative note, the visit note's shared decision-making branch) —
  withholding the whole note, not editing its language, is what actually
  registers as missing under decision 6, rather than a note that legitimately
  never uses the tracked phrases (still a real, present zero). Site C leaves
  a structured field blank: surgical's `or_log.txt` `ebl_ml` 95% of the time
  (and its operative note's own ESTIMATED BLOOD LOSS line, since the rule
  pack's `estimated_blood_loss_ml` marker has a narrative fallback that
  would otherwise still resolve it), and episode's `time_log.txt` entirely
  absent for 40% of episodes (`worth_complexity.episodes`'s
  `_oversight_minutes` now returns `None`, not a legitimate zero, when an
  episode has no time-log rows at all — the same treatment `markers.py`'s
  blank-`ebl_ml` and blank-timestamp handling already gave their own
  markers). `truth.json` plants `sites: 3` and `missingness_by_site`
  (`{facility NPI: {marker_id: [low, high]}}`, `planted.
  missingness_by_site`, scored fresh from the generated files the same way
  `missingness_max` already was, broken out by `Encounter.facility_npi`).
- **`markers.py`**: the two pre-existing ruff findings (an unsorted,
  over-length import line) were trivial and fixed alongside the EBL change
  they sit next to.
- **Tests**: `test_synthetic_scenarios.py` gained
  `test_policy_change_visit_trends` (100% linkage; `pipeline.run(...,
  policy_date=date(2027, 1, 1))` reads back a real pre/post split for
  `99214` and for the pre-date bundle code(s), with the bundle's pre ratio
  nowhere near post-date `99214`'s), `test_policy_change_monthly_points`
  (surgical/episode: 24 real monthly points, requesting month granularity
  directly since a ~1-case-per-code-per-month cohort does not clear
  `population.periods`'s own "auto" granularity heuristic at monthly
  resolution) and `test_multi_site_instrument_health_and_sites` (`truth.
  json`'s `missingness_by_site` reproduces byte-for-byte from
  `planted.missingness_by_site`; the real pipeline's work queue and cards
  see all three planted sites). The existing parametrized sweep
  (`test_scenario_truth_validates`, `test_core_scenario_is_readable`)
  already covers both scenarios across all four classes now that they plant
  something real. `synthetic/README.md`'s "Known gaps" section is gone,
  replaced with a real description of both scenarios.

- **Over-time granularity is automatic.** `population.periods` buckets by month, quarter or
  half-year, choosing the finest at which at least half a code's points clear the n = 11
  floor, and reports which it chose. Sixty cases a year are five a month, so a monthly series
  drew nothing; quarters do.
- **Over-time by domain.** The same series keyed by service line (`trend --by domain`), so a
  line whose billing vehicle changes at a policy date (maternity: antepartum bundle before,
  99214 after) reads as one series instead of two half-series under different codes.
- **Service dates past the newest pinned CMS release price at that release.** `adequacy.
  vintage_in_force` carries the newest vintage forward and marks the encounter
  `beyond_pinned`; a date before the oldest pinned release is still an error.
- **Antepartum bundles price at the bundle's own schedule amount** in the policy-change
  generator, once per pregnancy, allocated across the visits it covers by the linkage.

