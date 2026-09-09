# Episode-synthetic dataset

**Fabricated data. No patient, episode, clinician, payer contract or dollar
amount here corresponds to anything real.** It exists so the episode class's
pipeline path — `episodes.py`, the `episode-rpm-v1` rule pack, `method1`'s
`work_rules` — can be built and tested before a partner delivers a remote-
physiologic-monitoring extract.

Regenerate it with `just build-dataset episode`. It is deterministic: same
seed, same bytes. The generator and its assumptions are in
[`tools/make_episode_dataset.py`](../../../tools/make_episode_dataset.py); its
835/837 writers are a retargeted copy of the surgical generator's, not an
import — see that file's module docstring for why.

## What is here

168 30-day remote-monitoring episodes over calendar 2026, in the shape
CONTRACT-PACKS.md's episode section describes, plus the ANSI X12 835
remittances and 837P claims behind them.

### `clinical/`, nine pipe-delimited files, no `notes/`

| file | grain | carries |
|---|---|---|
| `episode.txt` | one row per 30-day episode | start/end date, service line, specialty, cohort, site, clinician, program id |
| `episode_proc.txt` | one row per billed code | the CPT panel; sequence 1 is always the RPM/monitoring code |
| `device_readings.txt` | one row per reading | timestamp, metric (SBP/DBP/GLUCOSE/WEIGHT/DEVICE_CHECK), value |
| `alerts.txt` | one row per exception | timestamp, severity (1-3), rule id, handled by (autonomous or clinician) |
| `interventions.txt` | one row per clinician action | medication titration, evaluation, order, message |
| `time_log.txt` | one row per oversight entry | minutes, whether it was interactive |
| `problem_list.txt` | one row per active flag | PREECLAMPSIA_RISK/HF/CKD/T2DM-family flags, tiered 0-3 |
| `outcomes.txt` | one row per episode | 30-day control, ED visit, readmission, whether an ED visit was averted |
| `patient_lds.txt` | one row per patient | Limited Data Set, as the surgical fixture |

No `notes/` directory: the methodology's own framing for this class is "from
device and alert logs alone", and `episodes.py` never gates a marker on a
pack the way the surgical and visit narrative markers do — every marker here
is `structured`. (`episodes.py` tolerates a `notes/` directory when one is
delivered anyway; this fixture simply does not carry one, and
`tests/test_episodes.py` checks both that absence and that tolerance
directly.)

### `remittance/` and `claims/`

Same shapes, same join keys (`billing_account_id` = `CLP01` = `CLM01`) as the
surgical fixture; see its own README for what each transaction carries.
Every episode's secondary RPM codes (99454, 99457, 99458, where billed) are
allowed and paid alongside the primary line, but — unlike the surgical
fixture's adhesiolysis code — none of them are bundled away: only the
primary line's allowed amount ever feeds the adequacy ratio
(`LinkedEncounter.realized`), so the secondary lines exist for claim realism
and for Method 1's "already submitted" check, not to demonstrate an NCCI
mismatch.

## The two cohorts

**Study, 60 episodes, one shared code.** POSTPARTUM_HTN (obstetrics) always
bills CPT **99453** first, plus 99454 whenever the episode's own
`reading_days` clears the real 16-day CMS threshold — with one deliberate
exception: episodes in the top slice of the study intensity range clear the
threshold but never bill it, a genuine documentation-capture gap for the
pack's `device-supply-threshold` rule to find (7 episodes trip it in this
fixture). 99457 and 99458 are billed exactly at the 20- and 40-interactive-
minute thresholds the pack's own `management-increment-*` rules check, so
those two rules fire only on hand-built rows in `tests/test_episodes.py`, not
from this fixture. Every study episode's realized payment is a single fixed
amount per payer (a fraction of what a curve fitted on the comparator
cohort's own (score, PFS) pairs predicts at the study cohort's median score),
not tied to the individual episode's score at all — the phenomenon under
study, and why Method 0 is flat on 99453 regardless of which payer's stratum
is read.

