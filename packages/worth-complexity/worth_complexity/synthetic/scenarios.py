"""The scenario catalog (CONTRACT-SEEDS.md, "Scenario catalog and planted
truth"): one :class:`Scenario` per row of that table, plus the four
``broken/*`` variants. ``worth-cli synth --list`` prints this catalog;
``worth-cli synth --scenario S --class C DIR`` looks a name up here and
hands it to that class's ``build()``.

A ``Scenario`` is data, not code: every class's ``build()`` reads the same
knobs (payment rule, Method 1 omission, friction, cohort sizing, the policy
date, multi-site, dirt) and applies whichever of them its own generation
produces. A knob a class has no use for (e.g. ``multi_site`` for the episode
class, which never had per-site documentation habits to begin with) is
simply not read there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

#: The four payers (worth_complexity.synthetic.edi.PAYERS), named here by id
#: so a scenario's friction knobs can target one without importing edi for
#: what is otherwise a pure-data module.
PAYER_UHC = "87726"
PAYER_AETNA = "60054"
PAYER_NGS_MEDICARE = "04412"
PAYER_HEALTHFIRST = "80141"


@dataclass(frozen=True)
class Scenario:
    """One row of the catalog. Every field has a baseline-safe default, so a
    scenario only sets the knobs its own row actually plants."""

    name: str
    group: str  # "core" | "broken"
    description: str

    # -- payment (parity / compression / center-mispriced / overpaid / baseline) --
    payment_rule: str = "flat-at-median-share"
    """One of ``flat-at-median-share``, ``on-curve``, ``at-median``,
    ``curve-share``, ``overpaid-at-median`` -- see
    :func:`worth_complexity.synthetic.pricing.price_study`."""
    payment_share: Decimal = Decimal("0.70")
    """The share (or multiple, for ``overpaid-at-median``) ``payment_rule``
    applies. Ignored by ``on-curve`` and ``at-median``, which do not scale."""

    # -- Method 1 (method1-heavy) --
    m1_omission_rate: float = 0.0
    """Fraction of documented secondary steps dropped from the claim and the
    note, so Method 1 finds them as ``missed``/``no_code`` rather than
    billed."""
    m1_underlevel: bool = False
    """Visit class: guarantee every study visit's documented time clears the
    ``em-level-by-time-99214`` threshold (40 minutes) while it still bills
    the flat 99214 -- the work documented supports the next level, the
    claim does not reflect it. Baseline already crosses this threshold on
    most study visits by construction (the time draw); this knob makes it
    every one, rather than leaving it to the draw."""

    # -- friction (payer-friction) --
    denial_share: dict[str, float] = field(default_factory=dict)
    """payer_id -> fraction of that payer's primary lines denied
    (CO-97/CO-234, RARC N19/M15)."""
    downcode_share: dict[str, float] = field(default_factory=dict)
    """payer_id -> fraction of that payer's lines paid against a different
    (lower) CPT on the 835 than the 837 submitted."""
    reversal_pairs: int = 0
    """How many paid claims get a reversal (CLP02=22) transaction followed
    by a corrected claim, net zero on the original."""
    no_modifier22_payer: str | None = None
    """A payer id that never pays the modifier-22 line at all (denied,
    CO-97), regardless of ``denial_share``."""

    # -- cohort sizing (thin-cohorts) --
    thin_cohorts: bool = False
    """8 study cases per code (above the n=11 suppression floor only when
    two payers' shares are pooled -- below it per payer), one payer with
    only 4 comparators (no fitted multiplier), one comparator code with 5
    cases."""

    scale: int = 1
    """Cohort multiplier the scenario needs on top of the caller's ``--scale``.
    ``policy-change`` spreads its cohort over 24 months, so it needs four
    times the cases for a quarter to clear the n = 11 floor."""

    # -- periods (policy-change) --
    policy_date: date | None = None
    """24 months of service dates straddle this date. Visit class: maternity
    bills a single 59426 antepartum-bundle claim per pregnancy before it,
    99214 per visit after. Surgical/episode: a 4% fee-schedule step at it."""

    # -- sites (multi-site) --
    multi_site: bool = False
    """Three facility NPIs; site B under-documents adhesion/extent language
    60% of the time, site C's OR log lacks EBL."""

    # -- dirt / broken --
    dirt: tuple[tuple[str, dict[str, Any]], ...] = ()
    """``(function name in worth_complexity.synthetic.dirt, kwargs)`` pairs,
    applied in order after generation."""
    broken: str | None = None
    """One of the four ``broken/*`` variant names; ``None`` for every
    non-broken scenario."""

    notes: tuple[str, ...] = ()
    """Free-text notes written into ``truth.json``, e.g. what was put in and
    why the ratio comes out where it does."""

    purpose: str = ""
    """A one-or-two-sentence "why this dataset is useful to test with"
    template, rendered into ``truth.json``'s top-level ``purpose`` at
    generation time by :func:`worth_complexity.synthetic.truth.
    render_purpose`, with placeholders filled from the seed's own
    ``planted`` values. A ``[bracketed clause]`` is dropped whole, rather
    than left with an unfilled placeholder, when a value it needs was not
    planted for this seed (e.g. ``compression``'s ``{ratio_p90}``, which
    nothing in the catalog plants today) -- the rendered sentence reads
    well either way. See ``synthetic/README.md``'s "purpose" section for
    the placeholder list."""


SCENARIOS: dict[str, Scenario] = {
    "baseline": Scenario(
        name="baseline",
        group="core",
        description="today's fixtures' assumptions: flat study payment at 70% of the curve at "
        "the median",
        payment_rule="flat-at-median-share",
        payment_share=Decimal("0.70"),
        notes=(
            "Every study code is paid at the comparator curve's value at the code's own mean "
            "score, times the payer multiple -- flat within a code, the phenomenon under study.",
        ),
        purpose=(
            "The reference dataset: every study code is paid one flat amount at about 70% of "
            "what comparators earn at the same complexity. Run it to see the canonical finding "
            "-- flat Method 0 slopes[, a ratio near {ratio},] and a fee-schedule-dominant "
            "signature."
        ),
    ),
    "parity": Scenario(
        name="parity",
        group="core",
        description="realized = curve(score) x payer multiple, small noise",
        payment_rule="on-curve",
        payment_share=Decimal("1.00"),
        notes=(
            "Every study encounter is paid the curve's own value at its own score: nothing "
            "planted to find, the instrument should read close to 1.0 and rising.",
        ),
        purpose=(
            "Every study encounter is paid exactly what the comparator curve pays at its own "
            "score. The null case:[ ratios sit at {ratio} against the 1.0 line,] Method 0 "
            "rises, and the signature has nothing to explain."
        ),
    ),
    "compression": Scenario(
        name="compression",
        group="core",
        description="realized = curve(median) x multiple for every case, regardless of the "
        "individual case's own score",
        payment_rule="at-median",
        payment_share=Decimal("1.00"),
        notes=(
            "Payment is capped at the median case's curve value: the ratio reads near 1 at the "
            "median score and falls below it for harder cases -- compression, not omission.",
        ),
        purpose=(
            "Every case is paid the code's median value regardless of complexity. Shows a code "
            "that is right at its center and wrong in its tail: near 1.0 at the median[, "
            "{ratio_p90} at p90], compression the dominant lever."
        ),
    ),
    "center-mispriced": Scenario(
        name="center-mispriced",
        group="core",
        description="realized = 0.7 x curve(score) x multiple",
        payment_rule="curve-share",
        payment_share=Decimal("0.70"),
        notes=(
            "Payment tracks the individual case's own score, just at a constant 70% of what the "
            "curve says it is worth -- a uniformly mispriced center, not a flat or compressed one.",
        ),
        purpose=(
            "Payment tracks complexity but the whole line sits 30% low. The opposite of "
            "compression: Method 0 rises,[ yet every case reads about {ratio},] and the "
            "shortfall is center mispricing."
        ),
    ),
    "overpaid": Scenario(
        name="overpaid",
        group="core",
        description="realized = 1.3 x curve(median) x multiple",
        payment_rule="flat-at-median-share",
        payment_share=Decimal("1.30"),
        notes=("The mirror image of baseline: flat, but above 1.0 rather than below it.",),
        purpose=(
            "Study codes are paid about 30% above the curve. Proves the instrument reads both "
            "directions: ratios above 1.0[ (about {ratio})] and a negative shortfall."
        ),
    ),
    "method1-heavy": Scenario(
        name="method1-heavy",
        group="core",
        description="every documented secondary step omitted from the 837; every visit "
        "documented past its billed level's time threshold",
        m1_omission_rate=0.6,
        m1_underlevel=True,
        notes=(
            "Method 1's missed/no_code buckets dominate the dollar gap rather than the ratio "
            "itself moving: documented work is real, billing does not reflect it.",
        ),
        purpose=(
            "Documented work is systematically left off the 837[: {missed} missed and "
            "{mismatched} mismatched flags across {n} study encounters]. Useful for the Method "
            "1 view, the work queue and an M1-dominant signature."
        ),
    ),
    "payer-friction": Scenario(
        name="payer-friction",
        group="core",
        description="one payer denies 30% of primary lines, one downcodes 20%, reversals "
        "followed by corrected claims, one payer never pays modifier 22",
        denial_share={PAYER_UHC: 0.30},
        downcode_share={PAYER_AETNA: 0.20},
        reversal_pairs=3,
        no_modifier22_payer=PAYER_NGS_MEDICARE,
        notes=(
            "Friction is visible only by comparing the 837 to the 835 (what was submitted "
            "against what was allowed, denied or downcoded); the underlying structural pricing "
            "is baseline's, so the ratio's structural shape is unchanged, the loss is friction.",
        ),
        purpose=(
            "One payer denies primary lines[ (about {denial_rate})] and another downcodes; "
            "reversals and corrected claims are included. Shows friction reported beside the "
            "structural ratio and never merged into it."
        ),
    ),
    "thin-cohorts": Scenario(
        name="thin-cohorts",
        group="core",
        description="8 study cases per code, one payer with 4 comparators, one comparator code "
        "with 5 cases",
        thin_cohorts=True,
        notes=(
            "Below the n=11 suppression floor per payer stratum and, for one code, in aggregate: "
            "the point is what the pipeline does when a cell cannot be reported, not a ratio.",
        ),
        purpose=(
            "This data is helpful in showing what happens when cohorts sit at or under the n = "
            "11 suppression floor. After running the pipeline you will see how the dashboard "
            "degrades honestly[ when {suppressed_share} of codes have insufficient data] and "
            "one payer has no multiplier."
        ),
    ),
    "policy-change": Scenario(
        name="policy-change",
        group="core",
        description="24 months, 2026 and 2027; maternity bills an antepartum bundle before the "
        "date and 99214 after; surgical/episode get a 4% fee-schedule step",
        policy_date=date(2027, 1, 1),
        scale=4,
        notes=(
            "Two periods either side of the policy date; the visit class's maternity billing "
            "vehicle changes shape across it (bundle -> per-visit 99214), which the Over-time "
            "output (worth track S2) is what reads back.",
            "Assumption: a pre-date pregnancy's antepartum bundle (CPT 59426/59425) is billed "
            "once for the whole pregnancy; each covered visit's own realized payment is that "
            "claim's allowed amount split evenly across the visits it covers "
            "(linkage.BUNDLE_CODES/LinkedEncounter.bundle_size) -- a fixture-side allocation "
            "choice for comparing each visit's own complexity score to its own share of what "
            "the pregnancy was paid, not a real per-visit CMS or payer rule.",
        ),
        purpose=(
            "Twenty-four months straddling[ the {policy_date}] maternity unbundling: an "
            "antepartum bundle before, per-visit 99214 after. The Over-time view reads the "
            "change; try By domain to follow maternity across the code change."
        ),
    ),
    "multi-site": Scenario(
        name="multi-site",
        group="core",
        description="three facility NPIs; site B under-documents 60% of the time, site C's OR "
        "log lacks EBL",
        multi_site=True,
        notes=(
            "Missingness is per marker per site (decision 6), not a refusal: site B and C read "
            "with real gaps in specific markers, site A stays complete.",
        ),
        purpose=(
            "Three facility NPIs with different documentation habits. Missingness by marker "
            "and site becomes visible[ ({missingness_max} at the worst site)], and scores "
            "drift across sites for identical work."
        ),
    ),
    "dirty": Scenario(
        name="dirty",
        group="core",
        description="blank operative minutes, one absurd case, duplicate accounts, unlinked "
        "encounters, orphan remittances, notes without section headers, header-case and "
        "date-format variance",
        dirt=(
            ("blank_operative_minutes", {"p": 0.05}),
            ("absurd_operative_time", {}),
            ("duplicate_accounts", {"n": 2}),
            ("drop_835_for", {"share": 0.03}),
            ("orphan_835s", {"n": 3}),
            ("strip_note_sections", {"n": 2}),
            ("header_case", {"table": "anchor"}),
            ("date_format", {"table": "patient_lds.txt", "fmt": "MM/DD/YYYY"}),
            ("claim_without_835", {"n": 2}),
        ),
        notes=(
            "Every corruption here is individually survivable: the run completes, at a linkage "
            "rate below 1.0 and a nonzero missingness maximum, not a raised exception.",
        ),
        purpose=(
            "A realistic delivery: blank fields, an implausible operative time, duplicate "
            "accounts, unlinked encounters and orphan remittances[, with linkage at "
            "{linkage}]; the run completes and every gap is reported rather than hidden."
        ),
    ),
    "broken/unpriceable-comparator": Scenario(
        name="broken/unpriceable-comparator",
        group="broken",
        description="a comparator code the fee fixture does not carry -- fails at price",
        broken="unpriceable-comparator",
        purpose=(
            "Designed to fail at the {stage} stage with {error}. Useful for exercising the "
            "failed-run screen and the rerun flow; it never produces findings."
        ),
    ),
    "broken/malformed-table": Scenario(
        name="broken/malformed-table",
        group="broken",
        description="the anchor table is missing a required column -- fails at extract",
        broken="malformed-table",
        purpose=(
            "Designed to fail at the {stage} stage with {error}. Useful for exercising the "
            "failed-run screen and the rerun flow; it never produces findings."
        ),
    ),
    "broken/corrupt-835": Scenario(
        name="broken/corrupt-835",
        group="broken",
        description="one 835 with a truncated CLP segment -- fails at remittance",
        broken="corrupt-835",
        purpose=(
            "Designed to fail at the {stage} stage with {error}. Useful for exercising the "
            "failed-run screen and the rerun flow; it never produces findings."
        ),
    ),
    "broken/no-anchor": Scenario(
        name="broken/no-anchor",
        group="broken",
        description="tables present but no anchor file -- fails at extract",
        broken="no-anchor",
        purpose=(
            "Designed to fail at the {stage} stage with {error}. Useful for exercising the "
            "failed-run screen and the rerun flow; it never produces findings."
        ),
    ),
}

#: Study encounters per code the scenario suite targets (decision 5,
#: CONTRACT-SEEDS.md): "about 24 study encounters per code (above the
#: suppression floor of 11)". Each class's ``build()`` picks its own
#: per-code (or per-line) base count so that ``scale=1`` lands here;
#: ``scale`` then multiplies it.
SCENARIO_STUDY_PER_CODE = 24


def catalog_rows() -> list[tuple[str, str, str]]:
    """``(name, group, description)`` for every scenario, catalog order --
    what ``worth-cli synth --list`` renders as a table."""
    return [(s.name, s.group, s.description) for s in SCENARIOS.values()]
