"""Command line for worth-complexity."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from worth_fees.cli import parse_when

from worth_complexity.method1 import Method1Summary
from worth_complexity.models import (
    LAYER_A,
    LAYER_A_STRUCTURED_ONLY,
    SUPPRESSION_THRESHOLD,
    Adequacy,
    Cohort,
)
from worth_complexity.pipeline import ClassRun, Run, run

if TYPE_CHECKING:
    from worth_complexity.rulepack import RulePack

FIXTURE = Path(__file__).parent / "fixtures" / "mssm-synthetic"
FIXTURE_LOCALITY = "NY01"
"""Manhattan: where the synthetic institution is. The extract does not carry a
locality, so the CLI defaults to the synthetic dataset's; a real extract needs
``--locality`` set to the partner's."""

FILTERS = {
    "structured": LAYER_A_STRUCTURED_ONLY,
    "layer-a": LAYER_A,
}


def _pack_source_label(pack: RulePack) -> str:
    if pack.source == "packaged":
        return "packaged"
    return f"external {pack.source_path}"


def _banner(r: Run) -> list[str]:
    """The shared header: locality, reference release, linkage, and the
    classes this run covers. Decision 6, CONTRACT-PACKS-MC.md: "the banner
    lists the classes" — per-class pack/rulebook/weights/setting now sit in
    each class's own section header instead (:func:`_class_header`)."""
    return [
        f"classes     {', '.join(c.encounter_class for c in r.classes)}",
        f"priced in   {r.locality} {r.locality_name}; schedule curve at CMS {r.reference.label}",
        f"linkage     {r.linkage.render()}",
        f"cohort      {len(r.study)} study encounters, {len(r.comparator)} comparator",
    ]


def _class_header(c: ClassRun) -> list[str]:
    pack = c.pack
    lines = [
        "",
        "#" * 78,
        f"CLASS: {c.encounter_class}".upper(),
        "#" * 78,
        f"rule pack   {pack.rule_pack_id} v{pack.version} "
        f"[{pack.digest[:12]}]  status: {pack.status}  "
        f"source: {_pack_source_label(pack)}",
        f"rulebook    {c.rulebook_version}   weights {c.weights_version}",
        f"filter      {'+'.join(c.provenance_filter)}",
        f"setting     {c.setting.value}",
    ]
    if pack.status_note:
        lines.append(f"NOTE        {pack.status_note}")
    if pack.is_provisional:
        lines.append(
            "WARNING     rule pack is provisional: weights are a mine and Claude's placeholder, "
            "not clinical\n            or statistical input. Nothing computed here is "
            "publishable."
        )
    return lines


def cmd_report(r: Run) -> str:
    """The report, staged the way the methodology stages its own confidence.

    Two things are defensible from the first validated extract: the empirical
    reference distribution and the Method 0 slope. Both come first. The adequacy
    ratio depends on the cross-specialty curve, which the same document calls the
    part that will take longer and a genuine research problem, so it is reported
    below and labelled.

    One section per class present (decision 6, CONTRACT-PACKS-MC.md), each
    headed by :func:`_class_header`; a single-class run reads exactly as it
    always has, one section under the shared banner.
    """
    out = ["WORTH: Layer A complexity and payment adequacy", "=" * 78, *_banner(r), ""]
    for c in r.classes:
        out += _class_header(c)
        out += _class_section(r, c)
    out += _witness_lines(r)
    return "\n".join(out)


