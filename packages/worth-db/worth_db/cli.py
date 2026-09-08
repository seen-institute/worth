"""Command line for worth-db: migrate the schema, load vintages, check status.

The database URL never appears in output: every message prints
:func:`database_label`, the host, port and database name with the
credentials stripped, the same shape ``worth-api``'s ``/api/health`` used to
report.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from worth_fees.cli import parse_when
from worth_fees.sources import PINNED_VINTAGES, Vintage, cache_dir, load_from_archive

from worth_db import db
from worth_db.schedule import best_local

if TYPE_CHECKING:
    from worth_fees.sources import FeeSchedule

DATABASE_URL_ENV = "DATABASE_URL"


def database_label(url: str) -> str:
    """The database without its credentials, for status output and logs."""
    parts = urlsplit(url)
    host = parts.hostname or "?"
    port = f":{parts.port}" if parts.port else ""
    return f"{host}{port}{parts.path}"


def _resolve_url(args: argparse.Namespace) -> str:
    url: str | None = getattr(args, "url", None) or os.environ.get(DATABASE_URL_ENV)
    if not url:
        print(
            f"no database configured: pass --url or set {DATABASE_URL_ENV}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return url


def _schedule_for_load(vintage: Vintage, *, full: bool) -> FeeSchedule:
    if not full:
        return best_local(vintage)
    if not (cache_dir() / vintage.archive_filename).exists():
        print(
            f"{vintage.label}: --full requires the CMS archive in the cache "
            f"({cache_dir() / vintage.archive_filename}). Run "
            "`worth-fees build-fixture` (needs network) to fetch it, or drop --full "
            "to fall back to the committed fixture.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return load_from_archive(vintage.rule_year, vintage.quarter)


def _vintage_keys(spec: str | None) -> list[tuple[int, int]]:
    if spec is None:
        return sorted(PINNED_VINTAGES)
    key = parse_when(spec)
    if key not in PINNED_VINTAGES:
        available = ", ".join(v.label for _, v in sorted(PINNED_VINTAGES.items()))
        print(f"{spec!r} is not a pinned vintage. Pinned: {available}", file=sys.stderr)
        raise SystemExit(2)
    return [key]


def _migrate(args: argparse.Namespace) -> int:
    url = _resolve_url(args)
    with db.connect(url) as conn:
        applied = db.ensure_schema(conn)
    print(f"{database_label(url)}: {'schema applied' if applied else 'schema already present'}")
    return 0


def _load(args: argparse.Namespace) -> int:
    url = _resolve_url(args)
    keys = _vintage_keys(args.vintage)
    with db.connect(url) as conn:
        db.ensure_schema(conn)
        for year, quarter in keys:
            vintage = PINNED_VINTAGES[(year, quarter)]
            schedule = _schedule_for_load(vintage, full=args.full)
            action = db.ensure_loaded(conn, schedule)
            print(f"{vintage.label}: {action} ({db.coverage_of(schedule)})")
    return 0


def _status(args: argparse.Namespace) -> int:
    url = _resolve_url(args)
    print(database_label(url))
    with db.connect(url) as conn:
        db.ensure_schema(conn)
        releases = db.releases(conn)
        print(f"{'vintage':<10}{'loaded':<9}{'coverage':<10}{'rvu rows':>10}  localities")
        print("-" * 55)
        for key, vintage in sorted(PINNED_VINTAGES.items()):
            release = releases.get(key)
            if release is None:
                print(f"{vintage.label:<10}{'no':<9}")
                continue
            print(
                f"{vintage.label:<10}{'yes':<9}{release.coverage:<10}"
                f"{release.rvu_rows:>10}  {release.localities}"
            )
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--url",
        # SUPPRESS, not None: both the top-level parser and each subparser
        # carry their own copy of --url (so it works before or after the
        # subcommand), and argparse re-applies a subparser's own defaults
        # over whatever the top-level parser already set. A default of
        # None would silently clobber "--url X migrate" back to None;
        # SUPPRESS means "didn't provide it here" instead of "provide None
        # here", so the earlier value survives.
        default=argparse.SUPPRESS,
        help=f"Postgres URL; defaults to the {DATABASE_URL_ENV} environment variable",
    )

    parser = argparse.ArgumentParser(prog="worth-db", description=__doc__, parents=[common])
    subcommands = parser.add_subparsers(dest="command", required=True)

    migrate = subcommands.add_parser(
        "migrate", help="apply the fee-schedule schema if it is missing", parents=[common]
    )
    migrate.set_defaults(func=_migrate)

    load = subcommands.add_parser(
        "load", help="load one or every pinned CMS vintage", parents=[common]
    )
    load.add_argument(
        "--vintage", help="one pinned vintage, e.g. 2026Q1 (default: every pinned vintage)"
    )
    load.add_argument(
        "--full",
        action="store_true",
        help="require the CMS archive in the cache; refuse rather than fall back to the fixture",
    )
    load.set_defaults(func=_load)

    status = subcommands.add_parser(
        "status", help="what is loaded, and how, per pinned vintage", parents=[common]
    )
    status.set_defaults(func=_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
