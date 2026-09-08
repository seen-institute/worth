# worth-db

The database half of what used to be `worth-api`: the code that talks to
Postgres and nothing else. It applies `worth-fees`' schema, loads the pinned
CMS fee-schedule vintages into it, and rebuilds a `FeeSchedule` from the rows
it wrote so that `worth_fees.expected_allowed` prices from the database the
same way it prices from the committed fixture.

```bash
worth-db migrate               # apply the schema if it is missing
worth-db load                  # load every pinned vintage (fixture, unless the CMS archive is cached)
worth-db load --vintage 2026Q1 --full   # one vintage, refusing to fall back to the fixture
worth-db status                # what is loaded, and how
```

Every subcommand takes `--url postgresql://...` or reads `DATABASE_URL` from
the environment; the process refuses with a clear message if neither is set.
No command ever prints the URL's credentials, only the host, port and
database name.

## The drift guarantee

`worth-fees` prices from a `FeeSchedule` it builds from its own committed
fixture. `schedule_from_db` builds the identical dataclass from the rows the
load script wrote to Postgres. `tests/test_schedule.py` prices every fixture
code, in every fixture locality, in both settings and on both payment bases,
both ways, and asserts the amount and the derivation trace are identical.
Nothing in this package re-derives a number; the formula, the trace and the
refusals are `worth-fees`' own.

Set `DATABASE_URL` (the repository's `compose.yaml` exposes one at
`postgresql://worth:worth@127.0.0.1:55432/worth`) to run the tests that need
Postgres; they skip when it is unset.
