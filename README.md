# WORTH

[![CI](https://github.com/joeymarino-seenhealth/worth/actions/workflows/ci.yml/badge.svg)](https://github.com/joeymarino-seenhealth/worth/actions/workflows/ci.yml)

WORTH is an open standard, goverened by [Seen Institute](https://seen.institute). Its aim is help in
determining whether a healthcare payment is adequate for the actual work that was done. 
It in no way serves to indicate what the price should be. This repositoryserves as the 
reference implementation.

## Directory

- **[`worth-fees`](packages/worth-fees)**. Medicare Physician Fee Schedule allowed
  amounts, with derivation traces and source provenance.
- **[`worth-complexity`](packages/worth-complexity)**. Layer A complexity scoring,
  the Method 0 slope, the fee schedule's complexity relation fitted on the
  comparator cohort, each payer's multiple of the schedule, and the payment
  adequacy ratio, computed from a partner's operative extract and 835 remittance.
- **[`worth-db`](packages/worth-db)**. The database half of what used to be
  `worth-api`: applies `worth-fees`' schema to Postgres, loads the pinned CMS
  vintages into it, and rebuilds a `FeeSchedule` from the rows it wrote. The
  one package in the workspace with a runtime dependency, `psycopg`.
- **[`worth-cli`](packages/worth-cli)**. The umbrella command: `worth-cli run`,
  `worth-cli price`, `worth-cli db`, `worth-cli version`, over the three
  packages above. Standard library only in its own code.
- More to come soon!

The console and the HTTP API that served it have moved to Meridian, which
depends on these packages instead of hosting the pipeline itself. This
repository is Python packages and a fee-schedule database, nothing else.

## Guides

Start with the [worth-fees field guide](packages/worth-fees/GUIDE.md), then the
[worth-complexity README](packages/worth-complexity/README.md). More to come soon.

## Ground rules

These are constraints on the implementation, not preferences:

- **Arithmetic runs in a pinned decimal context** (`prec=28`, `ROUND_HALF_EVEN`, trapping
  `InvalidOperation` and `DivisionByZero`), never the mutable ambient default.
- **Standard library only in the numeric path.** No numpy, no scipy, no pandas. Results are bit-identical on ARM and x86.
  `worth-complexity` depends on `worth-fees` for the fee schedule; nothing else.
- **Every derived value carries provenance** back to a source file and its sha256.
- **No CPT descriptors in this repository.** CPT is AMA-copyrighted and this repo is
  public; descriptor columns are stripped on ingest and only numeric codes are stored/emitted.

## Working on it

```bash
just test              # pytest
just lint              # ruff check, ruff format --check, mypy --strict
just demo              # the offline worth-fees demo above
just complexity-demo   # Layer A scores and adequacy ratios on the synthetic dataset
just build-dataset     # regenerate that dataset deterministically
just db-up             # Postgres in a container, on :55432
just db-load           # load a fee schedule vintage into it
just test-db           # the tests that need Postgres, against the compose database
```

`just refresh-fixture` re-downloads the pinned CMS release into a gitignored cache,
verifies its hash, and regenerates the small committed fixture that lets `just demo` and
CI run without a network.

## Licence

Apache-2.0. CPT® is a registered trademark of the American Medical Association; this
repository contains no CPT descriptors.
