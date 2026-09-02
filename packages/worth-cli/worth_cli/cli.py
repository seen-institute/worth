"""Command line for worth-cli, the one command a user or Meridian invokes.

Four subcommands:

* ``worth-cli run`` runs the ``worth-complexity`` pipeline on a dataset (the
  packaged synthetic fixture by default) and prints the same report
  ``worth-complexity report`` does, or the same data as JSON with ``--json``.
* ``worth-cli price ...`` and ``worth-cli db ...`` are thin forwarders: they
  hand their remaining arguments straight to ``worth_fees.cli.main`` and
  ``worth_db.cli.main``, so ``worth-cli price 99213 CA18 2026-03-14`` and
  ``worth-cli db migrate`` behave exactly as the package's own command would,
  including ``--help``.
* ``worth-cli version`` prints all four package versions.

Standard library only in this module and in :mod:`worth_cli.serialize`; the
work is entirely delegation to the other three packages.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from worth_complexity.cli import FILTERS, FIXTURE, FIXTURE_LOCALITY, cmd_report
from worth_complexity.pipeline import run
from worth_complexity.version import __version__ as worth_complexity_version
from worth_db import __version__ as worth_db_version
from worth_db.cli import main as db_main
from worth_fees import PlaceOfService
from worth_fees import __version__ as worth_fees_version
from worth_fees.cli import main as fees_main
from worth_fees.cli import parse_when

from worth_cli import __version__ as worth_cli_version
from worth_cli.serialize import to_jsonable

if TYPE_CHECKING:
    from worth_complexity.pipeline import Run

_VERSIONS = {
    "worth-cli": worth_cli_version,
    "worth-fees": worth_fees_version,
    "worth-complexity": worth_complexity_version,
    "worth-db": worth_db_version,
}


def run_document(r: Run) -> dict[str, object]:
    """The JSON document ``worth-cli run --json`` prints.

    ``run`` is the package's own :class:`~worth_complexity.pipeline.Run`,
    converted field by field with :func:`worth_cli.serialize.to_jsonable`:
    the "raw package output", not a view rebuilt for presentation. The
    version keys pin exactly which release of each package produced it.
    """
    return {
        "worth_cli_version": worth_cli_version,
        "worth_complexity_version": worth_complexity_version,
        "worth_fees_version": worth_fees_version,
        "run": to_jsonable(r),
    }


def _cmd_run(args: argparse.Namespace) -> int:
    r = run(
        args.clinical,
        args.remittance,
        locality=args.locality,
        setting=args.setting,
        reference=parse_when(args.reference) if args.reference else None,
        provenance_filter=FILTERS[args.filter],
    )
    if args.json:
        print(json.dumps(run_document(r), indent=2, allow_nan=False))
    else:
        print(cmd_report(r))
    return 0


def _cmd_version(_: argparse.Namespace) -> int:
    for name, version in _VERSIONS.items():
        print(f"{name} {version}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The parser for ``run`` and ``version``.

    ``price`` and ``db`` are handled before this parser ever sees the
    arguments, see :func:`main`; they are named here only so ``worth-cli
    --help`` lists all four subcommands.
    """
    parser = argparse.ArgumentParser(
        prog="worth-cli",
        description=__doc__,
        epilog="`worth-cli price ...` and `worth-cli db ...` forward to the "
        "worth-fees and worth-db commands of the same name; see `worth-cli "
        "price --help` and `worth-cli db --help`.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    run_parser = subcommands.add_parser(
        "run", help="run the worth-complexity pipeline and print its report"
    )
    run_parser.add_argument("--clinical", type=Path, default=FIXTURE / "clinical")
    run_parser.add_argument("--remittance", type=Path, default=FIXTURE / "remittance")
    run_parser.add_argument(
        "--locality",
        default=FIXTURE_LOCALITY,
        help="the institution's Medicare locality, e.g. NY01 (default: the synthetic dataset's)",
    )
    run_parser.add_argument(
        "--setting",
        choices=[p.value for p in PlaceOfService],
        default=PlaceOfService.FACILITY.value,
    )
    run_parser.add_argument(
        "--reference",
        help="the CMS release the schedule curve is fitted at, as 2026Q4 or a date "
        "(default: the release in force on the latest service date)",
    )
    run_parser.add_argument("--filter", choices=sorted(FILTERS), default="layer-a")
    run_parser.add_argument(
        "--json", action="store_true", help="print the run as JSON instead of the text report"
    )
    run_parser.set_defaults(func=_cmd_run)

    # `price` and `db` are listed here only so `worth-cli --help` names all
    # four subcommands; `main` intercepts and forwards both before this
    # parser ever parses their arguments, see below.
    subcommands.add_parser(
        "price", help="price one code (forwards to worth-fees; see `worth-cli price --help`)"
    )
    subcommands.add_parser(
        "db", help="the fee-schedule database (forwards to worth-db; see `worth-cli db --help`)"
    )

    version_parser = subcommands.add_parser("version", help="print all four package versions")
    version_parser.set_defaults(func=_cmd_version)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)

    # `price` and `db` are pure forwarders: everything after the subcommand
    # name goes straight to the other package's own `main`, so its own
    # argument handling, error messages and `--help` are exactly what a
    # caller of that package directly would see. worth-fees names its own
    # subcommand `price`, so that token is kept; worth-db has no `db`
    # subcommand of its own (`migrate`/`load`/`status` are top-level), so
    # that token is dropped.
    if raw and raw[0] == "price":
        return fees_main(raw)
    if raw and raw[0] == "db":
        return db_main(raw[1:])

    args = build_parser().parse_args(raw)
    result: int = args.func(args)
    return result


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
