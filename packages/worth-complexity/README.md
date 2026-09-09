# worth-complexity

Layer A complexity scoring and the WORTH payment adequacy ratio.

Give it a partner's operative extract and their 835 remittance files. It returns,
per encounter, a Layer A complexity score with the arithmetic behind it, the
realized payment linked from the remittance, the complexity-matched expected
payment read off a cross-specialty reference curve, and the ratio of the two.

```python
from pathlib import Path
from worth_complexity import run

r = run(Path("clinical"), Path("remittance"))
r.cohort_ratio()  # Decimal('0.70...')
r.linkage.render()  # 'linkage rate 100.00% (126 linked, 0 unlinked, ...)'
```

Or from the command line, against the synthetic dataset that ships with the package:

```bash
just complexity-demo
```

## What it publishes

The unit of output is one record per code, per institution, per period, the
thing the methodology says accumulates:

> Nothing at the patient record-level accumulates. What accumulates is the
> computed output, a payment adequacy index per CPT code, per institution, per
> period. Each value carries what is needed to read it: the code, the
> institution, the time window, the number of encounters behind it, the
> complexity distribution it was drawn from, and a confidence interval.

No figure is reported across codes. Each code is valued separately, fails
separately and is petitioned separately, so a blended number names no reform
lever.

Two governance rules are enforced in the record rather than in a review step.
**Payers are blinded**, the identity never leaves the environment the data is
in, because "the payment figure goes into a complexity ratio, blinded by payer,
and never comes back out as a rate." And **cells below n = 11 are withheld**,
which is the partner's own suppression commitment.

## What it computes

Output is staged the way the methodology stages its own confidence.

### Defensible from the first validated extract

**The empirical reference distribution.** Layer A complexity per code, as a
distribution rather than a mean, median, quartiles, range, n. "A measurement of
what the work actually looks like across thousands of encounters." Method 2 has
nothing to compare a case against without it.

**Method 0, the slope diagnostic.** Within one code and one blinded payer, does
realized payment move as documented complexity rises? Reported as dollars per
complexity point with a 95% interval. An interval containing zero means the code
has not been shown to pay differently for harder work, which is the finding
rather than a null result. Needs no reference standard.

### Research stage

**The schedule curve.** What the Medicare Physician Fee Schedule pays for
measured complexity in specialties whose valuation is not in dispute. Every
comparator encounter is priced through `worth-fees` at one reference release,
in the institution's locality and setting, and a line is fitted through
(Layer A score, PFS amount) across the whole comparator cohort. From the
comparator cohort only, because a curve that included the codes under study
would define adequacy partly in terms of the thing being tested. No payer in
it. The methodology is explicit that this is the part that will take longer,
"a genuine research problem worth publishing", to be derived with clinical
advisors and biostatisticians rather than asserted, so everything resting on
it is reported below the two outputs above and labelled. It deliberately
avoids pairing each study procedure to a specific analog, pair selection is
contestable, and it is the same move the counter-argument uses.

**The payer multiplier.** The 835 is in the payer's contract dollars and the
curve is in Medicare dollars. Commercial contracts are written as a multiple
of Medicare, so the multiple is measured rather than assumed: per payer, the
median of realized payment over the PFS amount across its own comparator
encounters, each priced at the release in force on its service date. A payer
with fewer comparators than the suppression floor gets no multiplier and its
study encounters get no ratio. The multiplier is a contract level. It stays
with the partner, appears in the encounter trace so the institution can check
its own number, and never enters an index record.

**Method 3, the band-expected payment.** The payment adequacy ratio's
denominator is no longer a single fitted line. For each study encounter, the
comparator cohort scored within a few points of it — the study score ± 3,
widened symmetrically until the band holds at least the suppression floor
(n = 11) or has nowhere left to widen to — supplies a median PFS amount,
priced at the reference release and multiplied by the payer's own contract
level. A code whose local neighbourhood never reaches the floor, even at the
full 0-100 width, gets no Method 3 value at all rather than one drawn from a
band so wide it has stopped meaning "near this score". The band and its
bootstrap interval are reported (`Method3.band`, `Method3.interval`) beside
the number, not behind it. Method 2 (the single fitted line above) is still
computed and reported — it feeds the dollar spine's compression component —
but the published ratio is `realized ÷ Method 3 expected`.

