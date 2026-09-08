# worth-cli

The one command a user or Meridian invokes. It has no arithmetic of its own:
`worth-cli run` calls `worth_complexity.pipeline.run` and prints its report or,
with `--json`, the same `Run` object converted field by field into plain JSON
by `worth_cli.serialize.to_jsonable`, so what Meridian's "raw output" view
shows is genuinely the packages' own output, in both a text and a machine
form, not a re-derived summary. `worth-cli price` and `worth-cli db` are pure
forwarders to `worth-fees` and `worth-db`'s own command lines, argument for
argument, including their own `--help`.

Standard library only in this package's own code — `argparse`, `json`,
`dataclasses` — matching the two numeric packages it sits on top of.

```bash
worth-cli run                          # the pipeline report, on the packaged synthetic fixture
worth-cli run --json                   # the same run, as JSON, plus the three package versions
worth-cli price 99213 CA18 2026-03-14  # forwards to `worth-fees price`
worth-cli db migrate                   # forwards to `worth-db migrate`
worth-cli version                      # all four package versions
```
