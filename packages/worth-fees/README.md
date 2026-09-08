# worth-fees

`worth-fees` serves as a utility package that returns fee data to eventually help generate a 'complexity matched expected payment'. It is a tool that turns a given code, in a given place in a given time into a defensible dollar amount. It is entirely deterministic, the same inputs produce the same dollar amount on any machine, on any architecture, in any year. The amount arrives with the arithmetic that produced it and the sha256 of every file it was read from.

> **Looking for the technical detail?** [`GUIDE.md`](GUIDE.md) is the reference.
> The Medicare domain, the formula, the API, the data pipeline, the database
> schema, every CLI flag, and the mistakes that produce wrong numbers. This page
> covers what the package claims, how to run it, and how to help.

## What it is for

It answers one question. What does Medicare's Physician Fee Schedule allow for
this service, in this locality, on this date, and how do you know? Anyone should
be able to redo the arithmetic on paper and re-download the source file to check
the hash.

**In scope.** Medicare Physician Fee Schedule allowed amounts, CY2026, all four
quarters, all 109 localities, both payment bases (the qualifying-APM conversion
factor and the non-qualifying one).

**Not in scope.** OPPS, ASC, IPPS, anesthesia, clinical lab, DMEPOS, Part B
drugs, and commercial rates. Within the PFS itself: payment-adjusting modifiers,
sequestration, the 80/20 beneficiary split, and quality-program adjustments.
The payment-policy indicators those adjustments would need are parsed and
stored, but nothing computes with them.

**What the number is not.** The allowed amount is a schedule figure, not a
payment. It is not what a payer remitted and not what landed in a bank account.
Using it as what someone was paid is a misuse.

## Install

`worth-fees` installs into the workspace virtualenv rather than onto your PATH,
so a bare `worth-fees` gives `command not found` until you do one of these:

```bash
uv run worth-fees demo
```

```bash
source .venv/bin/activate
```

```bash
uv tool install --editable ./packages/worth-fees
```

The last one puts it in `~/.local/bin`. Add that to your `PATH` in `~/.zshrc` to
make it stick. Requires Python 3.13+.

## Run it

Everything below works offline. No network, no database, no configuration.

```bash
just demo
```

That prints one allowed amount, the full derivation trace, and the sha256 of the
CMS file it came from.

```bash
uv run worth-fees price 99213 CA18 2026-03-14
```

Three positional arguments: a code, a locality, and a date. A few more:

```bash
uv run worth-fees price 99213 CA18 2026-03-14 --amount     # 104.89
uv run worth-fees price 99213 CA18 today --facility        # hospital setting
uv run worth-fees price 99213 CA18 today \
    --payment-basis qualifying-apm                         # the APM conversion factor
uv run worth-fees localities --full                        # the 109 valid places
uv run worth-fees counties --state CALIFORNIA              # what a locality covers
uv run worth-fees vintages                                 # the valid dates
```

Every flag and every command is documented in
[GUIDE section 9](GUIDE.md#9-command-line-reference).

## Working on it

```bash
just test    # pytest
just lint    # ruff check, ruff format --check, mypy --strict
just fix     # apply what `just lint` only reports
```

Two commands need the network, and no test or CI job calls them:

```bash
just refresh-fixture   # re-download CMS, verify the hash, rebuild the fixtures
just verify-rates      # print amounts to check by hand against CMS
```

The database is opt-in and never touched by the test suite.
[GUIDE section 10](GUIDE.md#10-the-database) covers it.

## Verification status

No amount produced by this package has been checked against CMS.

`VERIFIED_CMS_RATES` in `tests/test_published_rates.py` is deliberately empty
and its test skips. A second table, `COMPUTED_UNVERIFIED`, holds what the
implementation currently produces. It is a drift alarm, so a refactor cannot
silently change a published number, and it makes no correctness claim.

The claim today is that the arithmetic is reproducible, not that it is right.
Please do not cite an amount from this package as a Medicare rate until that
changes.

### Help verify the numbers

This is about half an hour of careful clerical work, and it is the most useful
contribution to the package right now. No Python required.

```bash
uv run worth-fees verify-rates
```

That prints a worksheet: nine cases with the MAC, locality, setting and our
amount, and a blank column for CMS's figure. Then, in the
[CMS Physician Fee Schedule Look-Up Tool](https://www.cms.gov/medicare/physician-fee-schedule/search):

1. Accept the AMA CPT licence click-through the tool opens with.
2. Set **Year** to 2026 and **Type of information** to *Pricing Information*.
3. Search a **single HCPCS code**, with the modifier from the `mod` column
   (blank means all modifiers).
4. Choose the **MAC and locality** from the worksheet, `01182` / `18` for the
   committed fixture.
5. Compare CMS's *Non-Facility Price* and *Facility Price* against ours.

Every row that matches moves into `VERIFIED_CMS_RATES`, which switches the
skipped correctness test on. Record who checked and when in the commit message.

A row that does not match is worth stopping for. Open an issue with the code,
locality, setting, date and both amounts.
[GUIDE section 11](GUIDE.md#11-decisions-and-open-questions) lists the likely
culprits in the order they are worth checking.

## Copyright and licensing

No CPT descriptors are in this repository, and none may be added. CPT is
AMA-copyrighted and this repo is public. The parser does not read the descriptor
column, the fixture builder blanks it before writing, and two tests enforce it.
One asserts the descriptor column is empty in every committed fixture row, the
other asserts no lowercase character appears in any data row at all. Numeric
codes only. If you widen the fixture, do not work around those tests.

Everything else in the data path is public CMS material and can be re-downloaded
from cms.gov at any time. There is no PHI here, and no institution or payer
rates.

Licensed Apache-2.0. CPT® is a registered trademark of the American Medical
Association.

## Where to go next

| You want to | Read |
| --- | --- |
| Understand RVUs, GPCIs, modifiers and status codes | [GUIDE section 2](GUIDE.md#2-the-domain-in-ten-minutes) |
| Know which conversion factor applies to you | [GUIDE section 11](GUIDE.md#11-decisions-and-open-questions) |
| Follow the arithmetic | [GUIDE section 3](GUIDE.md#3-the-formula-walked-through) |
| Call it from Python | [GUIDE section 1](GUIDE.md#1-what-it-does) |
| Know why it refuses something | [GUIDE section 5](GUIDE.md#5-refusals-errors-and-modifiers) |
| Change the CMS data or the fixture | [GUIDE section 6](GUIDE.md#6-where-the-data-comes-from) |
| Find your way around the code | [GUIDE section 8](GUIDE.md#8-the-code-module-by-module) |
| Query it in Postgres | [GUIDE section 10](GUIDE.md#10-the-database) |
| Avoid a known wrong answer | [GUIDE section 12](GUIDE.md#12-gotchas-that-will-bite) |