def _class_section(r: Run, c: ClassRun) -> list[str]:
    """Everything the report says about one class: distribution, Method 0,
    the schedule curve, multipliers, the adequacy index, Method 1, Method 3
    and the signature mix — all from that class's own ``ClassRun``."""
    out = [
        "",
        "DEFENSIBLE FROM THE FIRST VALIDATED EXTRACT",
        "=" * 78,
        "  No reference standard, no curve, nothing normative.",
        "",
        "Empirical reference distribution: Layer A complexity per code",
        "-" * 78,
        f"  {'code':<8}{'n':>5}{'median':>9}{'IQR':>14}{'range':>14}",
    ]
    for rec in c.records:
        d = rec.distribution
        out.append(
            f"  {rec.code:<8}{rec.n:>5}{d.median:>9.0f}"
            f"{f'{d.q1:.0f}-{d.q3:.0f}':>14}{f'{d.minimum:.0f}-{d.maximum:.0f}':>14}"
        )
    out.append("")

    out += [
        "Method 0: does payment track complexity within a code?",
        "-" * 78,
        "  One code, one payer at a time. Payers are blinded: the index reports whether",
        "  an institution's own payment matched its own work, never what a payer pays.",
        "",
    ]
    for rec in c.records:
        out.append(f"  CPT {rec.code}")
        out.extend(f"    {s.render()}" for s in rec.slopes)
        out.append("")

    out += [
        "RESEARCH STAGE. DEPENDS ON THE CROSS-SPECIALTY CURVE",
        "=" * 78,
        "  The methodology calls this the part that will take longer, to be derived with",
        "  clinical advisors and biostatisticians rather than asserted. Read accordingly.",
        "",
        "The fee schedule's complexity relation, fitted on the comparator cohort",
        "-" * 78,
    ]
    if c.schedule_curve is not None:
        out.append(f"  {c.schedule_curve.render()}")
        out.append(
            f"  Medicare PFS dollars at CMS {r.reference.label}, {r.locality} {c.setting.value}, "
            f"archive sha256 {r.reference.archive_sha256[:12]}..."
        )
    else:
        out.append("  no comparator encounter could be priced; no curve fitted")
    out += [
        "",
        "Payer multiples of the schedule, from each payer's own comparator encounters",
        "-" * 78,
        "  A contract level. This section stays with the partner and never enters a",
        "  published record.",
    ]
    out.extend(f"  {m.render()}" for m in c.multipliers.values())
    out.extend(f"  {reason}" for reason in c.thin_payers)
    out.append("")

    out += [
        "Payment adequacy index: per code, per institution, per period",
        "-" * 78,
        f"  {'code':<8}{'n':>5}{'scored':>8}{'ratio':>8}{'95% CI':>22}{'excluded':>10}",
    ]
    for rec in c.records:
        if rec.suppressed:
            out.append(
                f"  {rec.code:<8}{rec.n:>5}{'':>8}{'withheld':>8}"
                f"{f'n < {SUPPRESSION_THRESHOLD}':>22}"
            )
            continue
        if rec.adequacy is None:
            out.append(f"  {rec.code:<8}{rec.n:>5}{rec.scored:>8}{'n/a':>8}{'no curve':>22}")
            continue
        interval = rec.adequacy_interval.render(3) if rec.adequacy_interval else "n/a"
        out.append(
            f"  {rec.code:<8}{rec.n:>5}{rec.scored:>8}{rec.adequacy.value:>8.2f}"
            f"{interval:>22}{rec.excluded:>10}"
        )
    out += [
        "",
        "  No figure is reported across codes. Each code is valued separately, fails",
        "  separately and is petitioned separately; a blended number names no lever.",
    ]

    class_encounter_ids = {o.scored.encounter.encounter_id for o in c.observations}
    denied = [
        le
        for le in r.linkage.linked
        if le.denied_lines and le.encounter.encounter_id in class_encounter_ids
    ]
    if denied:
        out += ["", f"  Denial burden: {len(denied)} encounter(s) carry a line allowed at zero."]

    out += _method1_lines(c)
    out += _method3_lines(c)
    out += _signature_lines(c)
    out += _over_time_lines(c)
    return out


