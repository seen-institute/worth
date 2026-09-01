# WORTH

[![CI](https://github.com/joeymarino-seenhealth/worth/actions/workflows/ci.yml/badge.svg)](https://github.com/joeymarino-seenhealth/worth/actions/workflows/ci.yml)

WORTH is an open standard, goverened by [Seen Institute](https://seen.institute). Its aim is help in
determining whether a healthcare payment is adequate for the actual work that was done. It holds that every 
result must be reproducible by anyone at any time: derived by published
arithmetic, from a named source file. This repository
serves as the reference implementation.

```bash
just demo
```

That prints a Medicare allowed amount, the full derivation trace, and the sha256 of the
CMS source file it came from — offline, with no dependencies outside the standard
library.

## Directory

| Package | What it does |
| --- | --- |
| [`worth-fees`](packages/worth-fees) | Medicare Physician Fee Schedule allowed amounts, with derivation traces and source provenance. |

New to the domain or the codebase? Start with the [worth-fees field guide](packages/worth-fees/GUIDE.md).

```python
from worth_fees import PlaceOfService, expected_allowed

derivation = expected_allowed("99213", [], "CA18", PlaceOfService.NON_FACILITY, 2026, 1)

derivation.amount  # Decimal, never float
derivation.trace  # every multiplication and running sum, in order
derivation.source  # filename + sha256 + CMS release date, chained to the upstream zip
```

## Ground rules

These are constraints on the implementation, not preferences:

- **Arithmetic runs in a pinned decimal context** (`prec=28`, `ROUND_HALF_EVEN`, trapping
  `InvalidOperation` and `DivisionByZero`), never the mutable ambient default.
- **Standard library only in the numeric path.** No numpy, no scipy, no pandas. Results are bit-identical on ARM and x86.
- **Every derived value carries provenance** back to a source file and its sha256.
- **No CPT descriptors in this repository.** CPT is AMA-copyrighted and this repo is
  public; descriptor columns are stripped on ingest and only numeric codes are stored/emitted.

## Working on it

```bash
just test    # pytest
just lint    # ruff check, ruff format --check, mypy --strict
just demo    # the offline demo above
```

`just refresh-fixture` re-downloads the pinned CMS release into a gitignored cache,
verifies its hash, and regenerates the small committed fixture that lets `just demo` and
CI run without a network.

## Licence

Apache-2.0. CPT® is a registered trademark of the American Medical Association; this
repository contains no CPT descriptors.
