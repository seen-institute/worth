# worth-fees field guide

Everything a developer needs: where the numbers come from, what each one means,
how they flow through the code, what every column in the database is, and every
flag on the command line.

[`README.md`](README.md) covers what the package is for, how to install it, and
the standing commitments of the project. This file is the technical reference.
The code follows the shape of the Medicare data, so the domain comes first.

**Contents**

1. [What it does](#1-what-it-does)
2. [The domain in ten minutes](#2-the-domain-in-ten-minutes)
3. [The formula, walked through](#3-the-formula-walked-through)
4. [Numeric discipline](#4-numeric-discipline)
5. [Refusals: errors and modifiers](#5-refusals-errors-and-modifiers)
6. [Where the data comes from](#6-where-the-data-comes-from)
7. [How data flows](#7-how-data-flows)
8. [The code, module by module](#8-the-code-module-by-module)
9. [Command line reference](#9-command-line-reference)
10. [The database](#10-the-database)
11. [Decisions and open questions](#11-decisions-and-open-questions)
12. [Gotchas that will bite](#12-gotchas-that-will-bite)

---

## 1. What it does

Give it a procedure code, a place, and a point in time. It returns the dollar
amount Medicare's Physician Fee Schedule allows, plus the arithmetic that
produced it and the sha256 of the government file every input came from.

```python
from worth_fees import PlaceOfService, expected_allowed

d = expected_allowed("99213", [], "CA18", PlaceOfService.NON_FACILITY, 2026, 1)

d.amount  # Decimal("104.89")
d.trace  # every multiplication and running sum, in order
d.source  # filenames + sha256 + CMS release date
```

Those three attributes are the whole public surface. Everything else in the
package exists to make them trustworthy.

WORTH's claim is that a payment argument must be reproducible by someone who
does not trust you. So the package avoids anything that cannot be checked. No
floats, no third-party libraries in the arithmetic, no network at runtime, and a
hash chain back to bytes you can re-download yourself.

### `expected_allowed()`

| Parameter | Type | Notes |
| --- | --- | --- |
| `code` | `str` | HCPCS/CPT code. Case and surrounding whitespace are normalised. |
| `modifiers` | `Sequence[str]` | As billed. See [Refusals](#5-refusals-errors-and-modifiers). |
| `locality` | `str` | Medicare locality as `<state><2-digit number>`. `CA18`, `ca-18` and `ca 18` all resolve to `CA18`. |
| `place_of_service` | `PlaceOfService \| str` | `"non-facility"` or `"facility"`. |
| `rule_year` | `int` | Calendar year of the fee schedule. |
| `quarter` | `int` | Quarterly release, 1 to 4. |

### `FeeDerivation`

Frozen. Every field is either a `Decimal` or a normalised input.

| Field | Type | What it is |
| --- | --- | --- |
| `amount` | `Decimal` | The allowed amount, rounded to cents. |
| `trace` | `tuple[str, ...]` | Ordered arithmetic steps: each product and the running sum. |
| `source` | `Sources` | Every file read, with sha256 and CMS release date. |
| `work_rvu`, `pe_rvu`, `mp_rvu` | `Decimal` | RVU components, as selected for this modifier and setting. |
| `work_gpci`, `pe_gpci`, `mp_gpci` | `Decimal` | Geographic indices for the locality. |
| `conversion_factor` | `Decimal` | Read from the file, not hardcoded. |
| `adjusted_rvu_total` | `Decimal` | The parenthesised sum, unrounded, full precision. |
| `code`, `modifier`, `modifiers`, `locality`, `locality_name`, `place_of_service`, `rule_year`, `quarter` | | Normalised inputs, as resolved. |

`derivation.render()` formats all of it as text. That is what `just demo` prints.

### Scope

Medicare Physician Fee Schedule only.

Out of scope: OPPS, ASC, IPPS, anesthesia, clinical lab, DMEPOS, Part B drugs,
and commercial rates. Also out of scope within the PFS itself: payment
adjustments driven by modifiers, sequestration, the 80/20 beneficiary split, and
quality-program adjustments. `amount` is the fee schedule allowed amount. It is
not what a payer remitted, and not what landed in a bank account.

---

## 2. The domain in ten minutes

Since 1992, Medicare has paid physicians using a relative value system. Rather
than pricing each of ~9,400 procedures in dollars, CMS scores each one in
abstract units, then multiplies by a single dollar figure. Change that one
figure and every price in the country moves.

### The three RVU components

Every procedure carries three Relative Value Units, the scores for how much
resource it consumes.

| Component | What it captures |
| --- | --- |
| `work_rvu` | Physician work. Time, skill, mental effort, stress. This is the component the gender-disparity literature argues about. Penn et al. found male-coded procedures carry work RVUs 31 to 34% higher than matched female-coded ones. |
| `pe_rvu` | Practice expense. Overhead: staff, rent, equipment, supplies. Comes in two flavours, see below. |
| `mp_rvu` | Malpractice. Liability insurance cost attributable to the procedure. Always the smallest of the three. |

### Facility vs. non-facility

The practice-expense RVU has two values, and picking the wrong one is the
easiest way to be badly wrong.

If a physician works in their own office (non-facility) they bear the overhead,
so the PE RVU is high. If they work in a hospital (facility) the hospital is
separately paid for that overhead, so the physician's PE RVU is much lower. For
code 99213 that is 1.46 versus 0.33, a $45 swing on a $105 service.

> **The NA indicator.** When a service is not performed in one of the two
> settings, CMS marks that column `NA`, and copies the other setting's number
> into it anyway. Read the number without checking the flag and you get a
> plausible price for a service that has no price in that setting. `worth-fees`
> raises `NotPayableError` instead.

### GPCI, the geography adjustment

Rent in Manhattan is not rent in rural Alabama. CMS publishes a Geographic
Practice Cost Index for each of 109 localities, three of them, one per RVU
component. Above 1.0 raises payment, below 1.0 lowers it.

A locality is two letters of state plus a two-digit number. `CA18` is
Los Angeles-Long Beach-Anaheim. Some states are a single statewide locality
(`AL00`), California has many.

### The conversion factor

One number, set annually by CMS, that turns RVUs into dollars. For CY2026 it is
$33.4009. It is where budget neutrality and every act of Congress lands, and it
is why a fee-schedule cut is a single-line change with nationwide effect.

### Modifiers

Two-character suffixes on a code. Only two change which RVUs apply:

- `26`, the professional component. The physician reading the X-ray, not the
  machine taking it.
- `TC`, the technical component. The machine, film and technician, not the
  interpretation.

A code with no modifier is the global service, both halves. For code 71046 the
`26` and `TC` RVUs sum exactly to the global RVUs, and there is a test asserting
it.

Other modifiers either do not affect the base formula (`25`, `59`, `RT` and so
on) or scale payment in ways this package does not model (`50` bilateral, `51`
multiple, `80` assistant surgeon). The second group is rejected rather than
silently ignored, because returning the unadjusted amount would be a wrong
answer. The full lists are in [Refusals](#5-refusals-errors-and-modifiers).

### Status codes

A single letter saying whether the code is payable at all. Only `A`, `R` and `T`
are priced. The rest raise `NotPayableError`.

| Code | Meaning | Rows in RVU26A | Priced? |
| --- | --- | ---: | --- |
| `A` | Active, paid separately | 9,387 | yes |
| `X` | Statutory exclusion from the PFS | 2,548 | no |
| `I` | Not valid for Medicare, use another code | 1,437 | no |
| `C` | Contractor-priced, no national RVUs | 1,334 | no |
| `M` | Measurement code, reporting only | 1,292 | no |
| `E` | Excluded by regulation | 1,289 | no |
| `R` | Restricted coverage | 1,082 | yes |
| `N` | Non-covered service | 351 | no |
| `J` | Anesthesia, a different formula entirely | 276 | no |
| `P` | Bundled or excluded | 142 | no |
| `B` | Bundled into another service's payment | 80 | no |
| `T` | Injections, paid only if nothing else that day | 8 | yes |

### Global periods

How many days of follow-up care the payment already covers. This matters more
than it looks. Two procedures with identical operating-room time but a 10-day
versus 90-day global are not comparable on payment alone, and that asymmetry
sits at the centre of the published dispute about gynecologic code valuation.

| Value | Meaning | Rows in RVU26A |
| --- | --- | ---: |
| `XXX` | Global concept does not apply (office visits, imaging) | 11,457 |
| `090` | Major surgery, 90 days of follow-up included | 3,779 |
| `000` | Minor procedure, no follow-up days | 1,397 |
| `ZZZ` | Add-on code, inherits the global of its parent | 1,111 |
| `YYY` | Contractor decides case by case | 996 |
| `010` | Minor surgery, 10 days of follow-up | 469 |
| `MMM` | Maternity, the global obstetric package | 17 |

Those 17 `MMM` codes (59400, 59409, 59410, 59510, 59610 and so on) are the
global obstetric bundle that unbundles on 1 January 2027. Because `worth-fees`
pins data by release, you can price the same documented work under 2026 rules
and 2027 rules and measure the delta rather than assume it.

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
1.46, facility 0.33. The conversion factor is read from the RVU file itself. The
parser rejects a file carrying more than one distinct value.

Every intermediate value keeps full 28-digit precision. Rounding happens exactly
once, at the end. Rounding each term as you go is how two implementations of one
formula end up a penny apart.

`d.trace` gives the reader the same walk in text, so they can redo it on paper.
`d.render()` wraps it with the resolved inputs, which is what `just demo`
prints:

```
99213   locality CA18 (LOS ANGELES-LONG BEACH-ANAHEIM)
non-facility   CY2026 Q1

  work 1.30 RVU x 1.041 work GPCI (with 1.0 floor)  =  1.35330    running total 1.35330
  PE   1.46 RVU x 1.183 PE GPCI                     =  1.72718    running total 3.08048
  MP   0.09 RVU x 0.664 MP GPCI                     =  0.05976    running total 3.14024

  adjusted RVU total                                =  3.14024
  x 33.4009 conversion factor                       =  104.886842216
  rounded to cents (ROUND_HALF_EVEN, prec=28)       =  $104.89
```

---

## 4. Numeric discipline

These are enforced by tests.

**Money is `Decimal`, never `float`, anywhere.** A float cannot represent
`0.01`. There is no float in the package outside docstrings, and a test walks
every field of a result to confirm none arrives at runtime.

**Arithmetic runs in an explicitly pinned decimal context.**
`Context(prec=28, rounding=ROUND_HALF_EVEN, traps=[InvalidOperation,
DivisionByZero])`, entered via `localcontext()`. Python's ambient decimal
context is mutable global state, so any caller or imported library can change
its precision or rounding and silently change the answers. Tests set a hostile
ambient context (`prec=4`, `ROUND_UP`) and assert the result is unchanged. The
traps matter as much as the precision. A malformed input raises rather than
yielding `NaN`.

**Rounding happens exactly once, on the final amount.** Intermediate products
keep full 28-digit precision.

**Standard library only.** No runtime dependencies at all, not numpy, not
pandas, not `requests`. Results are bit-identical on ARM and x86, and the
dependency audit is the repo.

**Every derived value carries provenance** back to a source file and its sha256.
See [Provenance](#provenance).

**No CPT descriptors.** The parser does not read the descriptor column and
`build-fixture` blanks it before writing. Two tests enforce this. One asserts
the descriptor column is empty in every committed fixture row, the other asserts
no lowercase character appears in any data row at all.

---

## 5. Refusals: errors and modifiers

A wrong number that looks confident is worse than an error. All errors subclass
`WorthFeesError`.

| Error | Raised when |
| --- | --- |
| `UnknownCodeError` | The code, with that modifier, is not in the file. Names which modifier variants do exist. |
| `UnknownLocalityError` | The locality is not in the GPCI file. Lists what is available. |
| `NotPayableError` | Non-payable CMS status code (`B`, `C`, `E`, `I`, `J`, `M`, `N`, `P`, `X`), or an `NA` practice-expense indicator for the requested setting. |
| `UnsupportedModifierError` | A modifier that scales payment, or one not recognised at all. |
| `VintageError` | No pinned vintage for that year and quarter. |
| `SourceIntegrityError` | A file's sha256 does not match its pinned or manifest value, or the layout is not what was expected. |

The `NA` check matters more than it looks. CMS repeats the other setting's value
in an `NA` column, so reading the number without checking the indicator gives a
plausible amount for a service that is not payable in that setting at all.

### Modifier handling

- `26` and `TC` select a different RVU row, the professional and technical
  components. Mutually exclusive.
- Inert modifiers are accepted and recorded but do not change the formula:
  `24`, `25`, `57`, `59`, `76`, `77`, `95`, `GA`, `GT`, `GY`, `GZ`, `LT`, `RT`,
  `XE`, `XP`, `XS`, `XU`.
- Payment-adjusting modifiers are rejected rather than ignored: `50`, `51`,
  `52`, `53`, `54`, `55`, `56`, `62`, `66`, `78`, `80`, `81`, `82`, `AS`.
- Anything else is rejected. The inert list is an allowlist, so an unrecognised
  modifier fails rather than being silently dropped.

---

## 6. Where the data comes from

CMS publishes the fee schedule quarterly as a ZIP archive. The package pins
exactly one release per quarter and refuses to parse anything whose hash does
not match.

All four CY2026 quarters are pinned and committed, so any date in the year
resolves offline.

| `<date>` | Release | Governs service dates | Published | Rows |
| --- | --- | --- | --- | ---: |
| `2026Q1` | `RVU26A` | 2026-01-01 to 2026-03-31 | 2025-12-29 | 19,226 |
| `2026Q2` | `RVU26B` | 2026-04-01 to 2026-06-30 | 2026-05-01 | 19,277 |
| `2026Q3` | `RVU26C` | 2026-07-01 to 2026-09-30 | 2026-06-30 | 19,356 |
| `2026Q4` | `RVU26D` | 2026-10-01 to 2026-12-31 | 2026-08-26 | 19,453 |

Vintages live in `PINNED_VINTAGES` in `sources.py`, keyed by
`(rule_year, quarter)`. An unpinned year or quarter raises `VintageError` rather
than falling back to the nearest available.

The Q1 pin in full:

| key | value |
| --- | --- |
| Release | `RVU26A`, published 2025-12-29 |
| Archive | `rvu26a-updated-12-29-2025.zip` |
| sha256 | `91b9bdd5459bc4c19d4f8203410b29a672db37def2e70f01ef627b8b18fc7482` |
| RVU member | `PPRRVU2026_Jan_nonQPP.csv` |
| GPCI member | `GPCI2026.csv` |
| Conversion factor | `33.4009` |

CMS revises these files. Every CY2026 release carries an `-updated-` suffix, and
the index goes back to 2012 with entries like `RVU24AR` (A Revised). That is why
the hash is pinned. A silently re-cut upstream file fails the hash check instead
of changing a number.

How much the date changes the answer: within CY2026, less than you would expect.
The four quarters add 227 codes and reclassify 64, but no payable price changes.
Across years it changes a lot. The CY2025 conversion factor was `32.3465`
against CY2026's `33.4009`, and 99213's non-facility PE RVU moved from 1.35 to
1.46. The quarter matters for which codes exist, the year matters for what they
cost.

Earlier years need parser work. The CY2025 file has 31 columns to
CY2026's 32, a different member name, and no QPP/non-QPP split, since the dual
conversion factor only starts in 2026. The parser refuses a layout it does not
recognise rather than reading the wrong columns, so supporting historic years
means teaching it those layouts.

The archive holds 19 files. Only two matter:

- `PPRRVU2026_Jan_nonQPP.csv`, 19,226 rows, one per code-and-modifier, 32
  columns. The RVUs.
- `GPCI2026.csv`, 109 rows, one per locality. The geographic indices.

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

> **CPT descriptors never enter this repository.** The RVU file carries a
> `DESCRIPTION` column, for example "Office o/p est low 20 min", which is
> AMA-copyrighted. The parser never reads it and the fixture builder blanks it.
> The repo is public, so numeric codes only.

### Two paths, one parser

**Online.** `fetch_archive()` downloads the archive into a gitignored cache and
hashes it before anything is parsed. A hash mismatch raises
`SourceIntegrityError`. Cache location is `$WORTH_CACHE_DIR`, else
`$XDG_CACHE_HOME/worth/cms`, else `~/.cache/worth/cms`.

**Offline.** `load()` reads small stripped fixtures committed alongside the
package, verifying each against `fixtures/manifest.json` before parsing. This is
what `just demo`, the test suite, and CI use. None of them touch the network.

The fixtures are row subsets of the real CMS files in the real CMS layout, so
both paths run through the same parser and the fixture cannot silently drift
from the format it claims to sample. A bug in parsing shows up in CI, not only
against the full download.

Current fixture contents: 9 rows across 7 codes, and one locality.

| Code | Modifiers | Global | Exercises |
| --- | --- | --- | --- |
| `99213`, `99214` | none | XXX | Facility / non-facility PE split |
| `99232` | none | XXX | `NA` non-facility indicator |
| `20610` | none | 000 | Payable in both settings |
| `29881` | none | 090 | `NA` non-facility, 90-day global |
| `71046` | none, `26`, `TC` | XXX | Professional / technical component split |
| `93000` | none | XXX | `NA` facility indicator |

Locality: `CA18`, Los Angeles-Long Beach-Anaheim, MAC 01182.

Rebuild with `just refresh-fixture`, which requires network. Widen the sample by
editing `FIXTURE_CODES` / `FIXTURE_LOCALITIES` in `cli.py` and rebuilding. The
manifest and its hashes regenerate together.

### Parsing

The RVU layout is positional. Column indices are named constants in `sources.py`
and validated against the header row on every parse. The header is located by
content rather than line number, the column count must be exactly 32, and the
`MOD` and `DESCRIPTION` headers must be where they are expected. A layout change
fails the parse instead of producing a wrong number.

GPCI columns are located by matching header text (`"2026 PE GPCI"` and so on)
rather than position, because the GPCI header names its year. A test reverses
the column order and asserts the parsed values are identical.

### Provenance

Every `FeeDerivation` carries a three-link chain per file:

```
pprrvu-2026q1.csv  ->  PPRRVU2026_Jan_nonQPP.csv  ->  rvu26a-updated-12-29-2025.zip
   committed fixture         CMS member file              CMS release archive
```

Each link carries its own sha256 and the CMS release date. Upstream hashes are
recorded in the manifest at build time, so an offline run can still name them.
`source` covers two files, the RVU file and the GPCI file, because a derivation
reads both. A single hash would misstate where the geographic indices came from.

---

## 7. How data flows

The demo, the tests and CI never touch the network or a database. They read a
2.5 KB fixture committed next to the code. The full CMS download and Postgres
are both opt-in.

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

### What happens on a call

1. **Resolve the vintage.** `(2026, 1)` becomes `RVU26A`, or raises
   `VintageError`.
2. **Load and verify.** Fixtures are hashed against `manifest.json`. A
   hand-edited fixture fails here.
3. **Pick the modifier.** `26` and `TC` select a different row, payment-scaling
   modifiers are rejected.
4. **Look up the RVU row** by `(code, modifier)`, then check the status code.
5. **Look up the GPCI row** by locality.
6. **Choose the PE RVU** by place of service, honouring the `NA` flag.
7. **Compute** inside the pinned decimal context, recording each step.
8. **Round once** and return the `FeeDerivation`.

---

## 8. The code, module by module

Seven files. Read them in this order.

```
worth_fees/
  money.py        Decimal context, rounding, formatting
  provenance.py   sha256 helpers, SourceFile / Sources chains
  models.py       PlaceOfService, FeeDerivation, error taxonomy
  sources.py      Pinned vintages, CMS parsing, cache, fixtures
  fees.py         expected_allowed()
  sql.py          Postgres load script emitter
  cli.py          Command line entry point
  fixtures/       Stripped CMS subsets + manifest.json
```

| Module | What lives there |
| --- | --- |
| `money.py` | The pinned decimal context and the rounding rule. Start here. It is short, and everything else depends on it. |
| `models.py` | `PlaceOfService`, `CodeSystem`, `FeeDerivation`, and the error taxonomy. |
| `provenance.py` | sha256 helpers and the `SourceFile` chain. |
| `sources.py` | The biggest file. Pinned vintages, the CMS parsers, the download cache, fixture load and build. |
| `fees.py` | `expected_allowed()`. The formula, the refusals, the trace. |
| `sql.py` | Emits a Postgres load script. Writes SQL text, never connects. |
| `cli.py` | The `worth-fees` command. |

Tests mirror this. `test_money.py` guards the decimal discipline,
`test_sources.py` covers fixture integrity and copyright hygiene,
`test_fees.py` checks the formula against an independent recomputation,
`test_sql.py` asserts the SQL and Python cannot drift apart, and
`test_published_rates.py` holds the CMS-verification story.

Requires Python 3.13+. `mypy --strict` clean.

---

## 9. Command line reference

Everything works offline except the two commands that say otherwise.
[`README.md`](README.md#install) covers getting the command onto your PATH.
Examples here use the `uv run` form, which works anywhere in the repo without
setup.

### `price <code> <place> <date>`

```bash
uv run worth-fees price 99213 CA18 2026-03-14
```

| Argument | What it accepts | How to see the options |
| --- | --- | --- |
| `<code>` | Any HCPCS/CPT code in the release, five characters. Only status `A`, `R` and `T` are payable. Anything else raises `NotPayableError` rather than returning a number. | The committed fixture holds 7 codes, the full release has 19,226 rows. Load the database to browse them. |
| `<place>` | A Medicare locality, `<state><2 digits>`. `CA18`, `ca-18` and `ca 18` all resolve to `CA18`. | `worth-fees localities` (fixture) or `worth-fees localities --full` (all 109). |
| `<date>` | A service date `2026-03-14`, a quarter `2026Q2`, a year `2026` (meaning Q1), or `today`. Prefer the service date. The rate that applies to a claim is the one in force on the day the service happened, not the newest published. | `worth-fees vintages` lists every pinned release and the service dates it governs. |

| Option | Effect |
| --- | --- |
| `--facility` | Price the facility (hospital) setting. Default is non-facility (office). |
| `--modifier X` | Repeatable. `26` professional component, `TC` technical component. Payment-scaling modifiers are refused. |
| `--amount` | Print only the dollar amount, for scripting. |
| `--json` | Print the whole derivation, including the trace and the full provenance chain, as JSON. |

```bash
uv run worth-fees price 99213 CA18 2026-03-14              # full derivation
uv run worth-fees price 99213 CA18 2026-03-14 --amount     # 104.89
uv run worth-fees price 99213 CA18 today --facility        # hospital setting
uv run worth-fees price 71046 CA18 2026Q3 --modifier 26    # professional component
uv run worth-fees price 99213 CA18 2026-03-14 --json       # machine-readable
```

### Everything else

| Command | What it does | Network? |
| --- | --- | --- |
| `localities [--full]` | The valid `<place>` values, fixture or all 109. | no |
| `vintages` | Every pinned release and the service dates it governs. | no |
| `demo` | A worked example, no arguments needed. | no |
| `verify-rates` | Print amounts to check by hand against CMS. | no |
| `export-sql [--full]` | Postgres load script. `--full` emits every row. | `--full` needs the cache |
| `build-fixture` | Re-download the pinned release, verify its hash, rebuild fixtures. | yes |

Errors print to stderr and exit `2`.

---

## 10. The database

The schema lives in
[`migrations/0001_fee_schedule.sql`](migrations/0001_fee_schedule.sql).
Everything in it is public CMS data. No PHI, no institution or payer rates. It
can be dropped and rebuilt from the published archives at any time.

Postgres rather than SQLite because SQLite has no true decimal type. Its
`NUMERIC` affinity stores `1.30` as a binary float, which breaks the one
property this package exists to have.

### Running it

```bash
just db-up            # Postgres in Docker; the schema is applied on first boot
just db-load          # load the committed fixture
just db-load --full   # load all ~19k rows (needs the CMS cache)
just db-shell         # psql, inside the container
just db-reset         # destroy the volume and start clean
```

`compose.yaml` pins the server version and publishes it on `127.0.0.1:55432`,
off the default port so it cannot collide with a Postgres installed later. For a
GUI, connect to host `127.0.0.1`, port `55432`, user `worth`, password `worth`,
database `worth`. `psql` runs inside the container, so none of this requires
Postgres on the host. Nothing in the test suite or CI touches a database.

To load an external Postgres instead, pipe the script into your own client:

```bash
uv run worth-fees export-sql --full | psql "$DATABASE_URL" -v ON_ERROR_STOP=1
```

The loader emits SQL text rather than connecting. A driver would be the
package's first runtime dependency, and a `.sql` file travels into an air-gapped
environment in a way a Python client does not.

| Object | Grain | Rows per release |
| --- | --- | --- |
| `fee_schedule_release` | One CMS publication | 1 |
| `source_file` | One provenance link | 3 |
| `rvu` | `(release, code, modifier)` | 19,226 |
| `gpci` | `(release, locality)` | 109 |
| `allowed_amount` | view, the priced cross product | ~4.2M, computed |

Three things the schema enforces:

- **`numeric` throughout.** No `real`, no `double precision`. A test asserts the
  migration contains neither.
- **`round_half_even()`.** Postgres `round()` rounds half away from zero,
  worth-fees rounds half to even. They disagree by a cent on an exact tie, so
  the schema ships a function that matches the Python, and the view uses it.
- **A release is immutable.** CMS reissues files, and each reissue is a new
  release rather than an edit.

Verified against PostgreSQL 16 by loading all 19,226 rows and comparing 50,084
amounts across four localities, including the two where the work GPCI floor
changes the answer, against `expected_allowed()`. Zero mismatches.

The release-overlap guard needs `btree_gist`, standard contrib, present on RDS
and Aurora. It installs conditionally and raises a warning if unavailable rather
than failing the migration.

### `fee_schedule_release`

One row per CMS publication. This is both the time dimension and the provenance
anchor. Time is not a lookup table you join to. It versions everything, and the
thing being versioned is a published file with a hash.

| Column | Type | Meaning |
| --- | --- | --- |
| `release_id` | text PK | CMS's label, e.g. `RVU26A`. A = January, B = April, C = July, D = October. |
| `rule_year` | smallint | Calendar year the fee schedule belongs to. |
| `quarter` | smallint | 1 to 4. |
| `payment_basis` | text | `non-qualifying-apm` or `qualifying-apm`. CY2026 has two conversion factors, and which one this release carries is part of its identity. |
| `work_gpci_basis` | text | `floor` or `no-floor`, which work-GPCI column was used. Versioned with the data rather than buried in code. |
| `conversion_factor` | numeric(12,4) | Dollars per RVU. `33.4009` for CY2026 non-qualifying. |
| `released_on` | date | The date CMS stamped the file. |
| `effective` | daterange | Service dates these rates govern. Use `effective @> claim_date` to resolve a claim to a release. |
| `source_url` | text | Where it was downloaded from. |
| `archive_sha256` | char(64) | Hash of the ZIP. Re-download and re-hash to audit. |
| `retrieved_at` | timestamptz | When we fetched it, as distinct from when it applied. You need both to explain a number you published last year. |
| `superseded_by` | text FK | Set when CMS reissues. Null means still in force. |

An exclusion constraint prevents two in-force releases of the same payment basis
from covering the same service date, so a claim date resolves to exactly one
release. It permits the qualifying-APM basis over the same dates, which is the
CY2026 dual-conversion-factor case.

### `rvu`

The per-code numbers. Grain: one row per `(release, code, modifier)`, 19,226
rows per release. Note what is absent: no descriptor column.

| Column | CMS col. | Meaning |
| --- | ---: | --- |
| `release_id` | | Which publication this row came from. |
| `hcpcs` | 0 | The five-character procedure code. |
| `modifier` | 1 | `''`, `26`, `TC` or `53`. Part of the key, since `71046` and `71046-26` are different rows with different RVUs. |
| `code_system` | derived | `cpt-i` / `cpt-ii` / `cpt-iii` (AMA-copyrighted) or `hcpcs-ii` (CMS, public domain). Publication decisions differ, so the distinction is a column. |
| `status_code` | 3 | Payability. See the status table above. |
| `global_days` | 14 | Follow-up days included in the payment. |
| `work_rvu` | 5 | Physician work. |
| `pe_rvu_nonfacility` | 6 | Practice expense in the physician's own office. |
| `pe_rvu_nonfacility_na` | 7 | **True means not payable in that setting.** The number beside it is a copy of the other setting's value. |
| `pe_rvu_facility` | 8 | Practice expense in a hospital. |
| `pe_rvu_facility_na` | 9 | The same, other setting. |
| `mp_rvu` | 10 | Malpractice. |
| `source_role` | | Which `source_file` row this came from. |

The CMS file has 32 columns, the package reads 11. The unread ones are real
data, just out of scope for now: bilateral and multiple-procedure indicators,
pre/intra/post-op work splits, the PC/TC indicator, endoscopic base codes, and
OPPS payment caps. If you later model payment-adjusting modifiers, columns 18 to
22 are where you will go.

### `gpci`

Grain: one row per `(release, locality)`, 109 rows.

| Column | Meaning |
| --- | --- |
| `locality` | State plus two-digit locality number, e.g. `CA18`. The join key. |
| `state`, `locality_number` | The two halves, kept separately for querying. |
| `locality_name` | Human name, e.g. `LOS ANGELES-LONG BEACH-ANAHEIM`. |
| `mac` | Medicare Administrative Contractor, the private company that processes claims for the region. |
| `work_gpci_no_floor` | Work index as calculated. |
| `work_gpci_floor` | Work index with the statutory 1.0 floor applied. Differs from the above for 47 of 109 localities. |
| `pe_gpci` | Practice-expense index. The widest-varying of the three. |
| `mp_gpci` | Malpractice index. Varies enormously, since liability costs are intensely local. |

### `source_file`

The provenance chain, one row per link, self-referencing via `derived_from`.
Three rows per release. The committed fixture points at the CMS member file,
which points at the archive. Each carries its own `sha256`.

### `allowed_amount`, a view rather than a table

This is the master table you would expect, and it deliberately is not one. The
inputs are ~19,226 + 109 rows. Their cross product is ~4.2 million per release
and contains nothing the inputs do not, so it is computed rather than stored.

It filters out non-payable status codes, `NA` settings, and modifier `53`, so a
plain `SELECT` cannot return a number for a service that has no allowed amount.
Modifier `53` has RVU rows in the CMS file but scales payment, so it is stored
in `rvu` and excluded from the view. A test parses the migration and asserts its
modifier filter still matches the Python. The view exposes every input alongside
`adjusted_rvu_total` and the final `amount`, so any row can be re-checked by
hand.

```sql
SELECT hcpcs, setting, work_rvu, pe_rvu, amount
FROM allowed_amount
WHERE locality = 'CA18' AND hcpcs IN ('99213','29881')
ORDER BY hcpcs, setting;
```

---

## 11. Decisions and open questions

Two pins a reviewer will ask about. Both are recorded in the derivation trace
rather than buried in code, and both are reversible.

### CY2026 has two conversion factors

The non-qualifying-APM file carries 33.4009 across 19,226 codes, the
qualifying-APM (QPP) file carries 33.5675 across 11,811. The package models the
non-qualifying one. Modelling both changes the API signature, so it was left as
a deliberate pin.

### The work GPCI floor

CY2026 Q1 published the work index twice, with and without a 1.0 floor, because
whether the floor is in force is a statutory question rather than a CMS one.
They differ for 47 of 109 localities, so the choice is not cosmetic. Alabama
swings 0.988 to 1.000. `APPLY_WORK_GPCI_FLOOR` in `sources.py` defaults to the
with-floor column. The fixture locality `CA18` is floor-neutral (1.041 either
way), so the demo amount does not depend on this.

CMS has since answered this. From Q2 2026 onward the file carries only
`2026 PW GPCI (with 1.0 Floor)***`, and the unfloored column is gone. The floor
is in force, and the default was right. The parser handles both layouts. When
the unfloored column is absent, the floored value stands for both.

### Nothing has been verified against CMS yet

`VERIFIED_CMS_RATES` in `tests/test_published_rates.py` is deliberately empty
and its test skips. `COMPUTED_UNVERIFIED` holds what the implementation
currently produces, which is a drift alarm rather than evidence. Until that
changes, the claim is that the arithmetic is reproducible, not that it is right.

The procedure for closing this out is in
[`README.md`](README.md#help-verify-the-numbers). If a row does not match, the
likely culprits, in order, are the work-GPCI floor column, the
facility/non-facility setting, and the choice of conversion factor.

---

## 12. Gotchas that will bite

Each of these has produced a wrong answer at least once.

**The NA indicator is not optional.** CMS copies the other setting's value into
an `NA` column. Reading the number without the flag gives you a real-looking
price for an unpayable service. This is the easiest way to publish a wrong
number.

**Postgres and Python round differently.** Postgres `round(numeric, 2)` rounds
half away from zero, the package rounds half to even. `round(0.125, 2)` is
`0.13` in Postgres and `0.12` in Python. The schema ships `round_half_even()`
and the view uses it. Never use bare `round()` on money here.

**Never let a float near the money.** Not in Python, not in a `double precision`
column, not in a dataframe you add later. A test walks every field of a result
at runtime, and another asserts the migration contains no float types.

**The fixture is a sample, not the fee schedule.** Seven codes, one locality.
`UnknownCodeError` usually means not in the fixture rather than not a real code.
Widen `FIXTURE_CODES` in `cli.py` and run `just refresh-fixture`.

**The allowed amount is not what anyone was paid.** It is the schedule figure.
Medicare pays 80% of it, the patient owes the rest, sequestration trims the
government's share, and quality-program adjustments move it again. None of that
is modelled. The 835 remittance carries what a payer actually allowed, which is
a different number from a different source.

**A release is immutable.** Never `UPDATE` an `rvu` row. CMS reissues files.
`RVU24AR`, `RVU25D-0`, and our own `rvu26a-updated-12-29-2025` are all
corrections, and each one is a new release. Mutate rows in place and the
recorded hash stops meaning anything.
