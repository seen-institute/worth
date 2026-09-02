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
- Method 1 (the 837 against the code's valuation basis) and Method 2's adjustment
  factor. The file-hosting design should treat an 837 bundle as a third `kind` when
  it arrives; nothing else in this plan anticipates them.

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