**Comparator, 108 episodes, three RPM/CGM/dialysis programs.**
CARDIAC_DEVICE, CGM and HOME_DIALYSIS (36 episodes apiece) occupy successive
bands of the same intensity draw that drives every marker, low to high, and
each program's primary code steps with it — CARDIAC_DEVICE 93294 → 93296 →
93295, CGM 95251 → 95250, HOME_DIALYSIS 90966 → 90960 — chosen by each code's
own real PFS amount (93296 actually prices below 93295, so the tiering
follows dollars, not CPT numeric order) so the pooled (score, realized) trend
across all three programs climbs from the cheapest cardiac-device
interrogation to the most intensive home-dialysis month. Every comparator's
realized payment is the payer's multiple of the real Medicare PFS amount for
its own primary code, the same convention the surgical and visit generators
use.

Both cohorts score against the same `episode-rpm-v1` pack: one instrument
for the whole 30-day monitoring-episode class (decision 1,
CONTRACT-PACKS.md), scoring cardiac-device, home-dialysis and CGM programs
with the identical six markers and weights it scores postpartum hypertension
with.

## What the generator puts in, and what that means

1. **One continuous intensity draw, `t` in [0, 1], drives every marker input**
   for every episode, study and comparator alike, through a roughly linear
   map of the pack's own weights (see `make_episode_dataset.py`'s module
   docstring). Each program occupies its own band of `t`.
2. **The study code's realized payment is a curve-and-factor construction**,
   the same technique the surgical generator uses: fit a line through the
   comparator cohort's (score, reference-release PFS) pairs, read it at the
   study cohort's own median score, and pay `VALUATION_FACTOR` (0.34) of
   that, times the payer's multiple and a little noise — one number, reused
   for every study episode, which is what makes Method 0 flat on it.
3. Four payers, the same identities and contracted multiples as the surgical
   fixture.

So when the pipeline reports, on this fixture:

- a per-code adequacy ratio of **0.45** (95% CI 0.36-0.57), within the
  doc's worked-example-D band of roughly 0.4-0.6,
- a flat Method 0 line on 99453 for every payer,
- a rising pooled comparator slope across CARDIAC_DEVICE, CGM and
  HOME_DIALYSIS,
- Method 1 flags in all three buckets (49 `no_code`, 3 `mismatched`, 7
  `missed`, this run), and
- a signature mix of roughly M1 4% / M2 28% / M3 68%, with the median study
  episode reading as `center-mispriced` rather than the doc's "no billing
  vehicle" pattern. That is not a generator defect: `no_code` work is counted
  but not priced (decision 1 of the outputs build), so by dollars the gap
  shows up in Method 3. The doc's example D reaches "M1 and M3 dominant" by
  pricing no-code work at what equivalent work earns elsewhere; when that
  reference exists, this line will change and the test that prints the
  pattern will say so -

it has recovered structure that was put in on purpose, points 1-2 above, not
a hidden general finding about remote patient monitoring. The PFS amounts
themselves are real CMS data; the episodes, payments, device streams and
outcomes are not.

## What is deliberately easy

Every episode links to its remittance on the first rule, same as the
surgical and visit fixtures: no reversals, no split remits, no
secondary-payer coordination, no denials. Every work rule in
`episode-rpm-v1` reaches at least one bucket somewhere across this fixture
plus `tests/test_episodes.py`'s hand-built rows — `device-supply-threshold`
(`missed`, from this fixture directly), both `management-increment-*` rules
(`missed`, on hand-built rows only, per point 1 above), `sub-threshold-
exception-handling` (`mismatched`) and `autonomous-oversight` (`no_code`,
both from this fixture) — so between the two, Method 1's `missed`,
`mismatched` and `no_code` buckets are all exercised without needing a
second dataset.
