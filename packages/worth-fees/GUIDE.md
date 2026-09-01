# worth-fees — field guide

Everything a new developer needs: where the numbers come from, what each one
means, how they flow through the code, and what every column in the database is.

[`README.md`](README.md) is the reference — the API, the constraints, the
commands. This is the explanation. If the code looks strange, the reason is
almost always that Medicare is strange, so the domain comes first.

**Contents**

1. [What it does](#1-what-it-does)
2. [The domain in ten minutes](#2-the-domain-in-ten-minutes)
3. [The formula, walked through](#3-the-formula-walked-through)
4. [Where the data comes from](#4-where-the-data-comes-from)
5. [How data flows](#5-how-data-flows)
6. [Table and column reference](#6-table-and-column-reference)
7. [The code, module by module](#7-the-code-module-by-module)
8. [Running it](#8-running-it)
9. [Decisions and open questions](#9-decisions-and-open-questions)
10. [Gotchas that will bite](#10-gotchas-that-will-bite)

---

## 1. What it does

Give it a procedure code, a place, and a point in time. It returns the exact
dollar amount Medicare's Physician Fee Schedule allows — plus the arithmetic
that produced it and the sha256 of the government file every input came from.

```python
from worth_fees import PlaceOfService, expected_allowed

d = expected_allowed("99213", [], "CA18", PlaceOfService.NON_FACILITY, 2026, 1)

d.amount  # Decimal("104.89")
d.trace  # every multiplication and running sum, in order
d.source  # filenames + sha256 + CMS release date
```

That is the entire public surface. Everything else in the package exists to make
those three attributes trustworthy.

WORTH's claim is that a payment argument must be reproducible by someone who
does not trust you. So this package refuses to be a black box: no floats, no
third-party libraries in the arithmetic, no network at runtime, and a hash chain
back to bytes you can re-download yourself.

---

## 2. The domain in ten minutes

Since 1992, Medicare has paid physicians using a **relative value** system.
Rather than pricing each of ~9,400 procedures in dollars, CMS scores each one in
abstract units, then multiplies by a single dollar figure. Change that one
figure and every price in the country moves.

### The three RVU components

Every procedure carries three **Relative Value Units** — the "how much resource
does this consume" scores.

| Component | What it captures |
| --- | --- |
| `work_rvu` | **Physician work.** Time, skill, mental effort, stress. This is the component the gender-disparity literature argues about — Penn et al. found male-coded procedures carry work RVUs 31–34% higher than matched female-coded ones. |
| `pe_rvu` | **Practice expense.** Overhead: staff, rent, equipment, supplies. Comes in two flavours — see below. |
| `mp_rvu` | **Malpractice.** Liability insurance cost attributable to the procedure. Always the smallest of the three. |

### Facility vs. non-facility

The practice-expense RVU has two values, and picking the wrong one is the
easiest way to be badly wrong.

If a physician works **in their own office** (non-facility) they bear the
overhead, so the PE RVU is high. If they work **in a hospital** (facility) the
hospital is separately paid for that overhead, so the physician's PE RVU is much
lower. For code 99213 that is 1.46 versus 0.33 — a $45 swing on a $105 service.

> **The NA trap.** When a service is not performed in one of the two settings,
> CMS marks that column `NA` — *and copies the other setting's number into it
> anyway*. Read the number without checking the flag and you get a perfectly
> plausible price for a service that has no price in that setting.
> `worth-fees` raises `NotPayableError` instead.

### GPCI — the geography adjustment

Rent in Manhattan is not rent in rural Alabama. CMS publishes a **Geographic
Practice Cost Index** for each of 109 localities — three of them, one per RVU
component. Above 1.0 raises payment, below 1.0 lowers it.

A locality is two letters of state plus a two-digit number: `CA18` is
Los Angeles–Long Beach–Anaheim. Some states are a single statewide locality
(`AL00`); California has many.

### The conversion factor

One number, set annually by CMS, that turns RVUs into dollars. For CY2026 it is
**$33.4009**. It is where budget neutrality and every act of Congress lands, and
it is why a fee-schedule cut is a single-line change with nationwide effect.

### Modifiers

Two-character suffixes on a code. Only two change which RVUs apply:

- `26` — **professional component.** The physician reading the X-ray, not the
  machine taking it.
- `TC` — **technical component.** The machine, film and technician, not the
  interpretation.

A code with no modifier is the **global service**: both halves. For code 71046
the `26` and `TC` RVUs sum exactly to the global RVUs — there is a test
asserting it.

Other modifiers either do not affect the base formula (`25`, `59`, `RT` …) or
scale payment in ways this package does not model (`50` bilateral, `51`
multiple, `80` assistant surgeon). The second group is **rejected**, never
silently ignored — returning the unadjusted amount would be a wrong answer
wearing the costume of a right one.

### Status codes

A single letter saying whether the code is payable at all. Only `A`, `R` and `T`
are priced; the rest raise `NotPayableError`.

| Code | Meaning | Rows in RVU26A | Priced? |
| --- | --- | ---: | --- |
| `A` | Active — paid separately | 9,387 | yes |
| `X` | Statutory exclusion from the PFS | 2,548 | no |
| `I` | Not valid for Medicare — use another code | 1,437 | no |
| `C` | Contractor-priced — no national RVUs | 1,334 | no |
| `M` | Measurement code, reporting only | 1,292 | no |
| `E` | Excluded by regulation | 1,289 | no |
| `R` | Restricted coverage | 1,082 | yes |
| `N` | Non-covered service | 351 | no |
| `J` | Anesthesia — a different formula entirely | 276 | no |
| `P` | Bundled or excluded | 142 | no |
| `B` | Bundled into another service's payment | 80 | no |
| `T` | Injections — paid only if nothing else that day | 8 | yes |

### Global periods

How many days of follow-up care the payment already covers. This matters far
more than it looks: two procedures with identical operating-room time but a
10-day versus 90-day global are **not** comparable on payment alone, and that
asymmetry sits at the centre of the published dispute about gynecologic code
valuation.

| Value | Meaning | Rows in RVU26A |
| --- | --- | ---: |
| `XXX` | Global concept does not apply (office visits, imaging) | 11,457 |
| `090` | Major surgery — 90 days of follow-up included | 3,779 |
| `000` | Minor procedure — no follow-up days | 1,397 |
| `ZZZ` | Add-on code — inherits the global of its parent | 1,111 |
| `YYY` | Contractor decides case by case | 996 |
| `010` | Minor surgery — 10 days of follow-up | 469 |
| `MMM` | Maternity — the global obstetric package | 17 |

Those 17 `MMM` codes (59400, 59409, 59410, 59510, 59610 …) are the global
obstetric bundle that unbundles on 1 January 2027. Because `worth-fees` pins
data by release, you can price the same documented work under 2026 rules and
2027 rules and measure the delta rather than assume it.

---

## 3. The formula, walked through

Three multiplications, one sum, one more multiplication, one rounding.

```
(work_rvu * work_gpci + pe_rvu * pe_gpci + mp_rvu * mp_gpci) * CF
```

Code 99213, locality CA18, non-facility, CY2026 Q1:

```
   RVU (national)          GPCI (locality CA18)       product
   ---------------------------------------------------------------
   work           1.30  x  work GPCI       1.041  =       1.35330
   practice exp   1.46  x  PE GPCI         1.183  =       1.72718
   malpractice    0.09  x  MP GPCI         0.664  =       0.05976
   ---------------------------------------------------------------
                                    adjusted RVU total    3.14024
                                   x conversion factor   33.4009
   ---------------------------------------------------------------
                                                   104.886842216
                                    round half-even to cents
   ===============================================================
                                                         $104.89
```

The place of service decides which PE RVU enters the middle row: non-facility
1.46, facility 0.33.

Every intermediate value keeps full 28-digit precision. **Rounding happens
exactly once, at the end** — rounding each term as you go is how two
implementations of one formula end up a penny apart.

`d.trace` hands the reader the same walk in text, so they can redo it on paper:

```
work 1.30 RVU x 1.041 work GPCI  =  1.35330    running total 1.35330
PE   1.46 RVU x 1.183 PE GPCI    =  1.72718    running total 3.08048
MP   0.09 RVU x 0.664 MP GPCI    =  0.05976    running total 3.14024

adjusted RVU total               =  3.14024
x 33.4009 conversion factor      =  104.886842216
rounded to cents                 =  $104.89
```

---

## 4. Where the data comes from

CMS publishes the fee schedule quarterly as a ZIP archive. The package pins
exactly one release and refuses to parse anything whose hash does not match.

All four CY2026 quarters are pinned and committed, so any date in the year
resolves offline.

| `<date>` | Release | Governs service dates | Published | Rows |
| --- | --- | --- | --- | ---: |
| `2026Q1` | `RVU26A` | 2026-01-01 – 2026-03-31 | 2025-12-29 | 19,226 |
| `2026Q2` | `RVU26B` | 2026-04-01 – 2026-06-30 | 2026-05-01 | 19,277 |
| `2026Q3` | `RVU26C` | 2026-07-01 – 2026-09-30 | 2026-06-30 | 19,356 |
| `2026Q4` | `RVU26D` | 2026-10-01 – 2026-12-31 | 2026-08-26 | 19,453 |

Each is pinned by the sha256 of its archive — for Q1,
`rvu26a-updated-12-29-2025.zip`, hashing to
`91b9bdd5459bc4c19d4f8203410b29a672db37def2e70f01ef627b8b18fc7482`.

**Yes, CMS revises these files.** Every CY2026 release carries an `-updated-`
suffix, and the index goes back to 2012 with entries like `RVU24AR` ("A
Revised"). That is why the hash is pinned: a silently re-cut upstream file
becomes a loud failure rather than a changed number.

**How much does the date change the answer?** Within CY2026, less than you would
expect: the four quarters add 227 codes and reclassify 64, but no *payable*
price changes. Across years it changes a lot — the CY2025 conversion factor was
`32.3465` against CY2026's `33.4009`, and 99213's non-facility PE RVU moved from
1.35 to 1.46. So the quarter matters for *which codes exist*; the year matters
for *what they cost*.

Earlier years are not just a URL away. The CY2025 file has 31 columns to
CY2026's 32, a different member name, and no QPP/non-QPP split — the dual
conversion factor only starts in 2026. The parser refuses a layout it does not
recognise rather than reading the wrong columns, so supporting historic years
means teaching it those layouts.

The archive holds 19 files. Only two matter:

- `PPRRVU2026_Jan_nonQPP.csv` — 19,226 rows, one per code-and-modifier, 32
  columns. The RVUs.
- `GPCI2026.csv` — 109 rows, one per locality. The geographic indices.

The rest (OPPS caps, anesthesia factors, the locality-to-county crosswalk) is
out of scope for now.

```
   cms.gov
      |
      | fetch
      v
  +--------------------------------+
  |  rvu26a-updated-12-29-2025.zip |   in the cache, gitignored
  |  sha256 91b9bd...              |   <-- verified BEFORE anything is parsed
  +---------------+----------------+
                  | extract
          +-------+--------+
          v                v
  PPRRVU...nonQPP.csv   GPCI2026.csv
  19,226 rows           109 localities
  32 columns
          |                |
          +-------+--------+
                  | keep a few rows, blank the descriptor column
                  v
        packages/worth-fees/worth_fees/fixtures/*.csv
        2.5 KB, committed to the repo
```

Every file records its own sha256 and the parent it was cut from, so the chain
reads *fixture → CMS member file → CMS release archive*. The ZIP is hashed
before anything is parsed, so a truncated download or a silently re-cut upstream
file fails loudly rather than becoming a subtly wrong dollar amount.

> **CPT descriptors never enter this repository.** The RVU file carries a
> `DESCRIPTION` column — "Office o/p est low 20 min" — which is AMA-copyrighted.
> The parser never reads it and the fixture builder blanks it. Two tests enforce
> this: one asserts the column is empty in every committed row, another asserts
> no lowercase character appears in any data row at all. The repo is public;
> numeric codes only.

---

## 5. How data flows

The single most useful thing to internalise: **the demo, the tests and CI never
touch the network or a database.** They read a 2.5 KB fixture committed next to
the code. The full CMS download and Postgres are both opt-in.

```
  SOURCE                  LOADER                    PARSED           CONSUMER

  fixtures/*.csv  --->  load()  ------------+
  in the repo           hash-checks the     |
  offline               manifest            |                   +--> expected_allowed()
                                            +--> FeeSchedule ---+       -> FeeDerivation
                                            |    rvus           |
  cached .zip  ------>  load_from_archive() |    gpcis          +--> sql.export()
  downloaded            all 19,226 rows ----+    sources                -> psql -> Postgres
  opt-in

  ^ top lane: demo, tests, CI              ^ one parser, both paths
  v bottom lane: database, bulk analysis
```

The fixture is a row subset of the real file **in the real CMS layout**, so it
cannot drift from the format it claims to sample — and a bug in parsing shows up
in CI, not only against the full download.

### What happens on a call

1. **Resolve the vintage.** `(2026, 1)` → `RVU26A`, or `VintageError`.
2. **Load and verify.** Fixtures are hashed against `manifest.json`; a
   hand-edited fixture fails here.
3. **Pick the modifier.** `26`/`TC` select a different row; payment-scaling
   modifiers are rejected.
4. **Look up the RVU row** by `(code, modifier)`, then check the status code.
5. **Look up the GPCI row** by locality.
6. **Choose the PE RVU** by place of service, honouring the `NA` flag.
7. **Compute** inside the pinned decimal context, recording each step.
8. **Round once** and return the `FeeDerivation`.

---

## 6. Table and column reference

The schema lives in [`migrations/0001_fee_schedule.sql`](migrations/0001_fee_schedule.sql).
Everything in it is public CMS data — no PHI, no institution or payer rates. It
can be dropped and rebuilt from the published archives at any time.

Two rules it enforces rather than documents: money is always `numeric` (never a
float), and a release is immutable — CMS reissues files, and each reissue is a
new release, never an edit.

### `fee_schedule_release`

One row per CMS publication. This is both the **time dimension** and the
**provenance anchor** — the insight that makes the schema work. Time is not a
lookup table you join to; it versions everything, and the thing being versioned
is a published file with a hash.

| Column | Type | Meaning |
| --- | --- | --- |
| `release_id` | text PK | CMS's label, e.g. `RVU26A`. A = January, B = April, C = July, D = October. |
| `rule_year` | smallint | Calendar year the fee schedule belongs to. |
| `quarter` | smallint | 1–4. |
| `payment_basis` | text | `non-qualifying-apm` or `qualifying-apm`. CY2026 has two conversion factors; which one this release carries is part of its identity. |
| `work_gpci_basis` | text | `floor` or `no-floor` — which work-GPCI column was used. Versioned with the data rather than buried in code. |
| `conversion_factor` | numeric(12,4) | Dollars per RVU. `33.4009` for CY2026 non-qualifying. |
| `released_on` | date | The date CMS stamped the file. |
| `effective` | daterange | Service dates these rates govern. Use `effective @> claim_date` to resolve a claim to a release. |
| `source_url` | text | Where it was downloaded from. |
| `archive_sha256` | char(64) | Hash of the ZIP. Re-download and re-hash to audit. |
| `retrieved_at` | timestamptz | **The second clock.** When *we* fetched it, as distinct from when it applied. You need both to explain a number you published last year. |
| `superseded_by` | text FK | Set when CMS reissues. Null means still in force. |

An exclusion constraint prevents two *in-force* releases of the same payment
basis from covering the same service date. That is what makes "which rate
applied on 2026-03-14?" a question with exactly one answer. It permits the
qualifying-APM basis over the same dates, which is exactly the CY2026
dual-conversion-factor case.

### `rvu`

The per-code numbers. Grain: one row per `(release, code, modifier)` — 19,226
rows per release. Note what is *absent*: no descriptor column.

| Column | CMS col. | Meaning |
| --- | ---: | --- |
| `release_id` | — | Which publication this row came from. |
| `hcpcs` | 0 | The five-character procedure code. |
| `modifier` | 1 | `''`, `26`, `TC` or `53`. Part of the key: `71046` and `71046-26` are different rows with different RVUs. |
| `code_system` | derived | `cpt-i` / `cpt-ii` / `cpt-iii` (AMA-copyrighted) or `hcpcs-ii` (CMS, public domain). Publication decisions differ, so the distinction is a column. |
| `status_code` | 3 | Payability. See the status table above. |
| `global_days` | 14 | Follow-up days included in the payment. |
| `work_rvu` | 5 | Physician work. |
| `pe_rvu_nonfacility` | 6 | Practice expense in the physician's own office. |
| `pe_rvu_nonfacility_na` | 7 | **True means not payable in that setting** — and the number beside it is a copy of the other setting's value. |
| `pe_rvu_facility` | 8 | Practice expense in a hospital. |
| `pe_rvu_facility_na` | 9 | The same trap, other setting. |
| `mp_rvu` | 10 | Malpractice. |
| `source_role` | — | Which `source_file` row this came from. |

The CMS file has 32 columns; the package reads 11. The unread ones are real
data, just out of scope for now — bilateral and multiple-procedure indicators,
pre/intra/post-op work splits, the PC/TC indicator, endoscopic base codes, and
OPPS payment caps. If you later model payment-adjusting modifiers, columns 18–22
are where you will go.

### `gpci`

Grain: one row per `(release, locality)` — 109 rows.

| Column | Meaning |
| --- | --- |
| `locality` | State plus two-digit locality number, e.g. `CA18`. The join key. |
| `state`, `locality_number` | The two halves, kept separately for querying. |
| `locality_name` | Human name, e.g. `LOS ANGELES-LONG BEACH-ANAHEIM`. |
| `mac` | Medicare Administrative Contractor — the private company that processes claims for the region. |
| `work_gpci_no_floor` | Work index as calculated. |
| `work_gpci_floor` | Work index with the statutory 1.0 floor applied. Differs from the above for 47 of 109 localities. |
| `pe_gpci` | Practice-expense index. The widest-varying of the three. |
| `mp_gpci` | Malpractice index. Varies enormously — liability costs are intensely local. |

### `source_file`

The provenance chain, one row per link, self-referencing via `derived_from`.
Three rows per release: the committed fixture points at the CMS member file,
which points at the archive. Each carries its own `sha256`.

### `allowed_amount` — a view, not a table

This is the "master table" you would expect, and it deliberately is not one. The
inputs are ~19,226 + 109 rows; their cross product is ~4.2 million per release
and contains nothing the inputs do not. So it is computed, not stored.

It filters out non-payable status codes, `NA` settings, and modifier `53`, so
**a plain `SELECT` cannot return a number for a service that has no allowed
amount.** It exposes every input alongside `adjusted_rvu_total` and the final
`amount`, so any row can be re-checked by hand.

```sql
SELECT hcpcs, setting, work_rvu, pe_rvu, amount
FROM allowed_amount
WHERE locality = 'CA18' AND hcpcs IN ('99213','29881')
ORDER BY hcpcs, setting;
```

---

## 7. The code, module by module

Seven files. Read them in this order.

| Module | What lives there |
| --- | --- |
| `money.py` | The pinned decimal context and the rounding rule. Start here — it is short and it is the constitution. |
| `models.py` | `PlaceOfService`, `CodeSystem`, `FeeDerivation`, and the error taxonomy. |
| `provenance.py` | sha256 helpers and the `SourceFile` chain. |
| `sources.py` | The biggest file: pinned vintages, the CMS parsers, the download cache, fixture load and build. |
| `fees.py` | `expected_allowed()`. The formula, the refusals, the trace. |
| `sql.py` | Emits a Postgres load script. Writes SQL text; never connects. |
| `cli.py` | The `worth-fees` command. |

Tests mirror this: `test_money.py` guards the decimal discipline,
`test_sources.py` covers fixture integrity and copyright hygiene,
`test_fees.py` checks the formula against an independent recomputation,
`test_sql.py` asserts the SQL and Python cannot drift apart, and
`test_published_rates.py` holds the CMS-verification story.

---

## 8. Running it

Everything works offline except the two commands that say otherwise.

### First: getting the command to run

`worth-fees` is installed into the workspace virtualenv, not onto your PATH. A
bare `worth-fees` will give you `command not found`. Pick one:

```bash
uv run worth-fees price 99213 CA18 2026-03-14    # no setup, works anywhere in the repo
source .venv/bin/activate                         # then plain `worth-fees ...`
uv tool install --editable ./packages/worth-fees  # into ~/.local/bin (add it to PATH)
```

### The main call: `<code> <place> <date>`

```bash
uv run worth-fees price 99213 CA18 2026-03-14
```

| Argument | What it accepts | Where to find the valid values |
| --- | --- | --- |
| `<code>` | A HCPCS/CPT code, five characters. Only status `A`, `R` and `T` are payable — anything else raises rather than returning a number. | 7 codes in the fixture, 19,226 in a full release. Load the database to browse. |
| `<place>` | A Medicare locality, `<state><2 digits>`. `CA18`, `ca-18` and `ca 18` all mean `CA18`. | `worth-fees localities --full` — all 109. |
| `<date>` | A service date `2026-03-14`, a quarter `2026Q2`, a year `2026` (= Q1), or `today`. | `worth-fees vintages` — each release and the dates it governs. |

Prefer the service date. The rate that applies to a claim is the one in force
on the day the service happened, not the newest one published.

| Option | Effect |
| --- | --- |
| `--facility` | Price the hospital setting. Default is non-facility (office). |
| `--modifier X` | Repeatable. `26` professional component, `TC` technical. Payment-scaling modifiers are refused. |
| `--amount` | Just the number, for scripting. |
| `--json` | The whole derivation — trace and provenance chain — as JSON. |

```bash
uv run worth-fees price 99213 CA18 today --facility --amount   # 60.24
uv run worth-fees price 71046 CA18 2026Q3 --modifier 26 --json

# what are the valid arguments?
uv run worth-fees localities --full   # the 109 <place> values
uv run worth-fees vintages            # the valid <date> ranges

# a worked example, no arguments needed
uv run worth-fees demo

# development
just test     # pytest
just lint     # ruff check, ruff format --check, mypy --strict

# the database
just db-up            # Postgres in Docker; schema applied on first boot
just db-load --full   # all ~19k rows (needs the CMS cache)
just db-shell         # psql, inside the container
just db-reset         # destroy the volume, start clean

# needs network
just refresh-fixture  # re-download CMS, verify hash, rebuild fixtures
just verify-rates     # print amounts to check against CMS by hand
```

Connect a GUI to `127.0.0.1:55432`, user `worth`, password `worth`, database
`worth`. Port 55432 deliberately, so it can never collide with a Postgres
installed later.

---

## 9. Decisions and open questions

Three things a reviewer will ask about. All are recorded in the derivation trace
rather than buried in code.

### CY2026 has two conversion factors

The non-qualifying-APM file carries **33.4009** across 19,226 codes; the
qualifying-APM file carries **33.5675** across 11,811. The package models the
non-qualifying one. Modelling both changes the API signature, so it was left as
a deliberate pin.

### The work GPCI floor

CY2026 Q1 published the work index twice — with and without a 1.0 floor —
because whether the floor is in force is a statutory question, not a CMS one.
They differ for **47 of 109 localities**, so the choice is not cosmetic: Alabama
swings 0.988 → 1.000.

**CMS has since answered this.** From Q2 2026 onward the file carries only
`2026 PW GPCI (with 1.0 Floor)***` — the unfloored column is gone. The floor is
in force, and the default in `APPLY_WORK_GPCI_FLOOR` was right. The parser
handles both layouts: when the unfloored column is absent, the floored value
stands for both.

### Nothing has been verified against CMS yet

`VERIFIED_CMS_RATES` in `tests/test_published_rates.py` is deliberately **empty**
and its test skips. A second table, `COMPUTED_UNVERIFIED`, holds what the
implementation currently produces — it is a drift alarm, not evidence.

Until that changes, the honest claim is "our arithmetic is reproducible", not
"our arithmetic is right".

**Validating it is about half an hour of manual work.** Run:

```bash
uv run worth-fees verify-rates
```

That prints a worksheet — nine cases with the MAC, locality, setting and our
amount, plus a blank column for CMS's figure. Then open the
[CMS Physician Fee Schedule Look-Up Tool](https://www.cms.gov/medicare/physician-fee-schedule/search)
and for each row:

1. Accept the AMA CPT licence click-through the tool opens with.
2. Set **Year** to 2026, **Type of information** to *Pricing Information*.
3. Search a **single HCPCS code**, with the modifier from the `mod` column
   (blank means all modifiers).
4. Choose the **MAC and locality** — `01182` / `18` for the committed fixture.
5. Compare CMS's *Non-Facility Price* and *Facility Price* against ours.

Matching rows move into `VERIFIED_CMS_RATES`, which switches the skipped test
on. Record who checked and when in the commit message.

A row that does **not** match is the most valuable bug this repository can
surface. Likely culprits, in order: the work-GPCI floor column, the
facility/non-facility setting, and the choice of conversion factor.

---

## 10. Gotchas that will bite

Every one of these has already produced a wrong answer at least once.

**The NA indicator is not optional.** CMS copies the other setting's value into
an `NA` column. Reading the number without the flag gives you a real-looking
price for an unpayable service. This is the single easiest way to publish a
wrong number.

**Postgres and Python round differently.** Postgres `round(numeric, 2)` rounds
half *away from zero*; the package rounds half to *even*. `round(0.125, 2)` is
`0.13` in Postgres and `0.12` in Python. The schema ships `round_half_even()`
and the view uses it. Never use bare `round()` on money here.

**Never let a float near the money.** Not in Python, not in a `double precision`
column, not in a dataframe you add later. A test walks every field of a result
at runtime, and another asserts the migration contains no float types.

**The fixture is a sample, not the fee schedule.** Seven codes, one locality.
`UnknownCodeError` usually means "not in the fixture", not "not a real code".
Widen `FIXTURE_CODES` in `cli.py` and run `just refresh-fixture`.

**The allowed amount is not what anyone was paid.** It is the schedule figure.
Medicare pays 80% of it, the patient owes the rest, sequestration trims the
government's share, and quality-program adjustments move it again. None of that
is modelled. The 835 remittance carries what a payer actually allowed — a
different number from a different source.

**A release is immutable.** Never `UPDATE` an `rvu` row. CMS reissues files —
`RVU24AR`, `RVU25D-0`, and our own `rvu26a-updated-12-29-2025` are all
corrections. Each is a *new* release. Mutate rows in place and the recorded
hash stops meaning anything.
