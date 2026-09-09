# worth_complexity.synthetic

The generators behind every fixture `worth-complexity` and `worth-cli` ship,
and behind the scenario seed suite `worth-cli synth` generates on demand
(CONTRACT-SEEDS.md, "worth track S1"). Nothing here reads real data; every
number and every name is fabricated, deterministically, from a seed.

```
surgical.py   the OR-log ("mssm-synthetic") generator
visit.py      the office-visit ("visit-synthetic") generator
episode.py    the 30-day RPM episode ("episode-synthetic") generator
mixed.py      composes the three into one extract ("mixed-synthetic")
edi.py        the ANSI X12 835/837 writers surgical.py and visit.py share
knobs.py      small generation-time knobs shared across classes (Method 1 omission,
              policy-change's month-stratified study dates)
sites.py      the multi-site scenario's three facility NPIs and per-site habits
pricing.py    the generic study-payment engine every class's payment_rule uses,
              plus the policy-change fee-schedule step and vintage clamp
friction.py   payer friction (denials, downcodes, reversals) applied post-generation
dirt.py       named corruptions (blank fields, duplicate ids, orphan claims, ...)
scenarios.py  the Scenario catalog: one row per scenario, the knobs it sets
planted.py    derives truth.json's `planted` dict from a scenario and the files on disk
truth.py      the Truth dataclass, truth.json read/write, and its validator
```

## Two entry points per class

Every class module (`surgical`, `visit`, `episode`, `mixed`) exposes two
functions:

* **`build_fixture(root, scale=1, *, seed=<class default>)`** reproduces one
  of the four packaged fixtures byte-for-byte at `scale=1`. This is the
  original, pre-scenario generator's `main()` body, parameterized — its RNG
  call order is untouched, which is the entire byte-identity guarantee (see
  below). Nothing in this package calls it with any scenario knob; it exists
  only to keep regenerating what already shipped.
* **`build(out_dir, *, scenario, seed=None, scale=1)`** is the scenario-aware
  entry point `worth-cli synth` calls. It targets about
  `scenarios.SCENARIO_STUDY_PER_CODE` (24) study encounters per code — not
  `build_fixture`'s own cohort constants — and the generator's normal,
  unscaled comparator counts, except when the scenario's `thin_cohorts` knob
  overrides both. `scale` multiplies the study cohort only.

`build` and `build_fixture` never share a call path beyond the low-level
per-record builders (`build_encounters`, `build_records`, `build_episodes`):
a scenario knob can never leak into a packaged fixture's regeneration.

## The scenario catalog

`scenarios.SCENARIOS` is the catalog CONTRACT-SEEDS.md's "Scenario catalog
and planted truth" table names, one `Scenario` (a frozen dataclass of knobs)
per row. `worth-cli synth --list` prints it.

| scenario | group | what is planted |
|---|---|---|
| `baseline` | core | flat study payment at 70% of the curve at the code's mean score — today's packaged fixtures' own assumption |
| `parity` | core | `realized = curve(own score) x multiple`, small noise — nothing planted to find |
| `compression` | core | every study case paid `curve(cohort median) x multiple`, regardless of its own score |
| `center-mispriced` | core | `realized = 0.7 x curve(own score) x multiple` — tracks complexity, uniformly discounted |
| `overpaid` | core | `realized = 1.3 x curve(median) x multiple` — baseline's mirror image, above 1.0 |
| `method1-heavy` | core | 60% of documented secondary steps omitted from the 837; every study visit's time clears the next E/M level |
| `payer-friction` | core | one payer denies 30% of primary lines, one downcodes 20%, 3 reversal/correction pairs, one payer never pays modifier 22 |
| `thin-cohorts` | core | 8 study cases per code, one comparator code down to 5, one payer capped at 4 comparator encounters |
| `policy-change` | core | 24 months (2026-01 through 2027-12); MATERNITY bills a per-pregnancy antepartum bundle before 2027-01-01 and per-visit 99214 after; surgical/episode get a 4% fee-schedule step at the date |
| `multi-site` | core | three facility NPIs; site B's notes omit adhesion/anatomic-extent/shared-decision language 60% of the time, site C leaves a structured field blank (surgical's EBL, episode's time log) |
| `dirty` | core | blank operative minutes, one absurd case, duplicate account ids, dropped/orphaned remittance claims, notes missing section headers, header-case and date-format variance — the run completes, at real nonzero linkage loss and missingness |
| `broken/unpriceable-comparator` | broken | a comparator billed a code the fee fixture does not carry — fails at `price` |
| `broken/malformed-table` | broken | the anchor table is missing a required column — fails at `extract` |
| `broken/corrupt-835` | broken | one 835 claim's `AMT*B6` line is removed — fails at `remittance` |
| `broken/no-anchor` | broken | no anchor file present at all — fails at `extract` |

