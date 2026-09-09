# worth-cli

The one command a user or Meridian invokes, also installed as the bare
`worth`. It has no arithmetic of its own: `worth-cli run` calls
`worth_complexity.pipeline.run` and prints its report or, with `--json`, the
same `Run` object converted field by field into plain JSON by
`worth_cli.serialize.to_jsonable`, so what Meridian's "raw output" view shows
is genuinely the packages' own output, in both a text and a machine form, not
a re-derived summary. Seven more subcommands are views onto that same run:
`score`, `explain`, `slope`, `code`, `compare`, `trend`, `queue` — each
accepts the same dataset flags as `run` (plus `--claims`, the 837 directory)
and each of `--json` (the default; newline-delimited for a batch `score`),
`--table` (a plain-text column table) or `--csv` (with a leading
`# rulebook=... ` version comment). Every document any of them prints carries
`rulebook_version`, `weights_version`, `rule_pack_source`, `rule_pack_path`
and all three package versions. `worth-cli price` and `worth-cli db` are pure
forwarders to `worth-fees` and `worth-db`'s own command lines, argument for
argument, including their own `--help`.

`trend [CODE] [--policy-date YYYY-MM-DD] [--by payer]` is the over-time view
(decision 7, CONTRACT-SEEDS.md): each study code's payment adequacy ratio by
calendar month, one row per (code, period) — or per (code, payer, period)
with `--by payer` — reporting `n`, `ratio`, its 95% interval and whether the
point was suppressed (`n < 11` withholds the ratio, not the row). Pass
`--policy-date` to also compute a `pre`/`post` split at that date, carried on
each series' `pre`/`post` fields in `--json`; without it, a series still
reports its month-by-month points, just no `pre`/`post`. With no `CODE`,
every study code's series comes back. `worth-cli run --json` carries the same
month-by-month series under `trends` too (`run` itself takes no
`--policy-date`, so its own output never carries a `pre`/`post` split — use
`trend --policy-date` for that), and the text report adds a short "Over
time" section per class, but only when that class actually spans more than
one period — a single-period run's report carries no such section at all.

Standard library only in this package's own code — `argparse`, `json`, `csv`,
`dataclasses` — matching the two numeric packages it sits on top of.

Every subcommand above `packs` and `version` accepts repeatable `--pack
NAME_OR_PATH` and repeatable `--rulepack-dir DIR` (layer 2). A `--pack` value
that is an existing file is loaded directly as an external rule pack;
anything else is a name, resolved against `--rulepack-dir`, then
`WORTH_RULEPACK_DIR` (`os.pathsep`-separated), then the packaged rule packs —
first match wins, except a packaged pack is never silently shadowed by an
external one of the same file name. `--pack` is repeatable so a mixed extract
(CONTRACT-PACKS-MC.md: one directory with OR cases, clinic visits and
monitoring episodes side by side) can name one pack per class explicitly,
e.g. `--pack surgical-v2-pilot.json --pack visit-em-v1`; each pack is applied
to the class it declares, two packs naming the same class is refused, and a
pack naming a class the extract does not have is reported rather than an
error (unless the extract has only one class, where a mismatch is still
refused as before). `--setting` is likewise repeatable: a bare value
(`facility` or `non-facility`) applies to every class, and `CLASS=VALUE`
(e.g. `--setting visit=non-facility`) overrides one class only — both forms
may be combined in the same command. An external pack is capped at
`provisional` regardless of what it declares, so it can never produce a
`publishable` record; the text report's banner and every JSON document say
which source and, for an external pack, which path produced the run.
`worth-cli packs` lists what layer 2 can see right now, packaged first:

```bash
worth-cli run                          # the pipeline report, on the packaged synthetic fixture
worth-cli run --json                   # the same run, as JSON, plus the three package versions
worth-cli score                        # one adequacy document per encounter, newline-delimited JSON
worth-cli score ENC1 ENC2              # just those encounters
worth-cli explain ENC1 --table         # the full derivation for one encounter, as text
worth-cli slope 58662                  # the pooled (cross-payer) Method 0 slope for one code
worth-cli code 58662 --by payer --csv  # the population card, grouped by payer, as CSV
worth-cli compare                      # the cross-code compare view, grouped by class
worth-cli compare --domains            # collapsed to one row per service line (domain) per class
worth-cli trend                        # ratio by month, every study code
worth-cli trend 58662 --by payer --table   # one code, broken out by payer, as text
worth-cli trend --policy-date 2027-01-01 --json  # pre/post split at a policy date
worth-cli queue --site 1234567893      # the work queue, filtered to one site
worth-cli packs                        # every rule pack layer 2 can see, packaged first
worth-cli packs --rulepack-dir ./candidates --json
worth-cli run --pack ./candidates/surgical-v2-pilot.json  # score against an external candidate
worth-cli run --pack visit-em-v1 --pack episode-rpm-v1    # a mixed extract, two of its classes
worth-cli run --setting visit=non-facility --setting episode=non-facility
worth-cli price 99213 CA18 2026-03-14  # forwards to `worth-fees price`
worth-cli db migrate                   # forwards to `worth-db migrate`
worth-cli version                      # all four package versions
```

`WORTH_WITNESS_KEY`, if set in the environment, is read here (not by the
engine) and passed through as the HMAC key for every run's witness.

## `synth`

`worth-cli synth --scenario S --class C [--seed N] [--scale K] DIR` generates
one named scenario dataset from the catalog
(`worth_complexity.synthetic.scenarios.SCENARIOS`, CONTRACT-SEEDS.md's seed
suite) into `DIR`: the same generators `worth-cli run`'s packaged fixture
comes from, driven by a `Scenario`'s knobs instead of the fixture's own
baked-in ones. `--class` is one of `surgical`, `visit`, `episode`, `mixed`
(`mixed` composes the other three the same way `worth-cli run` reads a
combined extract). `--seed` defaults to the class's own fixed seed; `--scale`
(default 1) multiplies the study cohort only — the comparator cohort stays at
the generator's own normal count, the same convention `build_fixture` uses.
`worth-cli synth --list` prints the catalog (name, group, description) and
exits; every other flag is ignored when `--list` is given.

```bash
worth-cli synth --list                                       # the scenario catalog
worth-cli synth --scenario parity --class surgical ./out      # realized = curve(score) x multiple
worth-cli synth --scenario payer-friction --class mixed --seed 7 ./out
worth-cli synth --scenario broken/no-anchor --class visit ./out
```

On success it prints `DIR` followed by the `truth.json` written there —
what the scenario planted (a ratio band, a linkage rate, suppressed codes,
or, for the broken group, the exact exception class, message substring and
pipeline stage `worth-cli run ./out/clinical ./out/remittance` should fail
at). See `packages/worth-complexity/worth_complexity/synthetic/README.md`
for the full catalog, the knobs each scenario sets, and the byte-identity
guarantee the four packaged fixtures (`worth-cli run`'s defaults) still get
from the same generators.
