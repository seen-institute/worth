# Mount Sinai synthetic dataset

**Fabricated data. No patient, encounter, provider, payer contract or dollar
amount here corresponds to anything real.** It exists so the pipeline can be
built and tested before a partner extract arrives.

Regenerate it with `just build-dataset`. It is deterministic: same seed, same
bytes. The generator and its assumptions are in
[`tools/make_synthetic_dataset.py`](../../../tools/make_synthetic_dataset.py).

## What is here

396 surgeries at one hospital over calendar 2026, in the two shapes a partner
actually delivers.

### `clinical/`, five pipe-delimited files

What an Epic Clarity analyst produces from the OR module. Delimited text is the
physical encoding, not the contract: the same rows as Parquet, or read straight
from a warehouse, would land in the same records and only `cases.py` would
change.

| file | grain | carries |
|---|---|---|
| `or_log.txt` | one row per surgical case | OpTime timestamps, ASA class, blood loss, patient class, the billing account number |
| `or_log_proc.txt` | one row per CPT on the case | the procedure panel and its modifiers |
| `or_staff.txt` | one row per person in the room | role and specialty, where team composition comes from |
| `encounter_dx.txt` | one row per diagnosis | ICD-10, sequence, present-on-admission |
| `patient_lds.txt` | one row per patient | Limited Data Set: pseudonymous id, birth month, ZIP, county |

### `remittance/`. ANSI X12 835 files

What the payer sends back. There is no friendlier form of this: the 835 is a
mandated EDI transaction and arrives as a segment stream. Each file is one
payer's remittance for one month.

The join to the clinical side is `or_log.billing_account_id` = `CLP01`.

### `claims/`. ANSI X12 837P professional claims

The other half of the same money conversation. What was billed, not what was
paid: one file per payer per month, one CLM per CLP, submitted a few days
after the last surgery in the batch and always before that batch's 835 pays.
It exists for two reasons the methodology needs. Method 1 cross-checks
documented operative steps against the code list actually submitted on the
claim, not just what the payer ended up allowing. And payer friction --
downcoding, bundling, outright denial -- is only visible by comparing what
was submitted (837) against what was allowed, denied or downcoded (835); the
835 alone shows the outcome, not the gap.

The join is `CLM01` = `CLP01` = `or_log.billing_account_id`. Every service
line, CPT, modifier and charge on a claim is the same value the matching 835
carries; nothing about the money differs between the two, only which side of
the transaction is speaking.

**The pipeline reads this directory.** `worth_complexity.claims` parses every
837P here, `pipeline.run` links each encounter to its claim by
`billing_account_id`, and Method 1's documented-vs-submitted cross-check
reads `claim.codes` as "what was actually submitted" wherever a claim is
linked, falling back to the OR log's own procedure panel only when it is not
— a partner extract that has not started delivering the 837 side yet still
gets a complete Method 1 pass, just against the OR log's own coding rather
than the claim's. `run(..., claims_dir=...)` defaults to this directory
(`extract_dir.parent / "claims"`) when it exists.

## The two cohorts

Both are in the same files, distinguished by the `service` column, because one
hospital produces one extract.

**Study, 30 surgeries, 5 benign-GYN codes.** `58558`, `58563`, `58570`,
`58661`, `58662`. These are the codes under examination. `58662` deliberately
spans the widest complexity range of anything in the dataset: it is the code the
methodology's worked example uses, and the point is that one code absorbs both a
90-minute ablation and a five-hour transmural excision.

**Comparator, 96 surgeries, 8 codes across general surgery, orthopaedics and
urology.** Not padding. The payment adequacy ratio is defined against what a
payer already pays for equivalent measured complexity *elsewhere in medicine*,
and that curve has to be fitted from somewhere. Without this cohort there is no
expected payment and therefore no ratio.

Both cohorts are scored with the same `surgical` Layer A rule pack: it is the
instrument for the whole surgical encounter class, and it has to score this
urology/general-surgery/orthopaedics comparator cohort with the identical
markers and weights it scores the benign-GYN study cohort with, which is why
the pack is not named for gynaecology alone.

## What the generator puts in, and what that means

Three assumptions are encoded deliberately:

1. Every payer pays a **fixed multiple of the Medicare Physician Fee Schedule**.
   A comparator encounter's allowed amount is that multiple times the real PFS
   amount for its code, in Medicare locality NY01 (Manhattan), facility setting,
   at the CMS release in force on the service date. Commercial contracts really
   are written this way.
2. The fee schedule's own complexity relation is whatever a straight line through
   the comparator cohort's (Layer A score, PFS amount) pairs says it is, fitted
   at the reference release (RVU26D). The generator fits it with the pipeline's
   own least squares and asserts no slope of its own.
3. The five study codes are paid **70%** of what that line pays at the code's
   mean score, times the payer's multiple, and each pays one flat amount
   regardless of how hard the individual case was.

So when the pipeline reports each payer's multiple and a per-code adequacy ratio
near 0.70, it has recovered assumptions that were put in. That is the point:
scoring, linkage, pricing, the fitted curve, the multiplier and the division are
all independent of the generator, so recovering the numbers end to end means the
arithmetic between them is sound. The PFS amounts themselves are real CMS data;
the encounters, payments and complexity are not.

**It is not evidence about American healthcare.** It validates the instrument,
not the finding.

## What is deliberately easy

This is a happy path. Every encounter links on the first rule. There are no
reversals, no replacement claims, no split professional and facility remits for
one surgery, no secondary-payer coordination, no capitated zero-dollar remits,
and no provider-level clawbacks. Real data has all of them, the linkage rate
will not be 100%, and the encounters that fail to link will be the complex ones, which is why the linkage rate is published with every index value rather than
kept in a log.
