# worth-fees

`worth-fees` serves as a utility package to returns fee data to eventually help generate the 'complexity matched expected payment'. It is a tool that turns a given code, in a given place in a given time into a defensible dollar amount. It is an entirely deterministic tool, the same inputs and the same data produce the same amount on any machine, on any architecture, in any year. The amount arrives with the arithmetic that produced it and the sha256 of every file it was read from.

> **New here?** [`GUIDE.md`](GUIDE.md) explains the Medicare domain, where the CMS
> data comes from, how it flows through the code, and what every column means.
> This README is the reference; that is the explanation.

## The API

One public function.

```python
from worth_fees import PlaceOfService, expected_allowed

derivation = expected_allowed("99213", [], "CA18", PlaceOfService.NON_FACILITY, 2026, 1)
```

| Parameter | Type | Notes |
| --- | --- | --- |
| `code` | `str` | HCPCS/CPT code. Case and surrounding whitespace are normalised. |
| `modifiers` | `Sequence[str]` | As billed. See [Modifiers](#modifiers). |
| `locality` | `str` | Medicare locality as `<state><2-digit number>`. `CA18`, `ca-18` and `ca 18` all resolve to `CA18`. |
| `place_of_service` | `PlaceOfService \| str` | `"non-facility"` or `"facility"`. |
| `rule_year` | `int` | Calendar year of the fee schedule. |
| `quarter` | `int` | Quarterly release, 1-4. |

Returns a frozen `FeeDerivation`:

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

## What it prices

Medicare Physician Fee Schedule only.

```
(work_rvu * work_gpci + pe_rvu * pe_gpci + mp_rvu * mp_gpci) * CF
```

`pe_rvu` is the facility or non-facility practice-expense RVU depending on `place_of_service`. The conversion factor is read from the RVU file itself; the parser rejects a file carrying more than one distinct value.

**Out of scope:** OPPS, ASC, IPPS, anesthesia, clinical lab, DMEPOS, Part B drugs, and commercial rates. Also out of scope within the PFS itself: payment adjustments driven by modifiers, sequestration, the 80/20 beneficiary split, and quality-program adjustments. `amount` is the fee schedule allowed amount — not what a payer remitted, and not what landed in a bank account.

### A derivation

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

## Constraints

These are enforced, not aspirational.

**Money is `Decimal`. Never `float`, anywhere.** A float cannot represent `0.01`. There is no float in the package outside docstrings, and a test walks every field of a result to confirm none arrives at runtime.

**Arithmetic runs in an explicitly pinned decimal context.** `Context(prec=28, rounding=ROUND_HALF_EVEN, traps=[InvalidOperation, DivisionByZero])`, entered via `localcontext()`. Python's ambient decimal context is mutable global state — any caller or imported library can change its precision or rounding and silently change the answers. Tests set a hostile ambient context (`prec=4`, `ROUND_UP`) and assert the result is unchanged. The traps matter as much as the precision: a malformed input raises rather than yielding `NaN`.

**Rounding happens exactly once, on the final amount.** Intermediate products keep full 28-digit precision. Rounding each term as you go is how two implementations of one formula end up a penny apart.

**Standard library only.** No runtime dependencies at all — not numpy, not pandas, not `requests`. Results are bit-identical on ARM and x86, and the dependency audit is the repo.

**Every derived value carries provenance** back to a source file and its sha256.

**No CPT descriptors.** CPT is AMA-copyrighted and this repository is public. The parser does not read the descriptor column and `build-fixture` blanks it before writing. Two tests enforce this: one asserts the descriptor column is empty in every committed fixture row, another asserts no lowercase character appears in any data row at all.

## Data

Pinned to one CMS release, by hash.

| | |
| --- | --- |
| Release | `RVU26A`, published 2025-12-29 |
| Archive | `rvu26a-updated-12-29-2025.zip` |
| sha256 | `91b9bdd5459bc4c19d4f8203410b29a672db37def2e70f01ef627b8b18fc7482` |
| RVU member | `PPRRVU2026_Jan_nonQPP.csv` |
| GPCI member | `GPCI2026.csv` |
| Conversion factor | `33.4009` |

Vintages live in `PINNED_VINTAGES` in `sources.py`, keyed by `(rule_year, quarter)`. An unpinned year or quarter raises `VintageError` rather than falling back to the nearest available.

### Two paths, one parser

**Online.** `fetch_archive()` downloads the archive into a gitignored cache and hashes it *before* anything is parsed. A hash mismatch raises `SourceIntegrityError`. Cache location is `$WORTH_CACHE_DIR`, else `$XDG_CACHE_HOME/worth/cms`, else `~/.cache/worth/cms`.

**Offline.** `load()` reads small stripped fixtures committed alongside the package, verifying each against `fixtures/manifest.json` before parsing. This is what `just demo`, the test suite, and CI use — none of them touch the network.

The fixtures are row subsets of the real CMS files *in the real CMS layout*, so both paths run through the same parser and the fixture cannot silently drift from the format it claims to sample. Current contents: 9 rows across 7 codes, and one locality.

| Code | Modifiers | Global | Exercises |
| --- | --- | --- | --- |
| `99213`, `99214` | — | XXX | Facility / non-facility PE split |
| `99232` | — | XXX | `NA` non-facility indicator |
| `20610` | — | 000 | Payable in both settings |
| `29881` | — | 090 | `NA` non-facility, 90-day global |
| `71046` | —, `26`, `TC` | XXX | Professional / technical component split |
| `93000` | — | XXX | `NA` facility indicator |

Locality: `CA18`, Los Angeles-Long Beach-Anaheim, MAC 01182.

Rebuild with `just refresh-fixture` (requires network). Widen the sample by editing `FIXTURE_CODES` / `FIXTURE_LOCALITIES` in `cli.py` and rebuilding — the manifest and its hashes regenerate together.

### Parsing

The RVU layout is positional. Column indices are named constants in `sources.py` and validated against the header row on every parse: the header is located by content rather than line number, the column count must be exactly 32, and the `MOD` and `DESCRIPTION` headers must be where they are expected. A layout change is a loud failure rather than a wrong number.

GPCI columns are located by matching header *text* (`"2026 PE GPCI"` and so on), not position, because the GPCI header names its year. A test reverses the column order and asserts the parsed values are identical.

### Provenance

Every `FeeDerivation` carries a three-link chain per file:

```
pprrvu-2026q1.csv  →  PPRRVU2026_Jan_nonQPP.csv  →  rvu26a-updated-12-29-2025.zip
   committed fixture         CMS member file              CMS release archive
```

Each link carries its own sha256 and the CMS release date. Upstream hashes are recorded in the manifest at build time, so an offline run can still name them. `source` covers two files — the RVU file and the GPCI file — because a derivation reads both; a single hash would misstate where the geographic indices came from.

## Refusals

A wrong number that looks confident is worse than an error. All errors subclass `WorthFeesError`.

| Error | Raised when |
| --- | --- |
| `UnknownCodeError` | The code, with that modifier, is not in the file. Names which modifier variants do exist. |
| `UnknownLocalityError` | The locality is not in the GPCI file. Lists what is available. |
| `NotPayableError` | Non-payable CMS status code (`B`, `C`, `E`, `I`, `J`, `M`, `N`, `P`, `X`), or an `NA` practice-expense indicator for the requested setting. |
| `UnsupportedModifierError` | A modifier that scales payment, or one not recognised at all. |
| `VintageError` | No pinned vintage for that year and quarter. |
| `SourceIntegrityError` | A file's sha256 does not match its pinned or manifest value, or the layout is not what was expected. |

The `NA` check matters more than it looks: CMS repeats the other setting's value in an `NA` column, so reading the number without checking the indicator yields a plausible amount for a service that is not payable in that setting at all.

### Modifiers

- **`26` and `TC`** select a different RVU row — the professional and technical components. Mutually exclusive.
- **Inert modifiers** are accepted and recorded but do not change the formula: `24`, `25`, `57`, `59`, `76`, `77`, `95`, `GA`, `GT`, `GY`, `GZ`, `LT`, `RT`, `XE`, `XP`, `XS`, `XU`.
- **Payment-adjusting modifiers are rejected**, not ignored: `50`, `51`, `52`, `53`, `54`, `55`, `56`, `62`, `66`, `78`, `80`, `81`, `82`, `AS`. Returning the unadjusted amount would be a wrong answer wearing the costume of a right one.
- **Anything else is rejected.** The inert list is an allowlist, so an unrecognised modifier fails rather than being silently dropped.

## Two decisions taken, both reversible

Both are recorded in every trace rather than buried in a column index.

**CY2026 has two conversion factors.** The non-qualifying-APM file carries `33.4009` across 19,226 codes; the qualifying-APM (QPP) file carries `33.5675` across 11,811. `worth-fees` pins the non-qualifying CF. Modelling both would change the API signature.

**The work GPCI floor.** CMS publishes the 2026 work GPCI twice — with and without the 1.0 floor — because whether the floor is in force is a statutory question. They differ for 47 of the 109 localities in the file. `APPLY_WORK_GPCI_FLOOR` in `sources.py` defaults to the with-floor column. The fixture locality `CA18` is floor-neutral (1.041 either way), so the demo amount does not depend on this.

## Verification status

**No amount produced by this package has been checked against CMS.** `VERIFIED_CMS_RATES` in `tests/test_published_rates.py` is deliberately empty and its test skips.

A second table, `COMPUTED_UNVERIFIED`, holds what the implementation currently produces. It is a drift alarm — it catches a refactor that silently changes a published number — and makes no correctness claim.

### How to validate it — about half an hour

```bash
worth-fees verify-rates
```

That prints a worksheet: nine cases with the MAC, locality, setting and our
amount, and a blank column for CMS's figure. Then, in the
[CMS Physician Fee Schedule Look-Up Tool](https://www.cms.gov/medicare/physician-fee-schedule/search):

1. Accept the AMA CPT licence click-through the tool opens with.
2. Set **Year** to 2026 and **Type of information** to *Pricing Information*.
3. Search a **single HCPCS code**, with the modifier from the `mod` column
   (blank means all modifiers).
4. Choose the **MAC and locality** from the worksheet — `01182` / `18` for the
   committed fixture.
5. Compare CMS's *Non-Facility Price* and *Facility Price* against ours.

Every row that matches moves into `VERIFIED_CMS_RATES`, which switches the
skipped correctness test on. Record who checked and when in the commit message.

A row that does **not** match is the most valuable bug this repository can
surface, and worth stopping for. Likely culprits, in order: the work-GPCI floor
column, the facility/non-facility setting, and the choice of conversion factor.

## Database

`migrations/0001_fee_schedule.sql` defines a Postgres schema for the reference
data. Postgres rather than SQLite because SQLite has no true decimal type — its
`NUMERIC` affinity stores `1.30` as a binary float, which breaks the one
property this package exists to have.

```bash
just db-up            # Postgres in Docker; the schema is applied on first boot
just db-load          # load the committed fixture
just db-load --full   # load all ~19k rows (needs the CMS cache)
just db-shell         # psql, inside the container
just db-reset         # destroy the volume and start clean
```

`compose.yaml` pins the server version and publishes it on `127.0.0.1:55432`,
off the default port so it cannot collide with a Postgres installed later.
`psql` runs inside the container, so none of this requires Postgres on the
host. Nothing in the test suite or CI touches a database.

To load an external Postgres instead, pipe the script into your own client:

```bash
uv run worth-fees export-sql --full | psql "$DATABASE_URL" -v ON_ERROR_STOP=1
```

The loader emits SQL text rather than connecting: a driver would be the
package's first runtime dependency, and a `.sql` file travels into an air-gapped
environment in a way a Python client does not.

| Object | Grain | Rows per release |
| --- | --- | --- |
| `fee_schedule_release` | One CMS publication | 1 |
| `source_file` | One provenance link | 3 |
| `rvu` | `(release, code, modifier)` | 19,226 |
| `gpci` | `(release, locality)` | 109 |
| `allowed_amount` | *view* — the priced cross product | ~4.2M, computed |

`allowed_amount` is a view, not a table. The cross product is ~200× the size of
its inputs and contains nothing they do not.

Three things the schema enforces rather than documents:

- **`numeric` throughout.** No `real`, no `double precision`. A test asserts the
  migration contains neither.
- **`round_half_even()`.** Postgres `round()` rounds half *away from zero*;
  worth-fees rounds half to *even*. They disagree by a cent on an exact tie, so
  the schema ships a function that matches the Python, and the view uses it.
- **The view prices exactly what `expected_allowed()` prices.** Modifier `53`
  has RVU rows in the CMS file but scales payment, so it is stored in `rvu` and
  excluded from the view. A test parses the migration and asserts its modifier
  filter still matches the Python.

Verified against PostgreSQL 16 by loading all 19,226 rows and comparing 50,084
amounts across four localities — including the two where the work GPCI floor
changes the answer — against `expected_allowed()`. Zero mismatches.

The release-overlap guard needs `btree_gist` (standard contrib, present on RDS
and Aurora). It installs conditionally and raises a warning if unavailable
rather than failing the migration.

## CLI

### Getting the command

`worth-fees` is installed into the workspace virtualenv, not onto your PATH, so
a bare `worth-fees` gives `command not found` until you do one of these:

```bash
uv run worth-fees price 99213 CA18 2026-03-14   # no setup; works anywhere in the repo
```

```bash
source .venv/bin/activate                        # then `worth-fees ...` in this shell
```

```bash
uv tool install --editable ./packages/worth-fees # installs into ~/.local/bin
export PATH="$HOME/.local/bin:$PATH"             # add to ~/.zshrc to make it stick
```

Examples below assume it is on your PATH; otherwise prefix them with `uv run`.

### Pricing

The main call takes three positional arguments — a code, a place, and a date.

```bash
worth-fees price 99213 CA18 2026-03-14
```

| Argument | What it accepts | How to see the options |
| --- | --- | --- |
| `<code>` | Any HCPCS/CPT code in the release, five characters. Only status `A`, `R` and `T` are payable; anything else raises `NotPayableError` rather than returning a number. | The committed fixture holds 7 codes; the full release has 19,226 rows. Load the database to browse them. |
| `<place>` | A Medicare locality, `<state><2 digits>`. `CA18`, `ca-18` and `ca 18` all resolve to `CA18`. | `worth-fees localities` (fixture) or `worth-fees localities --full` (all 109). |
| `<date>` | A service date `2026-03-14`, a quarter `2026Q2`, a year `2026` (meaning Q1), or `today`. Prefer the service date: the rate that applies to a claim is the one in force on the day the service happened, not the newest published. | `worth-fees vintages` lists every pinned release and the service dates it governs. |

| Option | Effect |
| --- | --- |
| `--facility` | Price the facility (hospital) setting. Default is non-facility (office). |
| `--modifier X` | Repeatable. `26` professional component, `TC` technical component. Payment-scaling modifiers are refused. |
| `--amount` | Print only the dollar amount — for scripting. |
| `--json` | Print the whole derivation, including the trace and the full provenance chain, as JSON. |

```bash
worth-fees price 99213 CA18 2026-03-14              # full derivation
worth-fees price 99213 CA18 2026-03-14 --amount     # 104.89
worth-fees price 99213 CA18 today --facility        # hospital setting
worth-fees price 71046 CA18 2026Q3 --modifier 26    # professional component
worth-fees price 99213 CA18 2026-03-14 --json       # machine-readable

worth-fees localities --full   # the 109 valid <place> values
worth-fees vintages            # the valid <date> ranges
worth-fees demo                # a worked example, no arguments needed
worth-fees verify-rates        # amounts to check against CMS
worth-fees build-fixture       # re-download and rebuild fixtures (network)
worth-fees export-sql --full   # Postgres load script for every row
```

Errors print to stderr and exit `2`.

## Pinned releases

All four CY2026 quarters are pinned by hash and committed as fixtures, so every
date in the year resolves offline.

| `<date>` | Release | Governs service dates | Rows |
| --- | --- | --- | ---: |
| `2026Q1` | `RVU26A` | 2026-01-01 – 2026-03-31 | 19,226 |
| `2026Q2` | `RVU26B` | 2026-04-01 – 2026-06-30 | 19,277 |
| `2026Q3` | `RVU26C` | 2026-07-01 – 2026-09-30 | 19,356 |
| `2026Q4` | `RVU26D` | 2026-10-01 – 2026-12-31 | 19,453 |

Every one of those carries an `-updated-` suffix upstream: CMS revised each
after first publishing it. Within CY2026 the quarterly releases add codes and
reclassify non-payable ones but do not change any payable price. Across years
they do — the CY2025 conversion factor was `32.3465` against CY2026's
`33.4009`, and 99213's non-facility PE RVU moved 1.35 to 1.46.

Earlier years need parser work, not just a URL: the CY2025 file has 31 columns
to CY2026's 32 and no QPP/non-QPP split. The parser refuses a layout it does not
recognise rather than reading the wrong columns.

## Layout

```
worth_fees/
  money.py        Decimal context, rounding, formatting
  provenance.py   sha256 helpers, SourceFile / Sources chains
  models.py       PlaceOfService, FeeDerivation, error taxonomy
  sources.py      Pinned vintages, CMS parsing, cache, fixtures
  fees.py         expected_allowed()
  cli.py          Command line entry point
  fixtures/       Stripped CMS subsets + manifest.json
```

Requires Python 3.13+. `mypy --strict` clean.
