"""Population views: histogram, deciles, shortfall, mix, pooled slope, queue."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

from worth_complexity.money import money_context
from worth_complexity.population import (
    CodeCard,
    EncounterRow,
    HistogramBin,
    PayerFrictionRow,
    PeriodRow,
    QueueRow,
    code_card,
    compare_rows,
    headline,
    histogram,
    payer_friction,
    periods,
    pooled_slope,
    queue,
    ratio_at,
    ratio_by_decile,
    shortfall,
    signature_mix,
)
from worth_complexity.signature import MethodMagnitude, Signature

FOUR = Decimal("0.0001")

DUMMY_SIGNATURE = Signature(
    m1=MethodMagnitude(method="M1", dollars=None, lever="code-set-gap", built=False, label="m1"),
    m2=MethodMagnitude(
        method="M2", dollars=Decimal(0), lever="code-structure", built=True, label="m2"
    ),
    m3=MethodMagnitude(
        method="M3", dollars=Decimal(0), lever="fee-schedule", built=True, label="m3"
    ),
    pattern_id="mixed",
    dominant="mixed",
    m2_m3_converge=True,
)

SCORES = list(range(60))


def _rows() -> tuple[EncounterRow, ...]:
    """60 encounters, one per score 0..59. realized = 100*(score+1), expected
    (M3) held at a flat 1000, so shortfall and ratio move predictably with
    score."""
    rows = []
    for score in SCORES:
        realized = Decimal(100) * Decimal(score + 1)
        expected = Decimal(1000)
        rows.append(
            EncounterRow(
                encounter_id=f"E{score}",
                score=score,
                realized=realized,
                expected=expected,
                m2_expected=expected,
                m2_adjustment=Decimal(0),
                center_mispricing=Decimal(0),
                m3_shortfall=expected - realized,
                m1_total=None,
                m1_split=(None, None, None),
                multiplier=Decimal(1),
                payer_label="Payer A",
                site="Main",
                pattern_id="mixed",
                dominant_lever="fee-schedule",
                signature=DUMMY_SIGNATURE,
                friction=(False, False, (), ()),
                present_markers=("op_time", "ebl"),
            )
        )
    return tuple(rows)


# --------------------------------------------------------------------------
# histogram
# --------------------------------------------------------------------------


def test_histogram_bins_are_ten_points_wide_last_bin_inclusive_of_100() -> None:
    bins = histogram((0, 9, 10, 19, 89, 90, 99, 100))
    assert bins[0] == HistogramBin(0, 10, 2)  # 0, 9
    assert bins[1] == HistogramBin(10, 20, 2)  # 10, 19
    assert bins[8] == HistogramBin(80, 90, 1)  # 89
    assert bins[9] == HistogramBin(90, 100, 3)  # 90, 99, 100
    assert len(bins) == 10
    assert sum(b.count for b in bins) == 8


def test_histogram_empty_bin_is_zero() -> None:
    bins = histogram((5, 5, 5))
    assert bins[0].count == 3
    assert all(b.count == 0 for b in bins[1:])


# --------------------------------------------------------------------------
# deciles, on 60 synthetic scores
# --------------------------------------------------------------------------


def test_deciles_on_sixty_scores_are_even_sixes() -> None:
    rows = _rows()
    deciles = ratio_by_decile(rows)
    assert len(deciles) == 10
    assert [d.decile for d in deciles] == list(range(1, 11))
    assert all(d.n == 6 for d in deciles)
    assert sum(d.n for d in deciles) == 60


def test_first_and_last_decile_ratios() -> None:
    rows = _rows()
    deciles = ratio_by_decile(rows)
    # decile 1: scores 0-5 -> realized 100..600, expected 1000 each
    with money_context():
        realized = sum((Decimal(100) * Decimal(s + 1) for s in range(0, 6)), start=Decimal(0))
        expected = Decimal(6000)
        want = (realized / expected).quantize(FOUR)
    assert deciles[0].n == 6
    assert deciles[0].ratio is not None
    assert deciles[0].ratio.value == want
    assert deciles[0].interval is not None

    # decile 10: scores 54-59
    with money_context():
        realized10 = sum((Decimal(100) * Decimal(s + 1) for s in range(54, 60)), start=Decimal(0))
        want10 = (realized10 / Decimal(6000)).quantize(FOUR)
    assert deciles[9].n == 6
    assert deciles[9].ratio is not None
    assert deciles[9].ratio.value == want10


def test_ratio_at_picks_the_decile_containing_each_percentile_score() -> None:
    rows = _rows()
    deciles = ratio_by_decile(rows)
    at = ratio_at(rows)
    by_point = {r.point: r for r in at}

    # quantile(0.5) over 0..59 -> 29.5, which falls in decile 6 [29.5, 35.4).
    assert by_point["median"].n == deciles[5].n
    assert by_point["median"].ratio == deciles[5].ratio
    # quantile(0.75) -> 44.25, decile 8 [41.3, 47.2).
    assert by_point["p75"].n == deciles[7].n
    # quantile(0.9) -> 53.1, exactly the decile 9/10 boundary; the last
    # decile's own boundary rule is inclusive on both ends, so it lands in
    # decile 10.
    assert by_point["p90"].n == deciles[9].n


# --------------------------------------------------------------------------
# shortfall
# --------------------------------------------------------------------------


def test_shortfall_totals_and_top_quintile_share() -> None:
    rows = _rows()
    sf = shortfall(rows)
    with money_context():
        total = sum(
            (Decimal(1000) - Decimal(100) * Decimal(s + 1) for s in SCORES), start=Decimal(0)
        )
        cutoff = 47  # int(quantile(0..59, "0.8")) == int(47.2)
        top = sum(
            (Decimal(1000) - Decimal(100) * Decimal(s + 1) for s in SCORES if s >= cutoff),
            start=Decimal(0),
        )
        share = (top / total).quantize(FOUR)
    assert sf.n == 60
    assert sf.total == total
    assert sf.top_quintile == top
    assert sf.top_quintile_share == share


def test_shortfall_of_no_rows_is_all_zero() -> None:
    sf = shortfall(())
    assert sf.total == Decimal(0)
    assert sf.n == 0
    assert sf.m1_unpriced is None


# --------------------------------------------------------------------------
# signature mix
# --------------------------------------------------------------------------


def _mag(method: str, dollars: Decimal, lever: str) -> MethodMagnitude:
    return MethodMagnitude(method=method, dollars=dollars, lever=lever, built=True, label=method)  # type: ignore[arg-type]


def test_signature_mix_shares_sum_to_one() -> None:
    sig_a = Signature(
        m1=_mag("M1", Decimal(2500), "coding-documentation"),
        m2=_mag("M2", Decimal(0), "code-structure"),
        m3=_mag("M3", Decimal(0), "fee-schedule"),
        pattern_id="m1-dominant-vehicle",
        dominant="a",
        m2_m3_converge=False,
    )
    sig_b = Signature(
        m1=_mag("M1", Decimal(0), "coding-documentation"),
        m2=_mag("M2", Decimal(2500), "code-structure"),
        m3=_mag("M3", Decimal(0), "fee-schedule"),
        pattern_id="compression",
        dominant="b",
        m2_m3_converge=False,
    )
    sig_c = Signature(
        m1=_mag("M1", Decimal(0), "coding-documentation"),
        m2=_mag("M2", Decimal(0), "code-structure"),
        m3=_mag("M3", Decimal(5000), "fee-schedule"),
        pattern_id="center-mispriced",
        dominant="c",
        m2_m3_converge=False,
    )
    mix = signature_mix((sig_a, sig_b, sig_c))
    assert mix.m1_share == Decimal("0.2500")
    assert mix.m2_share == Decimal("0.2500")
    assert mix.m3_share == Decimal("0.5000")
    assert mix.m1_share + mix.m2_share + mix.m3_share == Decimal("1.0000")
    assert mix.pattern_counts == {
        "m1-dominant-vehicle": 1,
        "compression": 1,
        "center-mispriced": 1,
    }


def test_signature_mix_dominant_lever_is_the_most_common_per_encounter_winner() -> None:
    sig_a = Signature(
        m1=_mag("M1", Decimal(100), "coding-documentation"),
        m2=_mag("M2", Decimal(0), "code-structure"),
        m3=_mag("M3", Decimal(0), "fee-schedule"),
        pattern_id="m1-dominant-vehicle",
        dominant="a",
        m2_m3_converge=False,
    )
    sig_b = Signature(
        m1=_mag("M1", Decimal(0), "coding-documentation"),
        m2=_mag("M2", Decimal(10), "code-structure"),
        m3=_mag("M3", Decimal(0), "fee-schedule"),
        pattern_id="compression",
        dominant="b",
        m2_m3_converge=False,
    )
    mix = signature_mix((sig_a, sig_a, sig_a, sig_b))
    assert mix.dominant_lever == "coding-documentation"


# --------------------------------------------------------------------------
# pooled slope
# --------------------------------------------------------------------------


def test_pooled_slope_insufficient_spread_when_too_few_points() -> None:
    points = tuple((s, Decimal(100 + s)) for s in range(5))
    p = pooled_slope(points)
    assert p.verdict == "insufficient spread"
    assert p.n == 5


def test_pooled_slope_insufficient_spread_when_scores_are_bunched() -> None:
    points = tuple((40 + i, Decimal(100 + i)) for i in range(10))  # spread == 9
    p = pooled_slope(points)
    assert p.verdict == "insufficient spread"


def test_pooled_slope_flat_when_payment_does_not_move_with_score() -> None:
    scores = [10, 20, 30, 40, 50, 60, 70, 80]
    points = tuple((s, Decimal(1000)) for s in scores)
    p = pooled_slope(points)
    assert p.verdict == "flat"
    assert p.slope == Decimal(0)
    assert p.n == 8
    assert p.score_min == 10
    assert p.score_max == 80


def test_pooled_slope_rising_on_a_perfect_line() -> None:
    scores = [10, 20, 30, 40, 50, 60, 70, 80]
    points = tuple((s, Decimal(100 + 5 * s)) for s in scores)
    p = pooled_slope(points)
    assert p.verdict == "rising"
    assert p.slope == Decimal(5)
    assert p.r_squared == Decimal(1)
    assert p.normalized_slope > 0


# --------------------------------------------------------------------------
# headline, compare_rows — via a real code_card()
# --------------------------------------------------------------------------


def _card(code: str = "58660", service_line: str = "GYN") -> CodeCard:
    rows = _rows()
    return code_card(
        code=code,
        encounter_class="surgical",
        service_line=service_line,
        n=len(rows),
        rows=rows,
        strata=(),
        comparator_codes=(),
        comparator_points=(),
        comparator_slope=None,
        method1=None,
        markers=(("op_time", "rule"), ("ebl", "structured")),
        rulebook_version="surgical-v1@1.0.0",
        weights_version="1.0.0",
        rule_pack_digest="deadbeef",
        suppressed=False,
        suppression_reason=None,
    )


def test_headline_names_the_code_and_reports_dashes_are_never_needed_here() -> None:
    card = _card()
    text = headline(card)
    assert text.startswith("58660:")
    assert "cases" in text
    assert "dominant." in text
    median = next(r for r in card.ratio_at if r.point == "median")
    assert str(median.ratio) in text


def test_headline_uses_an_em_dash_for_missing_pieces() -> None:
    card = _card()
    bare = replace(card, method0=None, shortfall=None, signature_mix=None)
    text = headline(bare)
    assert "—" in text


def test_compare_rows_one_row_per_code() -> None:
    card_a = _card("58660")
    card_b = _card("58662")
    rows = compare_rows((card_a, card_b))
    assert [r.code for r in rows] == ["58660", "58662"]
    for row in rows:
        assert row.encounter_class == "surgical"
        assert row.n == 60
        assert row.rulebook_version == "surgical-v1@1.0.0"
        # every marker present on every row in this fixture -> no missingness
        assert row.missingness == Decimal(0)


def test_compare_rows_by_codes_is_the_default_and_keeps_caller_order() -> None:
    card_a = _card("58662", service_line="GYN")
    card_b = _card("58660", service_line="ENDOCRINOLOGY")
    rows = compare_rows((card_a, card_b))
    assert [r.code for r in rows] == ["58662", "58660"]
    assert [r.domain for r in rows] == ["GYN", "ENDOCRINOLOGY"]


def test_compare_rows_by_domains_sorts_by_service_line_then_code() -> None:
    """Decision 6 (CONTRACT-PACKS.md): ``by="domains"`` orders cards by
    ``(service_line, code)`` instead of the caller's own order, so codes in
    the same domain sit together. Every row's ``domain`` is populated
    either way; only the ordering changes."""
    card_gyn_b = _card("58662", service_line="GYN")
    card_endo = _card("58660", service_line="ENDOCRINOLOGY")
    card_gyn_a = _card("58558", service_line="GYN")
    rows = compare_rows((card_gyn_b, card_endo, card_gyn_a), by="domains")
    assert [(r.domain, r.code) for r in rows] == [
        ("ENDOCRINOLOGY", "58660"),
        ("GYN", "58558"),
        ("GYN", "58662"),
    ]


# --------------------------------------------------------------------------
# payer friction
# --------------------------------------------------------------------------


def test_payer_friction_rates_and_reason_tallies() -> None:
    rows = (
        ("Payer A", True, False, ("50",), ()),
        ("Payer A", False, True, ("59",), ("HE1",)),
        ("Payer A", False, False, (), ()),
        ("Payer B", True, True, ("50", "97"), ()),
    )
    result = payer_friction(rows)
    by_payer = {r.payer_label: r for r in result}
    a = by_payer["Payer A"]
    assert a == PayerFrictionRow(
        payer_label="Payer A",
        n=3,
        denied=1,
        downcoded=1,
        denial_rate=Decimal("0.3333"),
        downcode_rate=Decimal("0.3333"),
        carc={"50": 1, "59": 1},
        rarc={"HE1": 1},
    )
    b = by_payer["Payer B"]
    assert b.n == 1
    assert b.denied == 1
    assert b.downcoded == 1
    assert b.carc == {"50": 1, "97": 1}


# --------------------------------------------------------------------------
# queue
# --------------------------------------------------------------------------


def _queue_row(encounter_id: str, strength: str | None, percentile: str) -> QueueRow:
    return QueueRow(
        encounter_id=encounter_id,
        code="58660",
        score=50,
        code_percentile=Decimal(percentile),
        flags=(),
        has_missed_flag=False,
        missed_candidate_code=None,
        evidence_strength=strength,  # type: ignore[arg-type]
        outcome_835="pending",
        payer_label="Payer A",
        site="Main",
        surgeon=None,
        top_quintile_cutoff=80,
    )


def test_queue_ranks_by_evidence_strength_then_percentile() -> None:
    rows = (
        _queue_row("weak-high", "weak", "0.99"),
        _queue_row("strong-low", "strong", "0.10"),
        _queue_row("moderate-mid", "moderate", "0.50"),
        _queue_row("strong-high", "strong", "0.90"),
        _queue_row("none", None, "0.95"),
    )
    ranked = queue(rows)
    assert [item.encounter_id for item in ranked] == [
        "strong-high",
        "strong-low",
        "moderate-mid",
        "weak-high",
        "none",
    ]


def test_queue_suggested_vehicle_modifier_22_above_cutoff_with_no_missed_flag() -> None:
    row = QueueRow(
        encounter_id="E1", code="58660", score=90, code_percentile=Decimal("0.95"),
        flags=(), has_missed_flag=False, missed_candidate_code=None,
        evidence_strength=None, outcome_835="pending", payer_label="Payer A",
        site="Main", surgeon=None, top_quintile_cutoff=80,
    )  # fmt: skip
    item = queue((row,))[0]
    assert item.suggested_vehicle == "Modifier 22"


def test_queue_suggested_vehicle_is_the_missed_candidate_code() -> None:
    row = QueueRow(
        encounter_id="E1", code="58660", score=40, code_percentile=Decimal("0.30"),
        flags=(), has_missed_flag=True, missed_candidate_code="58661",
        evidence_strength="strong", outcome_835="pending", payer_label="Payer A",
        site="Main", surgeon=None, top_quintile_cutoff=80,
    )  # fmt: skip
    item = queue((row,))[0]
    assert item.suggested_vehicle == "58661"


def test_queue_suggested_vehicle_none_when_neither_applies() -> None:
    row = QueueRow(
        encounter_id="E1", code="58660", score=40, code_percentile=Decimal("0.30"),
        flags=(), has_missed_flag=False, missed_candidate_code=None,
        evidence_strength=None, outcome_835="pending", payer_label="Payer A",
        site="Main", surgeon=None, top_quintile_cutoff=80,
    )  # fmt: skip
    item = queue((row,))[0]
    assert item.suggested_vehicle is None


# --------------------------------------------------------------------------
# periods (decision 7, CONTRACT-SEEDS.md)
# --------------------------------------------------------------------------


def _period_row(
    code: str, day: date, *, realized: Decimal, expected: Decimal, payer: str = "Payer A"
) -> PeriodRow:
    return PeriodRow(
        code=code,
        service_date=day,
        score=50,
        realized=realized,
        expected=expected,
        signature=DUMMY_SIGNATURE,
        payer_label=payer,
    )


def test_periods_suppresses_a_thin_month_but_not_a_full_one() -> None:
    """The suppression floor (n < 11) applies per point, decision 7."""
    january = [
        _period_row("58662", date(2026, 1, d), realized=Decimal(1000), expected=Decimal(1000))
        for d in range(1, 16)  # 15 rows: clears the floor
    ]
    february = [
        _period_row("58662", date(2026, 2, d), realized=Decimal(700), expected=Decimal(1000))
        for d in range(1, 6)  # 5 rows: below the floor of 11
    ]
    (series,) = periods((*january, *february), policy_date=None)
    by_period = {p.period: p for p in series.points}
    assert by_period["2026-01"].suppressed is False
    assert by_period["2026-01"].n == 15
    assert by_period["2026-01"].ratio is not None
    assert by_period["2026-01"].ratio.value == Decimal("1.0000")
    assert by_period["2026-02"].suppressed is True
    assert by_period["2026-02"].n == 5
    assert by_period["2026-02"].ratio is None
    assert by_period["2026-02"].interval is None


def test_periods_pre_post_split_at_the_policy_date() -> None:
    january = [
        _period_row("58662", date(2026, 1, d), realized=Decimal(1000), expected=Decimal(1000))
        for d in range(1, 16)
    ]
    march = [
        _period_row("58662", date(2026, 3, d), realized=Decimal(700), expected=Decimal(1000))
        for d in range(1, 16)
    ]
    (series,) = periods((*january, *march), policy_date=date(2026, 2, 1))
    assert series.policy_date == date(2026, 2, 1)
    assert series.pre is not None
    assert series.post is not None
    assert series.pre.n == 15
    assert series.post.n == 15
    assert series.pre.ratio is not None and series.pre.ratio.value == Decimal("1.0000")
    assert series.post.ratio is not None and series.post.ratio.value == Decimal("0.7000")
    assert series.pre.distribution.n == 15


def test_periods_with_no_policy_date_reports_no_pre_or_post() -> None:
    rows = (_period_row("58662", date(2026, 1, 1), realized=Decimal(1), expected=Decimal(1)),)
    (series,) = periods(rows, policy_date=None)
    assert series.pre is None
    assert series.post is None


def test_periods_by_payer_splits_the_series() -> None:
    rows = tuple(
        _period_row(
            "58662",
            date(2026, 1, d),
            realized=Decimal(1000),
            expected=Decimal(1000),
            payer="Payer A",
        )
        for d in range(1, 16)
    ) + tuple(
        _period_row(
            "58662",
            date(2026, 1, d),
            realized=Decimal(500),
            expected=Decimal(1000),
            payer="Payer B",
        )
        for d in range(1, 16)
    )
    (series,) = periods(rows, policy_date=None)
    by_payer = {ps.payer_label: ps for ps in series.by_payer}
    assert set(by_payer) == {"Payer A", "Payer B"}
    a_point = by_payer["Payer A"].points[0]
    b_point = by_payer["Payer B"].points[0]
    assert a_point.ratio is not None and a_point.ratio.value == Decimal("1.0000")
    assert b_point.ratio is not None and b_point.ratio.value == Decimal("0.5000")


def test_periods_quarter_granularity_labels() -> None:
    rows = tuple(
        _period_row("58662", date(2026, m, 1), realized=Decimal(1000), expected=Decimal(1000))
        for m in (1, 2, 3, 4, 5, 6)
    )
    (series,) = periods(rows, policy_date=None, granularity="quarter")
    assert [p.period for p in series.points] == ["2026-Q1", "2026-Q2"]


def test_periods_one_series_per_code_sorted() -> None:
    rows = (
        _period_row("58663", date(2026, 1, 1), realized=Decimal(1), expected=Decimal(1)),
        _period_row("58662", date(2026, 1, 1), realized=Decimal(1), expected=Decimal(1)),
    )
    series = periods(rows, policy_date=None, rulebook_version="surgical-v1@1", weights_version="w1")
    assert [s.code for s in series] == ["58662", "58663"]
    assert all(s.rulebook_version == "surgical-v1@1" for s in series)
    assert all(s.weights_version == "w1" for s in series)


def test_periods_of_no_rows_is_empty() -> None:
    assert periods((), policy_date=None) == ()
