# Visit-synthetic dataset

**Fabricated data. No patient, encounter, clinician, payer contract or dollar
amount here corresponds to anything real.** It exists so the visit class's
pipeline path — `visits.py`, the `visit-em-v1` rule pack, `method1`'s
`work_rules` — can be built and tested before a partner delivers an office
E/M extract.

Regenerate it with `just build-dataset visit`. It is deterministic: same
seed, same bytes. The generator and its assumptions are in
[`tools/make_visit_dataset.py`](../../../tools/make_visit_dataset.py); it
reuses (imports, does not copy) the surgical generator's 835/837 writers,
`write_remittance` and `write_claims`, because both read their encounter
argument by attribute only and never touch anything surgical-specific.

## What is here

228 office visits over calendar 2026, in the shape a partner's ambulatory EHR
produces, plus the ANSI X12 835 remittances and 837P claims behind them.

### `clinical/`, eight pipe-delimited files plus `notes/`

| file | grain | carries |
|---|---|---|
| `visit.txt` | one row per visit | service date, service line, specialty, cohort, site, clinician, total documented minutes |
| `visit_proc.txt` | one row per billed code | the CPT panel; sequence 1 is always the E/M level |
| `encounter_dx.txt` | one row per diagnosis | ICD-10, sequence, whether it was addressed today |
| `orders.txt` | one row per order or result reviewed | labs, imaging, DEXA, glucose logs, fetal surveillance |
| `med_orders.txt` | one row per medication order | start/adjust/continue/stop |
| `window_encounters.txt` | one row per contact in the 30 days after the visit | portal, telephone, referral, lab review, prior authorisation, MFM comanagement, nurse call — minutes and who initiated it |
| `problem_list.txt` | one row per active flag | pregnancy and chronic-condition flags, tiered 0-3 |
| `patient_lds.txt` | one row per patient | Limited Data Set, as the surgical fixture |
| `notes/` | one visit note per visit | SUBJECTIVE, OBJECTIVE, ASSESSMENT, PLAN, COUNSELING, TIME |

`visits.py` reads all eight tables with `cases.read_table` and the note
dataset with `cases.read_notes`, unchanged from the surgical reader.

### `remittance/` and `claims/`

Same shapes, same join keys (`billing_account_id` = `CLP01` = `CLM01`) as the
surgical fixture; see its own README for what each transaction carries. The
only difference here is that nothing is deliberately bundled away — every
billed line, including the digital-E/M, care-management and prolonged-service
add-ons, is allowed at its own priced amount, because none of them are meant
to demonstrate an NCCI mismatch the way the surgical fixture's adhesiolysis
code is.

## The two cohorts

**Study, 120 visits, one shared code.** MENOPAUSE (gynaecology, 60 visits)
and MATERNITY (obstetrics, 60 visits) are both billed flat **99214**
regardless of how much time, data review or coordination the visit actually
involved — the phenomenon under study, and the reason both service lines fold
into the same population card even though their content differs sharply.
MATERNITY visits always carry a `PREGNANCY` or `PREGNANCY_HIGH_RISK`
problem-list flag and are drawn with enough documented time to cross the
pack's `em-level-by-time-99214` threshold on nearly every visit, so their
Method 1 flag is real, priced, and dominates their dollar spine — the
`m1-dominant-*` signature the methodology's worked example C describes.

**Comparator, 108 visits, three chronic-management specialties.**
ENDOCRINOLOGY, CARDIOLOGY and NEPHROLOGY (36 visits apiece) step their
primary E/M level with intensity — 99213 → 99214 → 99215 → 99215 with the
G2212 prolonged-service add-on — and bill the complexity add-on G2211 on
every visit and the chronic care-management code 99490 whenever clinician-
coordination time and chronic-condition count both qualify. Every comparator
patient carries two chronic problem-list flags, so the care-management
work rule's eligibility branch is reachable on this cohort by construction.

Both cohorts score against the same `visit-em-v1` pack: one instrument for
the whole office-visit E/M class (decision 1, CONTRACT-PACKS.md), the visit
class's equivalent of the surgical pack scoring urology and orthopaedics with
the same markers it scores gynaecology with.

## What the generator puts in, and what that means

1. **Realized payment is simply `payer's multiple × real Medicare PFS`**, for
   whatever code was actually billed, in Medicare locality NY01, the
   **non-facility** place of service (`CLASS_DEFAULT_SETTING["visit"]`), at
   the CMS release in force on the service date. Unlike the surgical
   generator, no schedule curve is fitted to place the study codes at a
   deliberate fraction of it — there is no need to: MENOPAUSE and MATERNITY
   billing one flat code makes their realized payment structurally unable to
   move with the complexity score, whatever that score turns out to be, and
   the comparator ladder's own primary code already climbs with intensity, so
   a rising Method 0 slope falls out of the billed code alone.
2. Four payers, the same identities and contracted multiples as the surgical
   fixture (`PAYERS` is imported, not re-declared), so a payer's recovered
   multiplier can be checked against the same expected values across both
   fixtures.
3. Every visit note carries the same negated decoy sentence in SUBJECTIVE —
   "No shared decision conversation occurred..." — that must never count
   toward `patient_context`, both because it is negated and because
   SUBJECTIVE is outside the marker's allowed sections (COUNSELING, PLAN).
   About half of the non-pregnant visits carry a real, unnegated shared
   decision-making phrase in COUNSELING or PLAN instead.

So when the pipeline reports a rising comparator slope, a flat Method 0 line
for 99214, and a per-domain adequacy ratio in roughly the 0.5-0.9 band, it has
recovered structure that was put in on purpose: the billed-code-driven
payment described above, not a hidden valuation factor. The PFS amounts
themselves are real CMS data; the encounters, payments and documentation are
not.

## What is deliberately easy

Every visit links to its remittance on the first rule, same as the surgical
fixture: no reversals, no split remits, no secondary-payer coordination, no
denials. Every work rule in `visit-em-v1` fires at least once across the
cohort — `em-level-by-time` (both thresholds), `prolonged-service`,
`online-digital-em` (all three tiers), `care-management-coordination` and its
ineligible branch, `non-vehicle-coordination`, and `prenatal-em-basis` — so
the fixture alone exercises Method 1's `missed`, `mismatched` and `no_code`
buckets without needing a second dataset.
