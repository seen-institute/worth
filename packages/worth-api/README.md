# worth-api

The console's backend. It serves one partner dataset as delivered, runs the
`worth-complexity` pipeline over it on request, and prices codes through
`worth-fees` against a Postgres copy of the CMS fee schedule.

```bash
just api-dev          # starts the compose Postgres, then the API on :8000 against it
just api-dev-offline  # no database: prices from the committed eight-code fixture
just up               # API and Postgres together in containers
just test-db          # the tests that need Postgres
```

`/api/docs` is the OpenAPI UI. Everything the console shows is computed here
at request time; nothing is compiled into the front end.

## The fee schedule

On boot, with `DATABASE_URL` set, the server applies `worth-fees`' schema if
the database is empty, loads every pinned CMS vintage it does not already hold,
and reads each one back into memory. Prices are then computed by
`worth_fees.expected_allowed` over a `FeeSchedule` rebuilt from the rows, so
the formula, the trace and the refusals are the package's own. A drift test
prices every fixture code both ways and demands identical results.

What gets loaded depends on what is available offline. The full CMS archives
are fetched and hash-verified when the image is built, into `/app/cms`; on a
laptop they live in `~/.cache/worth/cms` after `just refresh-fixture`. Without
an archive the committed fixture is loaded instead, and `/api/health` reports
the coverage per release. A release loaded from the fixture is upgraded to the
archive the first time a boot finds one.

Without `DATABASE_URL` there is no database at all: prices come from the
fixture and health says `not configured`.

## Configuration

| variable | meaning | default |
|---|---|---|
| `WORTH_DATASET_DIR` | the partner dataset: `clinical/` and `remittance/` | the packaged synthetic fixture |
| `DATABASE_URL` | Postgres holding the fee schedule | unset: fixture pricing |
| `WORTH_CACHE_DIR` | where the CMS archives are | `~/.cache/worth/cms` |

See `PLAN.md` at the repository root.
