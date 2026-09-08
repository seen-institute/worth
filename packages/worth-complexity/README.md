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

**The payment adequacy ratio.** Realized payment on the primary procedure's
line, divided by the payer's multiplier times the schedule curve at the
encounter's score. Both sides dollars, so the result is dimensionless, and it
carries a seeded bootstrap interval. 1.00 means paid consistently with
equivalent measured complexity elsewhere in medicine; 0.70 means seventy cents
on that dollar. Modifier 22 on the primary procedure is recorded beside the
score as the surgeon's own complexity claim, never fed into it, and stripped
before pricing.

Two limitations are asserted in the test suite rather than left to be discovered.
Encounters scoring outside the fitted range are excluded rather than
extrapolated, which is right, but the excluded ones are not random, and a code
that loses its hardest cases reads *toward* adequacy, the direction that hides a
finding. And a comparator encounter the fee schedule refuses to price stops the
run rather than being skipped, because a curve fitted on whichever comparators
happened to price is a curve fitted on a cohort nobody chose.

## Layer A

Layer A is the open, published, auditable part of the standard: a fixed formula
over facts that each trace to a structured field or a rule anyone can read. No
trained or generative model sits in the scoring path, so a skeptic cannot say
the model made it up, and a health system's AI governance committee has nothing
to evaluate.

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
