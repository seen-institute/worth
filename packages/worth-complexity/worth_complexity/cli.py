"""Command line for worth-complexity."""

from __future__ import annotations

import argparse
from pathlib import Path

from worth_fees import PlaceOfService
from worth_fees.cli import parse_when

from worth_complexity.models import (
    LAYER_A,
    LAYER_A_STRUCTURED_ONLY,
    SUPPRESSION_THRESHOLD,
    Cohort,
)
from worth_complexity.pipeline import Run, run

FIXTURE = Path(__file__).parent / "fixtures" / "mssm-synthetic"
FIXTURE_LOCALITY = "NY01"
"""Manhattan: where the synthetic institution is. The extract does not carry a
locality, so the CLI defaults to the synthetic dataset's; a real extract needs
``--locality`` set to the partner's."""

FILTERS = {
    "structured": LAYER_A_STRUCTURED_ONLY,
    "layer-a": LAYER_A,
}


def _banner(r: Run) -> list[str]:
    lines = [
        f"rule pack   {r.pack.rule_pack_id} v{r.pack.version} "
        f"[{r.pack.digest[:12]}]  status: {r.pack.status}",
        f"filter      {'+'.join(r.provenance_filter)}",
        f"priced in   {r.locality} {r.locality_name}, {r.setting.value}; "
        f"schedule curve at CMS {r.reference.label}",
        f"linkage     {r.linkage.render()}",
        f"cohort      {len(r.study)} study encounters, {len(r.comparator)} comparator",
    ]
    if r.pack.is_provisional:
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
    """
    out = ["WORTH: Layer A complexity and payment adequacy", "=" * 78, *_banner(r), ""]

    out += [
        "DEFENSIBLE FROM THE FIRST VALIDATED EXTRACT",
        "=" * 78,
        "  No reference standard, no curve, nothing normative.",
        "",
        "Empirical reference distribution: Layer A complexity per code",
        "-" * 78,
        f"  {'code':<8}{'n':>5}{'median':>9}{'IQR':>14}{'range':>14}",
    ]
    for rec in r.records:
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
    for rec in r.records:
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
        f"  {r.schedule_curve.render()}",
        f"  Medicare PFS dollars at CMS {r.reference.label}, {r.locality} {r.setting.value}, "
        f"archive sha256 {r.reference.archive_sha256[:12]}...",
        "",
        "Payer multiples of the schedule, from each payer's own comparator encounters",
        "-" * 78,
        "  A contract level. This section stays with the partner and never enters a",
        "  published record.",
    ]
    out.extend(f"  {m.render()}" for m in r.multipliers.values())
    out.extend(f"  {reason}" for reason in r.thin_payers)
    out.append("")

    out += [
        "Payment adequacy index: per code, per institution, per period",
        "-" * 78,
        f"  {'code':<8}{'n':>5}{'scored':>8}{'ratio':>8}{'95% CI':>22}{'excluded':>10}",
    ]
    for rec in r.records:
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

    denied = [le for le in r.linkage.linked if le.denied_lines]
    if denied:
        out += ["", f"  Denial burden: {len(denied)} encounter(s) carry a line allowed at zero."]
    return "\n".join(out)


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
        choices=[p.value for p in PlaceOfService],
        default=PlaceOfService.FACILITY.value,
    )
    parser.add_argument(
        "--reference",
        help="the CMS release the schedule curve is fitted at, as 2026Q4 or a date "
        "(default: the release in force on the latest service date)",
    )
    args = parser.parse_args(argv)

    r = run(
        args.clinical,
        args.remittance,
        locality=args.locality,
        setting=args.setting,
        reference=parse_when(args.reference) if args.reference else None,
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