def _method1_lines(c: ClassRun) -> list[str]:
    out = [
        "",
        "Method 1 — documented work the claim cannot carry",
        "-" * 78,
    ]
    for card in c.cards:
        out.append(f"  CPT {card.code}")
        summary = card.method1
        if not isinstance(summary, Method1Summary):
            out.append("    no operative notes matched a procedure rule")
            continue
        for bucket in ("missed", "mismatched", "no_code"):
            bt = summary.by_bucket[bucket]
            dollars = f"${bt.dollars:,.2f}" if bt.dollars is not None else "unpriced"
            out.append(
                f"    {bucket:<11} count={bt.count:<4} priced={bt.priced_count:<4}  {dollars}"
            )
    return out


def _method3_lines(c: ClassRun) -> list[str]:
    out = [
        "",
        "Method 3 — complexity-matched expected, pooled band",
        "-" * 78,
    ]
    for card in c.cards:
        out.append(f"  CPT {card.code}")
        for point in card.ratio_at:
            ratio_s = str(point.ratio) if point.ratio is not None else "n/a"
            ci = point.interval.render(4) if point.interval is not None else "n/a"
            out.append(
                f"    {point.point:<7} score={point.score:<4} ratio={ratio_s:<6} "
                f"95% CI {ci:<22} n={point.n}"
            )
        if card.shortfall is not None:
            sf = card.shortfall
            share = f"{sf.top_quintile_share * 100:.1f}%"
            out.append(
                f"    shortfall total=${sf.total:,.2f}  top quintile=${sf.top_quintile:,.2f} "
                f"({share} of the total)  compression=${sf.compression:,.2f}  "
                f"center mispricing=${sf.center_mispricing:,.2f}"
            )
    return out


def _signature_lines(c: ClassRun) -> list[str]:
    out = [
        "",
        "Signature — which lever explains the gap",
        "-" * 78,
    ]
    for card in c.cards:
        mix = card.signature_mix
        if mix is None:
            out.append(f"  CPT {card.code}: no signature computed")
            continue
        out.append(
            f"  CPT {card.code}   M1 {mix.m1_share * 100:.0f}%  M2 {mix.m2_share * 100:.0f}%  "
            f"M3 {mix.m3_share * 100:.0f}%   dominant lever: {mix.dominant_lever}"
        )
        out.append(f"    {card.headline}")
    return out


def _over_time_lines(c: ClassRun) -> list[str]:
    """The "Over time" section (decision 7, CONTRACT-SEEDS.md): omitted
    entirely for a class whose trends never span more than one period -- a
    single-period run has nothing to show here, and an empty shell reads as
    a bug rather than as "nothing happened yet"."""
    if not any(len(series.points) > 1 for series in c.trends):
        return []
    out = [
        "",
        "Over time — ratio by period",
        "-" * 78,
    ]
    for series in c.trends:
        if len(series.points) <= 1:
            continue
        out.append(f"  CPT {series.code}   granularity: {series.granularity}")
        for point in series.points:
            if point.suppressed:
                out.append(
                    f"    {point.period:<9} n={point.n:<4} withheld (n < {SUPPRESSION_THRESHOLD})"
                )
                continue
            ratio_s = str(point.ratio) if point.ratio is not None else "n/a"
            ci = point.interval.render(4) if point.interval is not None else "n/a"
            out.append(f"    {point.period:<9} n={point.n:<4} ratio={ratio_s:<6} 95% CI {ci}")
        if series.policy_date is not None:
            pre_n = series.pre.n if series.pre is not None else 0
            post_n = series.post.n if series.post is not None else 0
            pre_s = str(series.pre.ratio) if series.pre and series.pre.ratio is not None else "n/a"
            post_s = (
                str(series.post.ratio) if series.post and series.post.ratio is not None else "n/a"
            )
            out.append(
                f"    policy date {series.policy_date.isoformat()}:  "
                f"pre  n={pre_n:<4} ratio={pre_s:<6}  "
                f"post n={post_n:<4} ratio={post_s}"
            )
    return out


