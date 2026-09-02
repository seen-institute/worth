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
- **[`worth-api`](packages/worth-api)**. The console's backend: serves a partner
  dataset as delivered, runs the pipeline on request, and (soon) prices codes
  against a Postgres copy of the fee schedule.
- **[`app`](app)**, the adequacy console: a React front end over `worth-api`,
  served from AWS Amplify.
- **[`infra`](infra)**. The CDK stacks: the API on App Runner with Postgres on
  RDS, and the Amplify app with the one rewrite that joins them.
- More to come soon!

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
just build-dataset      # regenerate that dataset deterministically
just api-dev           # the backend, on http://localhost:8000/api/docs
just app-dev           # the console, on http://localhost:5173 (proxies /api to :8000)
just up                # backend and Postgres in containers
just types             # regenerate the console's TypeScript types from the API
just infra-synth       # assemble the AWS templates; no credentials needed
```

`just refresh-fixture` re-downloads the pinned CMS release into a gitignored cache,
verifies its hash, and regenerates the small committed fixture that lets `just demo` and
CI run without a network.

## Licence

Apache-2.0. CPT® is a registered trademark of the American Medical Association; this
repository contains no CPT descriptors.