`mixed` runs every scenario through all three per-class `build()` entry
points at the same seed and scale, then merges the three directories exactly
as `mixed.build_fixture` merges the packaged fixtures (`mixed.py`'s own
module docstring has the merge rules). A broken scenario is therefore broken
independently by all three classes, so the merged directory's planted
failure does not depend on which class's anchor or file a reader reaches
first.

### `policy-change`

Every class's service window widens from one year (CY2026) to 24 months,
2026-01 through 2027-12, straddling `Scenario.policy_date` (2027-01-01,
written into `truth.json` as `planted.policy_date`). Comparator encounters
stay inside the original CY2026 window in every class — only the study
cohort needs to span the policy date, and `worth_complexity.adequacy.
price_comparators` prices each comparator at the real, unclamped vintage in
force on its own service date, which has no CY2027 fallback (see below).

* **Visit class.** MATERNITY changes shape across the date; MENOPAUSE does
  not (`truth.json`'s `planted.distribution_shift` is `false`). Before the
  date, a pregnancy's 6 to 10 prenatal visits are never billed individually:
  they are grouped (`visit.build_pregnancy`) into one antepartum-care claim
  — CPT `59426` (7 or more visits) or `59425` (4 to 6) — submitted once,
  after the pregnancy's last covered visit, on an account every one of that
  pregnancy's visit rows shares. Each visit still extracts and scores as its
  own encounter (`visit_proc.txt` carries the bundle CPT on every one of
  them), but only one `VisitRecord` — a billing-only record `write_clinical`
  never sees — actually reaches `write_remittance`/`write_claims`.
  `worth_complexity.linkage.link` has a bundle carve-out for this
  (`BUNDLE_CODES`): several encounters sharing one account is ordinarily
  "duplicate account" (both left unlinked), but a claim whose lines carry a
  global/antepartum code is a legitimate bundle instead — every encounter it
  covers links, each with `LinkedEncounter.bundle_size` set and `realized`
  the claim's allowed amount divided evenly across them. **Assumption:**
  that even split is this fixture's own choice, not a CMS or payer rule — a
  real antepartum bundle has no per-visit allocation at all; splitting it
  evenly is what lets each visit's own complexity score still compare
  against its own share of what the pregnancy was paid. After the policy
  date, a pregnancy's visits bill `99214` individually, same as MENOPAUSE
  always has.
* **Surgical / episode classes.** Payer multiples are unchanged; every
  priced base amount (comparator and study alike) is multiplied by
  `pricing.FEE_SCHEDULE_STEP` (1.04) once the encounter's own service date
  reaches the policy date — a flat, deliberately-planted step, not a real
  CMS conversion-factor change.
* **No CY2027 CMS archive is pinned** (`worth_fees.sources.PINNED_VINTAGES`
  stops at CY2026 Q4), so `vintage_for_date` raises for a 2027 service date.
  Every scenario-aware pricing helper calls `pricing.
  vintage_for_date_clamped` instead, which falls back to the 2026 Q4 vintage
  for any date past it — a generator-side clamp only; `worth_fees.sources.
  vintage_for_date` itself is untouched and still raises for a real caller.
  A caller running the real pipeline over a `policy-change` fixture
  (`pipeline.run(..., policy_date=...)`) needs to pass `reference=(2026,
  4)` explicitly too — `pipeline.run`'s own default reference vintage
  (`vintage_for_date(max(service_date))`) is not clamped.
* `truth.json` plants `periods: 2` and, per study line, a `ratio` bands pair
  (`study_pre`/`study_post` for surgical and episode — the pre band times
  the fee step; `maternity_pre`/`maternity_post` for visit — read back from
  a real `pipeline.run` over the just-generated directory, since a bundle
  visit's realized share is not a fixed multiple of anything this package
  already has a closed-form band for).

### `multi-site`

Three facility NPIs (`sites.SITE_NPIS`): `1234567893` (site A, the existing
single-site NPI every non-multi-site fixture already writes — full
documentation), and two new ones, B and C, each with one real documentation
habit:

* **Site B** (60% of the time, `sites.SITE_B_NOTE_OMISSION_RATE`): the note
  a rule-based marker would read is not written at all — the surgical
  operative note (`adhesion_severity`, `anatomic_extent`, and incidentally
  `intraoperative_events`, which reads the same note type), the visit note
  (the shared decision-making branch of `patient_context`, decision 2 of
  CONTRACT-PACKS.md). Withholding the whole note, not editing its language,
  is what actually registers as *missing* (decision 6): a note that merely
  never uses the tracked phrases still scores a real, present zero.
* **Site C**: the surgical class's `or_log.txt` `ebl_ml` is blank 95% of
  the time (`sites.SITE_C_EBL_BLANK_RATE`) — both the structured field and,
  since the rule pack's `estimated_blood_loss_ml` marker has a narrative
  fallback of its own (the same operative note states an EBL in prose), the
  note's own ESTIMATED BLOOD LOSS line, or the marker would still resolve
  from the narrative lane even with the structured field blank. The episode
  class's `time_log.txt` is entirely absent for 40% of episodes
  (`sites.SITE_C_TIME_LOG_DROP_RATE`) — read at the whole-log granularity,
  since a partially-thinned log still sums to a real, present, merely
  smaller `oversight_minutes` rather than a missing one.
* The episode class has no note dataset, so site B's habit has nothing to
  touch there; an episode still gets assigned one of the three site NPIs
  (`sites.assign_sites`), but only site C's habit changes what it reads
  back as.

`truth.json` plants `sites: 3` and `missingness_by_site`: `{facility NPI:
{marker_id: [low, high]}}`, an inclusive band around each marker's observed
missing fraction at that site, scored fresh from the generated files
(`planted.missingness_by_site` — the same per-marker, per-encounter
`ScoredEncounter.missing` reading `population.instrument_health` uses,
broken out by `Encounter.facility_npi` instead of pooled across the whole
class).

## The knobs (`scenarios.Scenario`)

A `Scenario` is data, not code: every class's `build()` reads the same knob
names and applies whichever ones its own generation has a use for (`multi_
site` still assigns every episode one of the three facility NPIs even
though the episode class has no note dataset for site B's habit to touch —
only site C's structured-field habit changes what an episode reads back
as).

* `payment_rule` / `payment_share` — one of `flat-at-median-share`,
  `on-curve`, `at-median`, `curve-share`, `overpaid-at-median`
  (`pricing.price_study`), and the share/multiple it applies.
* `m1_omission_rate` — fraction of documented secondary steps dropped from
  the claim (`knobs.omit_secondary_codes`).
* `m1_underlevel` — visit class only: guarantee every study visit's
  documented time clears the next E/M level while still billing the flat
  code.
* `denial_share` / `downcode_share` / `reversal_pairs` /
  `no_modifier22_payer` — payer friction (`friction.apply`), applied to the
  835 side only; the 837 keeps what was actually submitted.
* `thin_cohorts` — below-suppression-floor cohort sizes (a class-specific
  `_thin_cohorts` helper).
* `policy_date` — 24-month service window, the maternity antepartum-bundle
  billing shift and the surgical/episode fee-schedule step; see
  `policy-change` above.
* `multi_site` — three facility NPIs and their per-site documentation
  habits; see `multi-site` above.
* `dirt` — `(function name in dirt.py, kwargs)` pairs applied in order after
  generation (`dirt.apply`).
* `broken` — one of the four broken variant names (`dirt.apply_broken`);
  mutually exclusive with every knob above except `notes`.

## `truth.json`

Every `build()` call writes `truth.json` beside `clinical/`, `remittance/`
and `claims/` — what the scenario planted, in the shape CONTRACT-SEEDS.md's
catalog table describes:

```json
{"scenario": "parity", "encounter_class": "surgical", "seed": 1,
 "generator_version": "0.1.0",
 "planted": {"linkage_rate": [1.0, 1.0], "method0_verdict": "rising",
             "missingness_max": 0.0, "ratio": {"study": [0.985, 1.015]},
             "suppressed_codes": ["58660"], "suppressed_share": 0.0714},
 "purpose": "Every study encounter is paid exactly what the comparator curve pays at its own score. The null case: ratios sit at 1.00 against the 1.0 line, Method 0 rises, and the signature has nothing to explain.",
 "notes": ["..."]}
```

`planted.py` derives every field: `linkage_rate` and `suppressed_codes` are
read back from the generated files on disk (which accounts also appear as
some 835's `CLP01`, how many lines each code billed against the n=11
suppression floor) — computed *after* every knob (payment rule, friction,
dirt, broken) has already run, the same reason a real linkage report would
read the same numbers, rather than tracked knob-by-knob through generation.
`suppressed_share` (the fraction of billed codes at or under that same
floor) is derived from the same read-back, for every scenario, not only
`thin-cohorts`. `ratio` and `method0_verdict` come from the caller's own
knowledge of what it planted (`pricing.ratio_band`). `missingness_max` is
scored fresh with the real scorer only when the scenario carries `dirt`.
`flags` (`method1-heavy` only) is `knobs.omit_secondary_codes`'s own
`missed` count plus the study encounter count `n`; `mismatched` and
`no_code` are always `0` here, since this generator only ever omits a
documented step outright, never bundles it into another submitted code. A
broken scenario's `planted` is just `{"expected_failure": {"stage",
"error", "message_contains"}}` — nothing else is worth checking, since the
run never reaches a stage that would produce it.

`purpose` is a one-or-two-sentence "why this dataset is useful to test
with" line: `truth.render_purpose` renders the scenario's own
`scenarios.Scenario.purpose` template against this same `planted` dict,
substituting `{placeholder}`s (a ratio band's own midpoint, a percentage,
a count, `expected_failure`'s `stage`/`error`, `policy_date` spelled out)
and dropping a `[bracketed clause]` whole, rather than leaving an unfilled
placeholder in it, when the value it needs was not planted for this seed
(`compression`'s `{ratio_p90}`, which nothing in the catalog plants today,
is the one case that currently exercises this) — the rendered sentence
reads well either way. Every `build()` call renders and writes its own
`purpose` at generation time, right beside `planted` itself.

`truth.validate(truth)` is a small JSON-schema-like checker (`truth.py`): it
requires the top-level fields — including a non-empty `purpose` with no
leftover `{`/`}` from an unrendered placeholder — and checks that whichever
`planted` keys are present have the right shape (a `[low, high]` band, a
non-negative count or a `">=N"` string, a fraction in `[0, 1]`) — it never
requires a `planted` key that a particular scenario has no reason to plant.

## Byte-identity

The four packaged fixtures (`mssm-synthetic`, `visit-synthetic`,
`episode-synthetic`, `mixed-synthetic`, under
`packages/worth-complexity/worth_complexity/fixtures/`) are the `baseline`
scenario at their historical scale, and `tests/test_synthetic_baseline.py`
asserts each one's `build_fixture` call reproduces every file under
`clinical/`, `remittance/` and `claims/` sha256-identical to what is
committed. That guarantee depends on one discipline: **never reorder an RNG
call inside a `build_fixture`-reachable function.** Adding a knob, a
scenario-only helper, or a new field is safe as long as `build_fixture`'s own
call graph draws from `random.Random` in exactly the sequence it always has;
`build`'s scenario path is free to draw differently because it is a
completely separate call graph from the same low-level builders.

`tests/test_synthetic_scenarios.py` covers the scenario side: every
`(scenario, class)` pair in the catalog generates at scale 1, is readable
(`extracts.read_extract`, or fails exactly as `truth.json` plants for the
broken group), and its `truth.json` validates.