def _witness_lines(r: Run) -> list[str]:
    w = r.witness
    out = [
        "",
        "Witness",
        "-" * 78,
        f"  scheme        {w.scheme}",
        f"  input hash    {w.input_hash}",
        f"  outputs hash  {w.outputs_hash}",
        f"  digest        {w.digest}",
    ]
    out.append(f"  signature     {w.signature if w.signature else '(none: no WORTH_WITNESS_KEY)'}")
    return out


def _encounter_detail_lines(a: Adequacy) -> list[str]:
    s = a.spine
    d = a.decomposition
    sig = a.signature
    out = [
        "",
        "Dollar spine",
        "-" * 78,
        f"  realized                       {s.realized:.2f}",
        f"  M1 missed / mismatched / no_code   "
        f"{s.m1_missed if s.m1_missed is not None else '-'} / "
        f"{s.m1_mismatched if s.m1_mismatched is not None else '-'} / "
        f"{s.m1_no_code if s.m1_no_code is not None else '-'}   "
        f"(unpriced flags: {s.m1_unpriced_count})",
        f"  M2 expected / adjustment       {s.m2_expected:.2f} / {s.m2_adjustment:.2f}",
        f"  M3 expected                    {s.m3_expected:.2f}",
        "",
        "Decomposition",
        "-" * 78,
        f"  m3 shortfall = {d.m3_shortfall:.2f}   compression = {d.compression:.2f}   "
        f"center mispricing = {d.center_mispricing:.2f}",
        "",
        f"Signature: {sig.pattern_id} — {sig.dominant}",
        "-" * 78,
        f"  M1 {sig.m1.dollars if sig.m1.dollars is not None else 'n/a'}  "
        f"M2 {sig.m2.dollars}  M3 {sig.m3.dollars}   "
        f"m2/m3 converge: {sig.m2_m3_converge}",
        "",
        "Method 1 flags",
        "-" * 78,
    ]
    if not a.method1_flags:
        out.append("  none")
    for f in a.method1_flags:
        priced_s = f"${f.priced:,.2f}" if f.priced is not None else "unpriced"
        out.append(
            f"  [{f.bucket:<10}] {f.statement[:60]!r}   rule={f.rule_id}   "
            f"strength={f.evidence_strength}   {priced_s}"
        )
    fr = a.payer_friction
    out += [
        "",
        "Payer friction",
        "-" * 78,
        f"  denied={fr.denied}  downcoded={fr.downcoded}  vehicle_existed={fr.vehicle_existed}",
        f"  carc={list(fr.carc)}  rarc={list(fr.rarc)}",
        "",
        "Witness",
        "-" * 78,
        f"  {a.witness.scheme}  digest {a.witness.digest}",
    ]
    return out


def cmd_encounter(r: Run, encounter_id: str) -> str:
    blocks: list[str] = []
    for obs in r.observations:
        if obs.scored.encounter.encounter_id != encounter_id:
            continue
        blocks.append(obs.scored.render())
        for a in r.adequacies:
            if a.encounter_id == encounter_id:
                blocks.append("")
                blocks.append(a.render())
                blocks.extend(_encounter_detail_lines(a))
        break
    if not blocks:
        return f"no such encounter in this run: {encounter_id}"
    return "\n".join(blocks)