**The payment adequacy ratio.** Realized payment on the primary procedure's
line, divided by the Method 3 band-expected payment. Both sides dollars, so
the result is dimensionless, and it carries a seeded bootstrap interval built
from the band's own interval (low expected pairs with high ratio). 1.00 means
paid consistently with equivalent measured complexity elsewhere in medicine;
0.70 means seventy cents on that dollar. Modifier 22 on the primary procedure
is recorded beside the score as the surgeon's own complexity claim, never fed
into it, and stripped before pricing.

**Method 1, documented-vs-submitted.** A separate question from complexity:
was the work the surgeon documented actually captured on the claim? Every
operative note is read for statements naming a separate procedure step (the
same section-scoped, negation-aware reading the marker rules use), and each
one is checked against the 837 claim linked to the encounter (falling back to
the OR log's own procedure panel when no claim has arrived yet). A candidate
code present on the claim closes the question with no flag. Otherwise:
`no_code` when the rule pack has no billable code for the step at all;
`missed` when a candidate exists, was not submitted, and nothing it bundles
into under NCCI was submitted either — real recoverable revenue; `mismatched`
when a candidate exists but NCCI already bundles it into another submitted
code — not recoverable, but worth a biller seeing so a modifier is not chased
for nothing. `missed` and `mismatched` flags are priced at the first
candidate code that prices, at the reference release, times the payer's
multiplier — "what equivalent work earns where the fee schedule pays for it";
`no_code` flags stay `priced = None`, since no reference exists to price them
against. No CPT descriptor text anywhere: the rule pack's phrase-to-code
patterns are our own regexes over our own phrase lists, keyed to numeric
codes only.

**The dollar spine and its signature.** Three methods price the same
encounter differently — Method 1's priced flags, Method 2's schedule curve,
Method 3's band — and the gap between them is diagnostic. The spine lays all
three on the encounter's own realized dollars; the decomposition splits
Method 3's shortfall into a compression component (how far the encounter sits
from its code's own median on the curve) and what is left over, center
mispricing. The signature reads a pattern off the three magnitudes — which
lever, coding/documentation, a gap in the code set, the code's structure, or
the fee schedule's rate, best explains why this encounter's dollars look the
way they do — and the same read, pooled across a code's cohort, becomes the
code card's signature mix and dominant lever.

**The witness.** Every run's outputs, per-encounter and run-level, are sealed
with a sha256 digest over `{input_hash, rulebook_version, weights_version,
package_versions, outputs}` as canonical JSON, so a referee re-running the
identical files against the identical rule pack and package gets the
identical hex string back by hand. `input_hash` is itself a sha256 over the
sorted (filename, sha256) pairs of every clinical, remittance and claims file
the run read. Set `WORTH_WITNESS_KEY` and the same bytes are also HMAC-SHA256
signed (`scheme: "hmac-sha256"`); unset, the scheme is plain `sha256` and
there is no signature.

**Over time.** `pipeline.run(policy_date=...)` and `population.periods()`
turn one point estimate into a series: for every study code, the ratio month
by month (or quarter by quarter), each point suppressed independently below
the floor (`n < 11` withholds the ratio, not the point itself, so a thin
month still shows up as "withheld" rather than vanishing). Give `run()` a
`policy_date` and each code's cohort is also split into a `pre` and `post`
summary — distribution, ratio and signature mix on each side of the line —
and the series is broken out by payer besides. `Run.trends` and
`ClassRun.trends` flatten and hold the per-class output; `worth-cli trend
[CODE] [--policy-date YYYY-MM-DD] [--by payer]` is the view onto it, and
`worth-cli run --json` carries it under `trends`. A run given no
`policy_date` still gets the month-by-month series; it just carries no
`pre`/`post` split.

Limitations are asserted in the test suite rather than left to be discovered.
A code whose local neighbourhood never reaches the suppression floor, even at
the band's full width, gets no Method 3 value and is excluded rather than
given one drawn from too wide a band; encounters excluded that way, or
because their payer has too few comparators for a multiplier, are named
rather than silently dropped (`Run.unbanded`, `Run.unmultiplied`). And a
comparator encounter the fee schedule refuses to price stops the run rather
than being skipped, because a curve fitted on whichever comparators happened
to price is a curve fitted on a cohort nobody chose.

## Layer A

Layer A is the open, published, auditable part of the standard: a fixed formula
over facts that each trace to a structured field or a rule anyone can read. No
trained or generative model sits in the scoring path, so a skeptic cannot say
the model made it up, and a health system's AI governance committee has nothing
to evaluate.

It ships as the `surgical` rule pack, not a gynaecology-specific one, because it
is the instrument for the whole surgical encounter class: the same markers and
weights score the urology, general surgery and orthopaedics comparator cohort
that they score the benign-gynaecologic study cohort with, so a name that said
"gyn" would misdescribe most of what it scores.

It has two lanes, and it needs both. Discrete fields carry operative time, ASA
class, team composition, blood loss and comorbidity count. They carry nothing at
all of the markers the methodology also names, anatomic extent, adhesion
severity, intraoperative events, because no hospital system has a field for
whether the ureter had to be dissected free. Those are read out of the operative
note by `notes.py`: section parsing, pattern matching and negation, and nothing
else.

Three refusals are what separate that from a keyword search, and each is a
sentence a `grep` would score wrongly:

| The note says | A search counts | Layer A counts |
|---|---|---|
| `INDICATION: history of appendectomy` | appendix involved | nothing, history is not work performed today |
| `no evidence of diaphragmatic involvement` | diaphragm involved | nothing, the finding was excluded |
| `no adhesions in the RUQ. Dense pelvic adhesions were encountered` | one negated mention | dense adhesions, negation stops at the full stop |

Where both lanes offer the same marker, the structured field wins. A note
usually restates the operative time, and preferring the narrative would make the
score a measure of how thoroughly a surgeon writes, the busiest services
document least, and would score lowest for it. The displaced marker is reported,
not discarded.

What Layer A does **not** attempt is the severity a rule cannot reach: a case
whose note never uses the word. The methodology reserves that for the generative
enrichment lane, which is reported beside the score and never rewrites it.

That promise is kept structurally rather than by discipline. Every marker carries
a `provenance` tag, and the scorer takes a provenance filter:

| filter | admits | is |
|---|---|---|
| `layer-a` | structured + rule-based note reading | the standard, and the default |
| `structured` | structured fields only | the subset a partner with no note dataset can produce |
|, | + `ml` | enrichment; reported beside Layer A, never rewriting it |
|, | + `generative` | enrichment; same |

Markers from the ML and generative lanes are excluded from a Layer A score
because the filter never admits them, not because a caller remembered to leave
them out. One consequence worth naming: the same pipeline output, scored twice,
measures how much the ML lane would move the number, which settles where the
classifier belongs with a measurement instead of an argument.

### Missing-data policy

A marker the rule pack lists but the extract did not produce for one
encounter no longer fails that encounter. It scores at zero and is named:
`ScoredEncounter.missing` carries the marker id, `ScoredEncounter.marker_rows`
carries the same fact with its reason (`"absent"`, or `"rejected: <why>"` for
a structured value a plausibility guard threw out — an operative time over
24 hours or negative, an ASA class outside 1–6, a blood loss over 5 000 mL,
any `*_minutes` marker negative, an episode's device-reading volume over
5 000), and the derivation trace shows the same row scored `0.0000`. Never
imputed upward: a missing marker is evidence of nothing, and the zero is
deliberately the worst case a caller could otherwise have hidden by silently
dropping the encounter.

`MissingMarkerError` still exists, narrowed to the one case where scoring
zero would be dishonest rather than conservative: every *structured* marker
the pack requires is absent or rejected, so nothing about the encounter's
complexity was actually measured. `pipeline.run` catches it per encounter —
one such case never fails the whole run — and lists the encounter in
`Run.unscored` with the reason, the same place a class with no rule pack
resolved for it already reports.

Missingness itself is published, not just tolerated: `population.
instrument_health` reports it per marker per site, driven directly by each
encounter's own missing-marker flags (`EncounterRow.missing_markers`) rather
than inferred from which markers happened to show up — a distinction that
matters once two sites disagree about which fields they populate.

### Where rule packs come from

A rule pack can come from two places, and the two carry different trust.
Published packs ship inside this package, under `rulepacks/`, and are the
only source a `ratified` pack may ever come from — `rulepack.load` enforces
that at load, and `IndexRecord.publishable` checks it again, independently,
on every record. That double lock is deliberate: a `ratified` status is a
claim a file makes about itself, and the second check exists for the record,
not the file, so a bug in the first one is not the only thing standing
between an unreviewed pack and a published value.

The loader also resolves packs from external directories — `--rulepack-dir`
on the CLI, or `WORTH_RULEPACK_DIR` (`os.pathsep`-separated, read fresh on
every call) — so a deployment or a partner can pilot a candidate pack
without a package release. An external pack is capped at `provisional` no
matter what it declares, and a `ratified` claim in the file is recorded and
overridden rather than trusted; that cap means an external pack can never
produce a `publishable` record, only ever a run someone can look at.
`rulepack.available()` lists everything either layer can see, packaged
first, and every run's report and JSON document name exactly where its pack
came from, packaged or the file path, so nobody has to ask.

### Three encounter classes

An extract is `surgical`, `visit` or `episode`, and `extracts.read_extract`
tells the three apart by which anchor file a directory holds — `or_log.txt`,
`visit.txt`, `episode.txt` — never by a flag a caller could get wrong. Each
class has exactly one packaged rule pack (`surgical-v1`, `visit-em-v1`,
`episode-rpm-v1`), and `pipeline.run` loads the right one automatically when
`pack_name` is not given; a pack whose `encounter_class` does not match the
extract it is handed is refused, naming both. Scores are never comparable
across classes, only within one.

**Mixed extracts.** A real partner delivery is one extract with OR cases,
clinic visits and monitoring episodes side by side, sharing one 835/837 feed
— `read_extract` no longer refuses a directory naming more than one anchor
file: it reads each class's own reader over the shared directory and wraps
the result in a `CombinedExtract` (`encounter_class == "mixed"`, `classes`
naming every class present; `notes`/`tables`/`file_hashes` are the union,
deduplicated, since a shared lookup table like `patient_lds.txt` is read
once by every class whose own reader names it; `notes_for`/`facts`/`markers`
dispatch on the encounter's own class). `pipeline.run` links remittance and
claims once over every encounter regardless of class, then runs one full
pass — pack resolution, pricing, the comparator curve, payer multipliers,
Method 0, Method 1, Method 3, signatures, cards, compare rows, the queue —
per class present, in the fixed order surgical, visit, episode. What one
pass produces is a `ClassRun`; `Run` becomes the shared linkage/claims/
witness context plus one `ClassRun` per class, with `adequacies`/`records`/
`cards`/`compare`/`queue`/`observations` as properties that flatten across
every class (so an existing single-class caller, and Meridian's serializer,
see no difference), and `pack`/`rulebook_version`/`weights_version`/
`setting`/`schedule_curve`/`priced`/`multipliers` and friends still read
directly as the one class's value when exactly one class is present, raising
`WorthComplexityError` (naming `run.classes`) only on a genuinely mixed run.
Method 3 bands, Method 0 slopes and the schedule curve are always fitted
within one class's own comparator cohort — a mixed run never lets one
class's comparators price another's study encounters. `pack_name`/
`pack_path` may be a sequence, one pack per class it declares (two packs
naming the same class is refused; a pack naming a class the extract does not
have is recorded in `Run.unused_packs` rather than erroring, unless the
extract has exactly one class, where a mismatch is refused exactly as
before); a class present in the extract with no pack resolved for it lists
its encounters in `Run.unscored` instead of being silently dropped.
`population.domain_rows(class_run)` collapses one class's cards to one row
per `service_line` (domain), reusing the same per-encounter computations
`code_card` runs per code; `worth-cli compare --domains` prints those.
`tools/make_mixed_dataset.py DIR` composes the three single-class
generators' own output into `fixtures/mixed-synthetic/` (`just build-dataset
mixed`) without regenerating any of the three single-class fixtures.

The surgical class's Method 1 is the note-phrase `procedures` cross-check
this README describes above. The visit and episode classes have no
separate-procedure statements to look for; their Method 1 is a
`work_rules` block instead — threshold rules over structured facts (e.g.
"documented time crossed 40 minutes"), evaluated by the same `method1`
module against the class extractor's flat `facts()` mapping, and producing
the same `Method1Flag`. A work rule may also declare `replaces`: the billed
code its candidate would supersede, priced as `(PFS(candidate) −
PFS(billed)) × multiplier`, floored at 0, rather than the candidate's amount
outright.

Office visits price in the **non-facility** setting, and OR cases in
**facility** — `pipeline.run`'s `setting` argument now defaults to `None`,
resolved per class from `CLASS_DEFAULT_SETTING` (an explicit value always
wins), the same way `pack_name` resolves from `CLASS_DEFAULT_PACKS`.

**The visit class** (`visit-em-v1`, `visits.py`) scores an office E/M
encounter: `Encounter.primary_cpt` is the billed E/M level, and six markers
carry the weight — `total_documented_minutes`, `problems_addressed`,
`data_reviewed_ordered`, `prescription_management` and `coordination_minutes`
read straight off structured fields, plus `patient_context` (weight 0.05),
which is one marker for two different facts: a pregnancy-risk tier read off
the problem list when the patient carries a `PREGNANCY*` flag, or, when they
do not, a shared decision-making note rule — the same section-scoped,
negation-aware reading `notes.py` does for the surgical pack, scoped to the
COUNSELING and PLAN sections. Exactly one of the two is ever emitted for a
given encounter, so lane precedence never has to choose between them. Its six
`work_rules` cover time-based E/M leveling (with `replaces`), a
prolonged-service threshold, three tiers of digital E/M, chronic care
management (and its ineligible branch, a `no_code` flag), lab-review time
with no billing vehicle, and a `mismatched` flag for E/M levels billed on a
pregnancy-flagged encounter — a code family the methodology's E/M valuation
basis was never designed to price prenatal content against.

**The episode class** (`episode-rpm-v1`, `episodes.py`) scores a 30-day
remote-physiologic-monitoring episode: `Encounter.primary_cpt` is the
sequence-1 billed code (99453 for the study line), and six markers carry the
weight, every one of them structured — this class carries no note dataset at
all, and the pack's own `scope_note` says why: "from device and alert logs
alone", so nothing here is gated on a pack the way the surgical and visit
narrative markers are. `monitoring_intensity` (readings ÷ window days) and
`exception_burden` (Σ alert severity) read the device stream and its
exceptions; `clinician_interventions` and `oversight_minutes` read what a
clinician actually did about them — `oversight_minutes` deliberately carries
the smallest weight (0.10) of the six, so the index does not just re-derive
the RPM codes' own time thresholds; `patient_risk_tier` is the same
problem-list-tier pattern the visit pack's `patient_context` uses, and
`outcome_delivered` is a composite of 30-day control, no ED visit (an averted
one counts as clean) and no readmission. Its five `work_rules` (four
conceptual, since the two RPM treatment-management increments are separate
rules) cover the 16-day device-supply threshold, the first and second
20-minute treatment-management increments (the second gated on the first
having actually been billed, `billed_any_of`), a `mismatched` flag for
clinician-handled exceptions that never reach the 20-minute floor either
increment bills against, and a `no_code` flag for exceptions the escalation
protocol resolved autonomously — autonomous monitoring oversight has no CPT
code anywhere in this fee schedule.

## Ground rules

Inherited from the repository, and load-bearing here:

- **Standard library only.** No numpy, no pandas. A fit anyone can redo in a
  spreadsheet is worth more than a fast one, and results must be bit-identical
  across architectures.
- **Arithmetic in a pinned decimal context.** `money.py` is a deliberate copy of
  `worth_fees.money`; the two are guarded against drift by a test, not merged
  into a shared core, so each package stays separately auditable.
- **No network, no clock, no cloud SDK.** The engine reads a directory and
  writes a result. That is what makes "run our code inside your environment" a
  deployment target rather than a rewrite.
- **The complexity score is ordinal and is never a denominator.**
  `ComplexityScore.__rtruediv__` raises `MethodologyViolation` and quotes the
  methodology, so whoever hits it in 2028 understands why rather than routing
  around it.

## Status

**Nothing computed by this package is publishable yet.** Two reasons, both
reported by the CLI on every run:

1. The rule pack is marked `provisional`, and `IndexRecord.publishable` is false
   for every record while it stays that way. Its weights and anchors are an
   engineering placeholder. The methodology requires them to be set by clinical
   advisory input and statistical analysis of which markers actually predict
   resource intensity, not by assertion, and not by an engineer.
2. The dataset that ships with the package is synthetic. It is built to exercise
   the pipeline, and the adequacy ratio it produces is a property of the
   generator, not evidence about anything. See
   `tools/make_synthetic_dataset.py`, which states its assumptions in full.

## Licence

Apache-2.0. CPT® is a registered trademark of the American Medical Association;
this package contains no CPT descriptors.
