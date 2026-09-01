"""Command line entry point for worth-fees.

``worth-fees demo`` is the thesis of the project in miniature: a code, a
locality, an allowed amount, the arithmetic that produced it, and the sha256 of
the CMS file it came from -- offline, on a clean machine.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date

from worth_fees.fees import expected_allowed
from worth_fees.models import FeeDerivation, PlaceOfService, VintageError, WorthFeesError
from worth_fees.money import usd
from worth_fees.sources import (
    PINNED_VINTAGES,
    Vintage,
    build_fixture,
    load,
    load_from_archive,
    vintage_for_date,
)
from worth_fees.sql import export

# The sample committed to the repository. A spread wide enough to exercise the
# facility/non-facility split, the 26/TC component split, and the NA indicator,
# and small enough that the fixture stays reviewable by eye.
FIXTURE_CODES = ["99213", "99214", "99232", "71046", "93000", "20610", "29881"]
FIXTURE_LOCALITIES = ["CA18"]

# Cases printed by `verify-rates` for checking against the CMS Physician Fee
# Schedule Look-Up Tool. These are inputs only; no expected value is asserted
# here or anywhere else until a human has confirmed it against CMS.
VERIFICATION_CASES: list[tuple[str, list[str], str, PlaceOfService]] = [
    ("99213", [], "CA18", PlaceOfService.NON_FACILITY),
    ("99213", [], "CA18", PlaceOfService.FACILITY),
    ("99214", [], "CA18", PlaceOfService.NON_FACILITY),
    ("99232", [], "CA18", PlaceOfService.FACILITY),
    ("20610", [], "CA18", PlaceOfService.NON_FACILITY),
    ("71046", ["26"], "CA18", PlaceOfService.FACILITY),
    ("71046", ["TC"], "CA18", PlaceOfService.NON_FACILITY),
    ("93000", [], "CA18", PlaceOfService.NON_FACILITY),
    ("29881", [], "CA18", PlaceOfService.FACILITY),
]

_LOOKUP_TOOL = "https://www.cms.gov/medicare/physician-fee-schedule/search"


def _demo(args: argparse.Namespace) -> int:
    derivation = expected_allowed(
        args.code,
        args.modifier,
        args.locality,
        PlaceOfService(args.place_of_service),
        args.year,
        args.quarter,
    )
    print(derivation.render())
    return 0


def _verify_rates(args: argparse.Namespace) -> int:
    """Print a worksheet for checking our amounts against CMS by hand."""
    rule_year, quarter = parse_when(args.date)
    schedule = load(rule_year, quarter)

    print("Validating worth-fees against the CMS Physician Fee Schedule Look-Up Tool")
    print(f"  {_LOOKUP_TOOL}\n")
    print("The tool opens with an AMA CPT licence click-through; accept it to continue.")
    print("Then set, for each row below:\n")
    print(f"  Year ..................  {rule_year}")
    print("  Type of information ...  Pricing Information")
    print("  HCPCS criteria ........  Single HCPCS code  ->  the 'code' column")
    print("  Modifier ..............  the 'mod' column (blank = All Modifiers)")
    print("  MAC / Locality ........  the 'MAC' and 'loc' columns\n")
    print("Compare CMS's 'Non-Facility Price' / 'Facility Price' with ours, then")
    print("write the CMS figure in the last column.\n")

    header = (
        f"  {'code':<7} {'mod':<4} {'MAC':<7} {'loc':<4} {'setting':<13} "
        f"{'worth-fees':>11}   {'CMS says':<10}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))

    for code, modifiers, locality, setting in VERIFICATION_CASES:
        try:
            derivation = expected_allowed(code, modifiers, locality, setting, rule_year, quarter)
        except WorthFeesError as exc:
            print(
                f"  {code:<7} {','.join(modifiers):<4} {'':<7} {locality:<4} "
                f"{setting.value:<13} {'--':>11}   (not priced: {exc})"
            )
            continue
        gpci = schedule.gpcis[derivation.locality]
        print(
            f"  {code:<7} {','.join(modifiers):<4} {gpci.mac:<7} {gpci.locality:<4} "
            f"{setting.value:<13} {usd(derivation.amount):>11}   __________"
        )

    print(
        "\nEvery row that matches goes into VERIFIED_CMS_RATES in\n"
        "  packages/worth-fees/tests/test_published_rates.py\n"
        "which turns the skipped correctness test on. Record who checked, and when,\n"
        "in the commit message. A value that does not match is a bug worth finding\n"
        "before anything else in this repository is trusted."
    )
    return 0


def _build_fixture(args: argparse.Namespace) -> int:
    manifest = build_fixture(args.year, args.quarter, FIXTURE_CODES, FIXTURE_LOCALITIES)
    print(f"wrote {manifest.parent}")
    print(f"  codes:      {', '.join(FIXTURE_CODES)}")
    print(f"  localities: {', '.join(FIXTURE_LOCALITIES)}")
    print("CPT descriptors stripped on ingest.")
    return 0


def _export_sql(args: argparse.Namespace) -> int:
    schedule = (
        load_from_archive(args.year, args.quarter) if args.full else load(args.year, args.quarter)
    )
    sys.stdout.write(export(schedule))
    return 0


def parse_when(text: str) -> tuple[int, int]:
    """Resolve a user-supplied time into a (rule_year, quarter) pair.

    Accepts a service date (``2026-03-14``), an explicit quarter (``2026Q2``),
    a bare year (``2026``, meaning Q1), or ``today``. A service date is the
    form to prefer: the rate that applies to a claim is the one in force on the
    day the service happened, not the newest one published.
    """
    raw = text.strip().lower()
    if raw == "today":
        return _year_quarter(vintage_for_date(date.today()))
    if "-" in raw:
        try:
            return _year_quarter(vintage_for_date(date.fromisoformat(raw)))
        except ValueError:
            raise VintageError(f"{text!r} is not a date in YYYY-MM-DD form") from None
    if "q" in raw:
        year, _, quarter = raw.partition("q")
        if year.isdigit() and quarter.isdigit():
            return int(year), int(quarter)
    if raw.isdigit() and len(raw) == 4:
        return int(raw), 1
    raise VintageError(
        f"cannot read {text!r} as a time. Use a service date (2026-03-14), "
        "a quarter (2026Q2), a year (2026, meaning Q1), or 'today'."
    )


def _year_quarter(vintage: Vintage) -> tuple[int, int]:
    return vintage.rule_year, vintage.quarter


def _price(args: argparse.Namespace) -> int:
    rule_year, quarter = parse_when(args.date)
    setting = PlaceOfService.FACILITY if args.facility else PlaceOfService.NON_FACILITY
    derivation = expected_allowed(args.code, args.modifier, args.place, setting, rule_year, quarter)
    if args.amount:
        print(derivation.amount)
    elif args.json:
        print(json.dumps(_as_dict(derivation), indent=2))
    else:
        print(derivation.render())
    return 0


def _as_dict(d: FeeDerivation) -> dict[str, object]:
    return {
        "amount": str(d.amount),
        "currency": "USD",
        "code": d.code,
        "modifier": d.modifier,
        "modifiers": list(d.modifiers),
        "locality": d.locality,
        "locality_name": d.locality_name,
        "place_of_service": d.place_of_service.value,
        "rule_year": d.rule_year,
        "quarter": d.quarter,
        "inputs": {
            "work_rvu": str(d.work_rvu),
            "pe_rvu": str(d.pe_rvu),
            "mp_rvu": str(d.mp_rvu),
            "work_gpci": str(d.work_gpci),
            "pe_gpci": str(d.pe_gpci),
            "mp_gpci": str(d.mp_gpci),
            "conversion_factor": str(d.conversion_factor),
        },
        "adjusted_rvu_total": str(d.adjusted_rvu_total),
        "trace": list(d.trace),
        "source": {
            "release": d.source.release,
            "release_date": d.source.release_date.isoformat(),
            "files": [
                {"filename": f.filename, "sha256": f.sha256, "role": f.role}
                for entry in d.source.files
                for f in entry.chain()
            ],
        },
    }


def _localities(args: argparse.Namespace) -> int:
    rule_year, quarter = parse_when(args.date)
    schedule = load_from_archive(rule_year, quarter) if args.full else load(rule_year, quarter)
    scope = "all localities in the CMS file" if args.full else "localities in the committed fixture"
    print(f"{schedule.vintage.label} — {scope}\n")
    print(f"{'place':<7} {'MAC':<7} {'work':>6} {'PE':>6} {'MP':>6}  name")
    print("-" * 78)
    for key in sorted(schedule.gpcis):
        g = schedule.gpcis[key]
        print(f"{g.key:<7} {g.mac:<7} {g.work_gpci:>6} {g.pe_gpci:>6} {g.mp_gpci:>6}  {g.name}")
    if not args.full:
        print("\nAdd --full to list all 109 (needs the CMS download).")
    return 0


def _vintages(_: argparse.Namespace) -> int:
    print(f"{'date':<8} {'release':<9} {'service dates it governs':<26} {'published':<12}")
    print("-" * 60)
    for (year, quarter), vintage in sorted(PINNED_VINTAGES.items()):
        start, end = vintage.effective
        print(
            f"{year}Q{quarter:<5} {vintage.label:<9} "
            f"{start.isoformat()} .. {end.isoformat():<11} {vintage.release_date.isoformat()}"
        )
    print("\nPass any of the left column, or a service date inside a range, as <date>.")
    return 0


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--year", type=int, default=2026, help="fee schedule calendar year")
    parser.add_argument("--quarter", type=int, default=1, help="quarterly release (1-4)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="worth-fees", description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    demo = subcommands.add_parser("demo", help="derive one allowed amount, with full trace")
    demo.add_argument("code", nargs="?", default="99213", help="HCPCS/CPT code")
    demo.add_argument("--locality", default="CA18", help="Medicare locality, e.g. CA18")
    demo.add_argument(
        "--place-of-service",
        default=PlaceOfService.NON_FACILITY.value,
        choices=[p.value for p in PlaceOfService],
    )
    demo.add_argument(
        "--modifier", action="append", default=[], help="repeatable, e.g. --modifier 26"
    )
    _add_common(demo)
    demo.set_defaults(func=_demo)

    price = subcommands.add_parser(
        "price",
        help="price one code: worth-fees price <code> <place> <date>",
        description="Price a procedure: worth-fees price 99213 CA18 2026-03-14",
    )
    price.add_argument("code", help="HCPCS/CPT code, e.g. 99213")
    price.add_argument("place", help="Medicare locality, e.g. CA18 (see `worth-fees localities`)")
    price.add_argument(
        "date",
        help="service date 2026-03-14, quarter 2026Q2, year 2026, or 'today' "
        "(see `worth-fees vintages`)",
    )
    price.add_argument(
        "--facility",
        action="store_true",
        help="price the facility setting (hospital). Default is non-facility (office).",
    )
    price.add_argument("--modifier", action="append", default=[], help="repeatable, e.g. 26 or TC")
    output = price.add_mutually_exclusive_group()
    output.add_argument("--amount", action="store_true", help="print only the dollar amount")
    output.add_argument("--json", action="store_true", help="print the full derivation as JSON")
    price.set_defaults(func=_price)

    localities = subcommands.add_parser("localities", help="list valid <place> values")
    localities.add_argument("date", nargs="?", default="2026Q1", help="which release to list")
    localities.add_argument(
        "--full", action="store_true", help="all 109 from the CMS file, not just the fixture"
    )
    localities.set_defaults(func=_localities)

    verify = subcommands.add_parser(
        "verify-rates", help="worksheet for checking our amounts against CMS by hand"
    )
    verify.add_argument("date", nargs="?", default="2026Q1", help="which release to check")
    verify.set_defaults(func=_verify_rates)

    fixture = subcommands.add_parser(
        "build-fixture", help="download CMS data and rebuild the committed fixtures (online)"
    )
    _add_common(fixture)
    fixture.set_defaults(func=_build_fixture)

    export_sql = subcommands.add_parser(
        "export-sql", help="emit a Postgres load script for a vintage"
    )
    export_sql.add_argument(
        "--full",
        action="store_true",
        help="load every row from the cached CMS archive instead of the committed fixture "
        "(requires `build-fixture` or a prior download)",
    )
    _add_common(export_sql)
    export_sql.set_defaults(func=_export_sql)

    vintages = subcommands.add_parser("vintages", help="list pinned CMS vintages")
    vintages.set_defaults(func=_vintages)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result: int = args.func(args)
    except WorthFeesError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return result


if __name__ == "__main__":
    raise SystemExit(main())