def cmd_cases(r: Run) -> str:
    out = [
        f"  {'encounter':<12}{'code':<8}{'score':>6}{'minutes':>9}{'realized':>12}"
        f"{'ratio':>8}  payer"
    ]
    ratios = {a.encounter_id: a for a in r.adequacies}
    for obs in sorted(
        r.observations, key=lambda o: (o.scored.encounter.primary_cpt, o.score.value)
    ):
        if obs.cohort is not Cohort.STUDY:
            continue
        enc = obs.scored.encounter
        minutes = next(m.value for m in obs.scored.markers if m.marker_id == "operative_minutes")
        a = ratios.get(enc.encounter_id)
        ratio = f"{a.ratio.value:.2f}" if a else "n/a"
        out.append(
            f"  {enc.encounter_id:<12}{enc.primary_cpt:<8}{obs.score.value:>6}{minutes:>9}"
            f"{obs.realized:>12,.2f}{ratio:>8}  {obs.payer_name[:28]}"
        )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="worth-complexity", description=__doc__)
    parser.add_argument("command", choices=["demo", "report", "cases", "encounter"])
    parser.add_argument("argument", nargs="?")
    parser.add_argument("--clinical", type=Path, default=FIXTURE / "clinical")
    parser.add_argument("--remittance", type=Path, default=FIXTURE / "remittance")
    parser.add_argument("--filter", choices=sorted(FILTERS), default="layer-a")
    parser.add_argument(
        "--locality",
        default=FIXTURE_LOCALITY,
        help="the institution's Medicare locality, e.g. NY01 (default: the synthetic dataset's)",
    )
    parser.add_argument(
        "--setting",
        dest="setting",
        action="append",
        default=None,
        metavar="[CLASS=]VALUE",
        help="place of service: a bare value (facility or non-facility) applies to "
        "every class; CLASS=VALUE (e.g. visit=non-facility) overrides one class only; "
        "repeatable (default: the packaged default per class — facility for surgical, "
        "non-facility for visit and episode)",
    )
    parser.add_argument(
        "--reference",
        help="the CMS release the schedule curve is fitted at, as 2026Q4 or a date "
        "(default: the release in force on the latest service date)",
    )
    parser.add_argument(
        "--pack",
        dest="pack",
        action="append",
        default=None,
        metavar="NAME_OR_PATH",
        help="the rule pack: a name (e.g. surgical-v1) resolved against "
        "--rulepack-dir, WORTH_RULEPACK_DIR and the packaged rule packs, or "
        "a path to a pack file, loaded directly as external. Repeatable: each "
        "pack given is applied to the class it declares (default: the packaged "
        "pack for each class present — surgical-v1, visit-em-v1, episode-rpm-v1)",
    )
    parser.add_argument(
        "--rulepack-dir",
        dest="rulepack_dir",
        action="append",
        default=None,
        metavar="DIR",
        help="a directory to search for --pack by name, before "
        "WORTH_RULEPACK_DIR and the packaged rule packs (repeatable)",
    )
    args = parser.parse_args(argv)

    search = [Path(d) for d in args.rulepack_dir] if args.rulepack_dir else None
    names: list[str] = []
    paths: list[Path] = []
    for value in args.pack or ():
        candidate = Path(value)
        if candidate.is_file():
            paths.append(candidate)
        else:
            names.append(value)
    # ``None`` (unset) lets ``run`` pick the packaged pack for each class
    # present (decision 3, CONTRACT-PACKS.md / CONTRACT-PACKS-MC.md).
    pack_name = names or None
    pack_path = paths or None

    setting: str | dict[str, str] | None = None
    if args.setting:
        mapping: dict[str, str] = {}
        bare: str | None = None
        for value in args.setting:
            if "=" in value:
                cls, _, val = value.partition("=")
                mapping[cls] = val
            else:
                bare = value
        if mapping:
            if bare is not None:
                mapping["*"] = bare
            setting = mapping
        else:
            setting = bare

    r = run(
        args.clinical,
        args.remittance,
        locality=args.locality,
        setting=setting,
        reference=parse_when(args.reference) if args.reference else None,
        pack_name=pack_name,
        pack_path=pack_path,
        pack_search=search,
        provenance_filter=FILTERS[args.filter],
    )

    if args.command in {"demo", "report"}:
        print(cmd_report(r))
    elif args.command == "cases":
        print(cmd_cases(r))
    elif args.command == "encounter":
        if not args.argument:
            parser.error("encounter requires an encounter id")
        print(cmd_encounter(r, args.argument))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
