"""Command line for worth-cli, the one command a user or Meridian invokes.

Subcommands:

* ``worth-cli run`` runs the ``worth-complexity`` pipeline on a dataset (the
  packaged synthetic fixture by default) and prints the same report
  ``worth-complexity report`` does, or the same data as JSON with ``--json``.
* ``worth-cli score [ID ...]``, ``explain ID``, ``slope CODE``,
  ``code CODE [--by ...]``, ``compare [--codes|--domains]``,
  ``queue [--surgeon ...] [--site ...] [--payer ...] [--bucket ...]`` and
  ``trend [CODE] [--policy-date YYYY-MM-DD] [--by payer]`` are the W3/S2
  views onto a run: each accepts the same dataset flags as ``run`` (plus
  ``--claims``), and each of ``--json`` (default; newline-delimited for a
  batch ``score``), ``--table`` and ``--csv``. ``trend`` is the over-time
  view (decision 7, CONTRACT-SEEDS.md): every code's ratio by month (or, with
  ``--by payer``, by month within each payer), with ``--policy-date`` also
  reporting the pre/post split baked into the run.
* Every one of the above also accepts ``--pack NAME_OR_PATH`` (a name
  resolved the normal way, or a path loaded directly as an external pack)
  and repeatable ``--rulepack-dir DIR`` (searched before
  ``WORTH_RULEPACK_DIR`` and the packaged rule packs). ``worth-cli packs``
  lists what layer 2 can see: packaged rule packs first, then external ones,
  as a table or with ``--json``.
* ``worth-cli price ...`` and ``worth-cli db ...`` are thin forwarders: they
  hand their remaining arguments straight to ``worth_fees.cli.main`` and
  ``worth_db.cli.main``, so ``worth-cli price 99213 CA18 2026-03-14`` and
  ``worth-cli db migrate`` behave exactly as the package's own command would,
  including ``--help``.
* ``worth-cli version`` prints all four package versions.
* ``worth-cli synth --scenario S --class C [--seed N] [--scale K] DIR``
  generates one named scenario dataset (CONTRACT-SEEDS.md's seed suite) into
  ``DIR`` and prints ``DIR`` followed by its ``truth.json``; ``--class`` is
  one of ``surgical``, ``visit``, ``episode``, ``mixed``. ``worth-cli synth
  --list`` prints the scenario catalog instead (name, group, description)
  and exits; every other flag is ignored when ``--list`` is given.

Standard library only in this module and in :mod:`worth_cli.serialize`; the
work is entirely delegation to the other three packages.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from worth_complexity.cli import FILTERS, FIXTURE, FIXTURE_LOCALITY, cmd_encounter, cmd_report
from worth_complexity.models import Cohort, ratio_of
from worth_complexity.money import money_context
from worth_complexity.pipeline import CLASS_ORDER, run
from worth_complexity.population import domain_rows
from worth_complexity.rulepack import PackInfo, available
from worth_complexity.version import __version__ as worth_complexity_version
from worth_db import __version__ as worth_db_version
from worth_db.cli import main as db_main
from worth_fees import __version__ as worth_fees_version
from worth_fees.cli import main as fees_main
from worth_fees.cli import parse_when

from worth_cli import __version__ as worth_cli_version
from worth_cli.serialize import to_jsonable

if TYPE_CHECKING:
    from worth_complexity.adequacy import Observation
    from worth_complexity.models import Adequacy
    from worth_complexity.pipeline import Run
    from worth_complexity.population import (
        CodeCard,
        CompareRow,
        PeriodPoint,
        PooledSlope,
        QueueItem,
    )

_VERSIONS = {
    "worth-cli": worth_cli_version,
    "worth-fees": worth_fees_version,
    "worth-complexity": worth_complexity_version,
    "worth-db": worth_db_version,
}


_RUN_FLATTENED_FIELDS = (
    "observations",
    "adequacies",
    "records",
    "cards",
    "compare",
    "queue",
    "provenance_filter",
    "trends",
)
"""Properties that flatten across every class present, always included in
``run_document`` (decision 6, CONTRACT-PACKS-MC.md): ``Run`` carries these
as computed properties, not dataclass fields, so plain
``to_jsonable(r)`` -- which walks ``dataclasses.fields`` -- never sees them
on its own."""

_RUN_SINGLE_CLASS_FIELDS = (
    "pack",
    "rulebook_version",
    "weights_version",
    "setting",
    "schedule_curve",
    "priced",
    "multipliers",
    "thin_payers",
    "slopes",
    "thin_strata",
    "extrapolated",
    "unmultiplied",
    "unbanded",
)
"""Properties ``Run`` exposes only when exactly one class is present (they
raise on a genuinely mixed run, see ``pipeline.Run._only_class``); included
in ``run_document`` only then, so a mixed run's JSON never trips the same
raise ``r.pack`` would."""


def run_document(r: Run) -> dict[str, object]:
    """The JSON document ``worth-cli run --json`` prints.

    ``run`` is the package's own :class:`~worth_complexity.pipeline.Run`,
    converted field by field with :func:`worth_cli.serialize.to_jsonable`
    for its real dataclass fields (``classes[]`` among them), plus every
    flattened view :data:`_RUN_FLATTENED_FIELDS` names, plus (single-class
    runs only) :data:`_RUN_SINGLE_CLASS_FIELDS` — the "raw package output"
    a caller who only ever saw a single-class ``Run`` still gets in full,
    not a view rebuilt for presentation.
    """
    doc = to_jsonable(r)
    for name in _RUN_FLATTENED_FIELDS:
        doc[name] = to_jsonable(getattr(r, name))
    if len(r.classes) == 1:
        for name in _RUN_SINGLE_CLASS_FIELDS:
            doc[name] = to_jsonable(getattr(r, name))
    return {
        "worth_cli_version": worth_cli_version,
        "worth_complexity_version": worth_complexity_version,
        "worth_fees_version": worth_fees_version,
        "run": doc,
    }


def _versions_envelope(r: Run) -> dict[str, str]:
    """The keys every W3 subcommand's JSON document carries at top level.

    A single-class run reads exactly as before: ``r.rulebook_version``/
    ``r.pack`` and friends. A mixed run has no single rule pack, so these
    become one ``+``-joined value per class instead of raising (decision 6,
    CONTRACT-PACKS-MC.md: every W3 subcommand "works on a mixed dataset") —
    the per-class detail still lives in ``run.classes[]`` for a caller that
    needs it precisely.
    """
    if len(r.classes) == 1:
        return {
            "rulebook_version": r.rulebook_version,
            "weights_version": r.weights_version,
            "rule_pack_source": r.pack.source,
            "rule_pack_path": r.pack.source_path or "",
            "worth_cli_version": worth_cli_version,
            "worth_complexity_version": worth_complexity_version,
            "worth_fees_version": worth_fees_version,
        }
    return {
        "rulebook_version": "+".join(c.rulebook_version for c in r.classes),
        "weights_version": "+".join(c.weights_version for c in r.classes),
        "rule_pack_source": "+".join(sorted({c.pack.source for c in r.classes})),
        "rule_pack_path": "+".join(c.pack.source_path for c in r.classes if c.pack.source_path),
        "worth_cli_version": worth_cli_version,
        "worth_complexity_version": worth_complexity_version,
        "worth_fees_version": worth_fees_version,
    }


def _meta_comment(r: Run) -> str:
    """The line ``--table``'s first row and ``--csv``'s ``#`` comment share."""
    versions = _versions_envelope(r)
    return "# " + " ".join(f"{k}={v}" for k, v in versions.items())


# ---------------------------------------------------------------------------
# Shared argument setup
# ---------------------------------------------------------------------------


def _add_dataset_flags(p: argparse.ArgumentParser) -> None:
    """The dataset flags every view shares with ``run``."""
    p.add_argument("--clinical", type=Path, default=FIXTURE / "clinical")
    p.add_argument("--remittance", type=Path, default=FIXTURE / "remittance")
    p.add_argument(
        "--claims",
        type=Path,
        default=None,
        help="the 837 directory (default: <clinical's parent>/claims, if it exists)",
    )
    p.add_argument(
        "--locality",
        default=FIXTURE_LOCALITY,
        help="the institution's Medicare locality, e.g. NY01 (default: the synthetic dataset's)",
    )
    p.add_argument(
        "--setting",
        dest="setting",
        action="append",
        default=None,
        metavar="[CLASS=]VALUE",
        help="place of service: a bare value (facility or non-facility) applies to "
        "every class; CLASS=VALUE (e.g. visit=non-facility) overrides one class only; "
        "repeatable, so both forms can be combined (default: the packaged default per "
        "class — facility for surgical, non-facility for visit and episode)",
    )
    p.add_argument(
        "--reference",
        help="the CMS release the schedule curve is fitted at, as 2026Q4 or a date "
        "(default: the release in force on the latest service date)",
    )
    p.add_argument("--filter", choices=sorted(FILTERS), default="layer-a")
    p.add_argument(
        "--pack",
        dest="pack",
        action="append",
        default=None,
        metavar="NAME_OR_PATH",
        help="the rule pack: a name (e.g. surgical-v1) resolved against "
        "--rulepack-dir, WORTH_RULEPACK_DIR and the packaged rule packs, or "
        "a path to a pack file, loaded directly as external. Repeatable: on a "
        "mixed extract, each pack given is applied to the class it declares "
        "(decision 3, CONTRACT-PACKS-MC.md). Omitted entirely: the packaged "
        "pack for each class present — surgical-v1, visit-em-v1, episode-rpm-v1",
    )
    p.add_argument(
        "--rulepack-dir",
        dest="rulepack_dir",
        action="append",
        default=None,
        metavar="DIR",
        help="a directory to search for --pack by name, before "
        "WORTH_RULEPACK_DIR and the packaged rule packs (repeatable)",
    )


def _add_output_flags(p: argparse.ArgumentParser) -> None:
    group = p.add_mutually_exclusive_group()
    group.add_argument("--json", action="store_true", help="newline-delimited for a batch score")
    group.add_argument("--table", action="store_true", help="a plain-text column table")
    group.add_argument("--csv", action="store_true", help="CSV, with a leading version comment")


def _format_of(args: argparse.Namespace) -> str:
    if args.csv:
        return "csv"
    if args.table:
        return "table"
    return "json"


def _witness_key() -> bytes | None:
    """``WORTH_WITNESS_KEY``, read here rather than by the engine (decision 6)."""
    raw = os.environ.get("WORTH_WITNESS_KEY")
    return raw.encode("utf-8") if raw else None


def _pack_args(
    args: argparse.Namespace,
) -> tuple[list[str] | None, list[Path] | None, list[Path] | None]:
    """Turn repeatable ``--pack``/``--rulepack-dir`` into ``pipeline.run``'s
    pack arguments.

    Each ``--pack`` value that names an existing file is a path, loaded
    directly as external; anything else is a name, resolved the normal way
    against ``--rulepack-dir``. ``--pack`` omitted entirely passes ``None``
    for both so ``pipeline.run`` picks the packaged pack for each class
    present (decision 3, CONTRACT-PACKS.md / CONTRACT-PACKS-MC.md) rather
    than this CLI assuming surgical.
    """
    search = [Path(d) for d in args.rulepack_dir] if args.rulepack_dir else None
    if not args.pack:
        return None, None, search
    names: list[str] = []
    paths: list[Path] = []
    for value in args.pack:
        if Path(value).is_file():
            paths.append(Path(value))
        else:
            names.append(value)
    return names or None, paths or None, search


def _setting_arg(args: argparse.Namespace) -> str | dict[str, str] | None:
    """Turn repeatable ``--setting [CLASS=]VALUE`` into ``pipeline.run``'s
    ``setting`` argument: ``None`` when never given, a plain string when
    every value given was bare (applies to every class), or a mapping
    (``"*"`` for the last bare value, one entry per named class) once any
    ``CLASS=VALUE`` form appears — see ``pipeline._resolve_settings``.
    """
    if not args.setting:
        return None
    mapping: dict[str, str] = {}
    bare: str | None = None
    for value in args.setting:
        if "=" in value:
            cls, _, val = value.partition("=")
            mapping[cls] = val
        else:
            bare = value
    if not mapping:
        return bare
    if bare is not None:
        mapping["*"] = bare
    return mapping


def _run_from_args(args: argparse.Namespace) -> Run:
    """Build the ``Run`` every subcommand shares.

    ``args.policy_date`` is read with ``getattr`` rather than named directly:
    only ``trend``'s own parser defines that flag (decision 7,
    CONTRACT-SEEDS.md), and every other subcommand's ``Namespace`` simply
    does not carry it, the same way none of them carry ``args.by``.
    """
    pack_name, pack_path, pack_search = _pack_args(args)
    raw_policy_date = getattr(args, "policy_date", None)
    return run(
        args.clinical,
        args.remittance,
        locality=args.locality,
        setting=_setting_arg(args),
        reference=parse_when(args.reference) if args.reference else None,
        pack_name=pack_name,
        pack_path=pack_path,
        pack_search=pack_search,
        provenance_filter=FILTERS[args.filter],
        claims_dir=args.claims,
        witness_key=_witness_key(),
        policy_date=date.fromisoformat(raw_policy_date) if raw_policy_date else None,
    )


# ---------------------------------------------------------------------------
# Table / CSV rendering
# ---------------------------------------------------------------------------


def _cell(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):  # pragma: no cover - money never crosses as float
        return f"{value}"
    return str(value)


def _print_table(r: Run, rows: list[dict[str, object]], columns: list[str]) -> None:
    print(_meta_comment(r))
    if not rows:
        print("(no rows)")
        return
    widths = {c: len(c) for c in columns}
    rendered = [{c: _cell(row.get(c)) for c in columns} for row in rows]
    for row in rendered:
        for c in columns:
            widths[c] = max(widths[c], len(row[c]))
    print("  ".join(c.ljust(widths[c]) for c in columns))
    print("  ".join("-" * widths[c] for c in columns))
    for row in rendered:
        print("  ".join(row[c].ljust(widths[c]) for c in columns))


def _print_csv(r: Run, rows: list[dict[str, object]], columns: list[str]) -> None:
    print(_meta_comment(r))
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(row.get(c)) for c in columns])
    sys.stdout.write(buf.getvalue())


def _print_rows(r: Run, rows: list[dict[str, object]], columns: list[str], fmt: str) -> None:
    if fmt == "table":
        _print_table(r, rows, columns)
    elif fmt == "csv":
        _print_csv(r, rows, columns)
    else:  # json: one document per row, newline-delimited
        envelope = _versions_envelope(r)
        for row in rows:
            print(json.dumps(to_jsonable({**envelope, **row}), allow_nan=False))


def _print_document(doc: dict[str, object]) -> None:
    """Print one JSON document. ``doc`` may still carry raw ``Decimal``\\ s,
    dataclasses or other values a hand-built row dict is not yet safe to hand
    to :func:`json.dumps`; :func:`to_jsonable` is what makes it safe."""
    print(json.dumps(to_jsonable(doc), indent=2, allow_nan=False))


# ---------------------------------------------------------------------------
# score / explain
# ---------------------------------------------------------------------------

_SCORE_COLUMNS = [
    "encounter_id",
    "cpt",
    "score",
    "realized",
    "expected",
    "ratio",
    "ratio_low",
    "ratio_high",
    "pattern_id",
    "dominant_lever",
    "payer",
    "site",
]


def _dominant_lever(a: Adequacy) -> str | None:
    def magnitude(m: object) -> Decimal:
        dollars = getattr(m, "dollars", None)
        return abs(dollars) if dollars is not None else Decimal(0)

    best = max((a.signature.m1, a.signature.m2, a.signature.m3), key=magnitude)
    return best.lever


def _score_row(a: Adequacy, obs: Observation) -> dict[str, object]:
    return {
        "encounter_id": a.encounter_id,
        "cpt": a.cpt,
        "score": a.score.value,
        "realized": a.realized,
        "expected": a.expected,
        "ratio": a.ratio.value,
        "ratio_low": a.ratio_interval.low,
        "ratio_high": a.ratio_interval.high,
        "pattern_id": a.signature.pattern_id,
        "dominant_lever": _dominant_lever(a),
        "payer": a.payer_name,
        "site": obs.scored.encounter.facility_npi,
    }


def _observations_by_id(r: Run) -> dict[str, Observation]:
    return {o.scored.encounter.encounter_id: o for o in r.observations}


def _cmd_score(args: argparse.Namespace) -> int:
    r = _run_from_args(args)
    obs_by_id = _observations_by_id(r)
    ids: list[str] = args.encounter_id or [a.encounter_id for a in r.adequacies]
    by_id = {a.encounter_id: a for a in r.adequacies}

    fmt = _format_of(args)
    if fmt == "json":
        envelope = _versions_envelope(r)
        for eid in ids:
            a = by_id.get(eid)
            if a is None:
                print(json.dumps({**envelope, "encounter_id": eid, "error": "not scored"}))
                continue
            doc = to_jsonable(a)
            doc.update(envelope)
            print(json.dumps(doc, allow_nan=False))
        return 0

    rows = [_score_row(by_id[eid], obs_by_id[eid]) for eid in ids if eid in by_id]
    _print_rows(r, rows, _SCORE_COLUMNS, fmt)
    return 0


def _cmd_explain(args: argparse.Namespace) -> int:
    r = _run_from_args(args)
    by_id = {a.encounter_id: a for a in r.adequacies}
    a = by_id.get(args.encounter_id)
    if a is None:
        print(f"no such scored encounter in this run: {args.encounter_id}", file=sys.stderr)
        return 1

    # Every ``Adequacy`` in ``r.adequacies`` was built from a study
    # ``Observation``, so ``obs`` exists whenever ``a`` does.
    obs = _observations_by_id(r)[args.encounter_id]

    fmt = _format_of(args)
    if fmt == "json":
        doc = to_jsonable(a)
        doc.update(_versions_envelope(r))
        doc["report"] = cmd_encounter(r, args.encounter_id)
        # Decision 6, CONTRACT-SEEDS.md: one row per rule pack marker,
        # present or missing (``missing: bool``, with its reason) --
        # ``Adequacy`` itself carries no marker detail, so this is where
        # ``explain --json`` gets it.
        doc["markers"] = to_jsonable(obs.scored.marker_rows)
        _print_document(doc)
        return 0

    _print_rows(r, [_score_row(a, obs)], _SCORE_COLUMNS, fmt)
    if fmt == "table":
        print()
        print(cmd_encounter(r, args.encounter_id))
    return 0


# ---------------------------------------------------------------------------
# slope
# ---------------------------------------------------------------------------

_SLOPE_COLUMNS = [
    "code",
    "n",
    "score_min",
    "score_max",
    "score_median",
    "slope",
    "slope_low",
    "slope_high",
    "normalized_slope",
    "normalized_low",
    "normalized_high",
    "verdict",
    "r_squared",
]


def _slope_row(code: str, slope: PooledSlope) -> dict[str, object]:
    return {
        "code": code,
        "n": slope.n,
        "score_min": slope.score_min,
        "score_max": slope.score_max,
        "score_median": slope.score_median,
        "slope": slope.slope,
        "slope_low": slope.interval.low,
        "slope_high": slope.interval.high,
        "normalized_slope": slope.normalized_slope,
        "normalized_low": slope.normalized_interval.low,
        "normalized_high": slope.normalized_interval.high,
        "verdict": slope.verdict,
        "r_squared": slope.r_squared,
    }


def _cmd_slope(args: argparse.Namespace) -> int:
    r = _run_from_args(args)
    card = next((c for c in r.cards if c.code == args.code), None)
    if card is None or card.method0 is None:
        print(f"no Method 0 slope for CPT {args.code} in this run", file=sys.stderr)
        return 1

    row = _slope_row(args.code, card.method0)
    fmt = _format_of(args)
    if fmt == "json":
        _print_document({**_versions_envelope(r), **row})
        return 0
    _print_rows(r, [row], _SLOPE_COLUMNS, fmt)
    return 0


# ---------------------------------------------------------------------------
# code
# ---------------------------------------------------------------------------

_CODE_BY_COLUMNS = ["group", "n", "realized", "expected", "ratio"]
_CODE_SUMMARY_COLUMNS = [
    "code",
    "n",
    "scored",
    "ratio_median",
    "ratio_p90",
    "shortfall_total",
    "top_quintile_share",
    "headline",
]

_BY_KEYS = {"site", "payer", "surgeon", "period"}


def _group_key(by: str, obs: Observation, r: Run) -> str:
    enc = obs.scored.encounter
    if by == "site":
        return enc.facility_npi
    if by == "payer":
        return r.payer_labels.get(obs.payer_id, obs.payer_id)
    if by == "surgeon":
        return enc.surgeon_id or "(unknown)"
    if by == "period":
        return enc.service_date.strftime("%Y-%m")
    msg = f"unknown --by value: {by}"
    raise ValueError(msg)


def _code_by_rows(r: Run, code: str, by: str) -> list[dict[str, object]]:
    by_id = {a.encounter_id: a for a in r.adequacies}
    groups: dict[str, list[Adequacy]] = defaultdict(list)
    for obs in r.observations:
        enc = obs.scored.encounter
        if obs.cohort is not Cohort.STUDY or enc.primary_cpt != code:
            continue
        a = by_id.get(enc.encounter_id)
        if a is None:
            continue
        groups[_group_key(by, obs, r)].append(a)

    rows: list[dict[str, object]] = []
    for key in sorted(groups):
        items = groups[key]
        with money_context():
            realized = sum((a.realized for a in items), start=Decimal(0))
            expected = sum((a.expected for a in items), start=Decimal(0))
        ratio = ratio_of(realized, expected) if expected > 0 else None
        rows.append(
            {
                "group": key,
                "n": len(items),
                "realized": realized,
                "expected": expected,
                "ratio": ratio.value if ratio is not None else None,
            }
        )
    return rows


def _code_summary_row(card: CodeCard) -> dict[str, object]:
    median = next((p for p in card.ratio_at if p.point == "median"), None)
    p90 = next((p for p in card.ratio_at if p.point == "p90"), None)
    return {
        "code": card.code,
        "n": card.n,
        "scored": card.scored,
        "ratio_median": median.ratio.value if median and median.ratio else None,
        "ratio_p90": p90.ratio.value if p90 and p90.ratio else None,
        "shortfall_total": card.shortfall.total if card.shortfall else None,
        "top_quintile_share": card.shortfall.top_quintile_share if card.shortfall else None,
        "headline": card.headline,
    }


def _cmd_code(args: argparse.Namespace) -> int:
    r = _run_from_args(args)
    card = next((c for c in r.cards if c.code == args.code), None)
    if card is None:
        print(f"no CodeCard for CPT {args.code} in this run", file=sys.stderr)
        return 1

    fmt = _format_of(args)
    if args.by:
        rows = _code_by_rows(r, args.code, args.by)
        if fmt == "json":
            doc = {**_versions_envelope(r), "code": args.code, "by": args.by, "rows": rows}
            _print_document(doc)
            return 0
        _print_rows(r, rows, _CODE_BY_COLUMNS, fmt)
        return 0

    if fmt == "json":
        doc = to_jsonable(card)
        doc.update(_versions_envelope(r))
        _print_document(doc)
        return 0
    _print_rows(r, [_code_summary_row(card)], _CODE_SUMMARY_COLUMNS, fmt)
    return 0


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------

_COMPARE_COLUMNS = [
    "class",
    "domain",
    "code",
    "n",
    "normalized_slope",
    "ratio_median",
    "ratio_p90",
    "shortfall",
    "top_quintile_share",
    "dominant_lever",
    "verdict",
]

# Decision 6 (CONTRACT-PACKS.md and CONTRACT-PACKS-MC.md): ``--domains``
# collapses a class's cards to one row per service line (``population.
# domain_rows``, decision 5, MC track) rather than reordering per-code rows.
# Same shape as ``--codes`` otherwise; both carry a ``class`` column on a
# multi-class run (decision 6, MC track: "compare groups output by class with
# a class column"), always populated even on a single-class run.
_COMPARE_DOMAIN_COLUMNS = [
    "class",
    "domain",
    "code",
    "n",
    "normalized_slope",
    "ratio_median",
    "ratio_p90",
    "shortfall",
    "dominant_lever",
    "verdict",
]


def _compare_row(row: CompareRow) -> dict[str, object]:
    return {
        "class": row.encounter_class,
        "domain": row.domain,
        "code": row.code,
        "n": row.n,
        "normalized_slope": row.normalized_slope,
        "ratio_median": row.ratio_median.value if row.ratio_median else None,
        "ratio_p90": row.ratio_p90.value if row.ratio_p90 else None,
        "shortfall": row.shortfall,
        "top_quintile_share": row.top_quintile_share,
        "dominant_lever": row.dominant_lever,
        "verdict": row.verdict,
    }


def _class_rank(cls: str) -> int:
    return CLASS_ORDER.index(cls) if cls in CLASS_ORDER else len(CLASS_ORDER)


def _cmd_compare(args: argparse.Namespace) -> int:
    r = _run_from_args(args)
    fmt = _format_of(args)

    if args.domains:
        domain_row_tuples = [row for c in r.classes for row in domain_rows(c)]
        rows = [_compare_row(row) for row in domain_row_tuples]
        if fmt == "json":
            envelope = _versions_envelope(r)
            for row in rows:
                doc = {**envelope, "by": "domains", **row}
                print(json.dumps(to_jsonable(doc), allow_nan=False))
            return 0
        _print_rows(r, rows, _COMPARE_DOMAIN_COLUMNS, fmt)
        return 0

    ordered = sorted(r.compare, key=lambda row: (_class_rank(row.encounter_class), row.code))
    rows = [_compare_row(row) for row in ordered]
    if fmt == "json":
        envelope = _versions_envelope(r)
        for row in rows:
            print(json.dumps(to_jsonable({**envelope, **row}), allow_nan=False))
        return 0
    _print_rows(r, rows, _COMPARE_COLUMNS, fmt)
    return 0


# ---------------------------------------------------------------------------
# trend (decision 7, CONTRACT-SEEDS.md)
# ---------------------------------------------------------------------------

_TREND_COLUMNS = ["code", "period", "n", "ratio", "ratio_low", "ratio_high", "suppressed"]
_TREND_BY_DOMAIN_COLUMNS = ["domain", *_TREND_COLUMNS]
_TREND_BY_PAYER_COLUMNS = [
    "code",
    "payer",
    "period",
    "n",
    "ratio",
    "ratio_low",
    "ratio_high",
    "suppressed",
]


def _trend_row(
    code: str, point: PeriodPoint, *, payer: str | None = None, domain: str | None = None
) -> dict[str, object]:
    row: dict[str, object] = {}
    if domain is not None:
        row["domain"] = domain
    row["code"] = code
    if payer is not None:
        row["payer"] = payer
    row.update(
        {
            "period": point.period,
            "n": point.n,
            "ratio": point.ratio.value if point.ratio is not None else None,
            "ratio_low": point.interval.low if point.interval is not None else None,
            "ratio_high": point.interval.high if point.interval is not None else None,
            "suppressed": point.suppressed,
        }
    )
    return row


def _cmd_trend(args: argparse.Namespace) -> int:
    r = _run_from_args(args)
    pool = r.domain_trends if args.by == "domain" else r.trends
    series = [s for s in pool if args.code is None or s.code == args.code]

    fmt = _format_of(args)
    by_payer = args.by == "payer"

    if fmt == "json":
        doc: dict[str, object] = {**_versions_envelope(r)}
        if args.by:
            doc["by"] = args.by
        doc["series"] = series
        _print_document(doc)
        return 0

    if by_payer:
        rows = [
            _trend_row(s.code, point, payer=payer_series.payer_label)
            for s in series
            for payer_series in s.by_payer
            for point in payer_series.points
        ]
        _print_rows(r, rows, _TREND_BY_PAYER_COLUMNS, fmt)
        return 0

    if args.by == "domain":
        rows = [
            _trend_row(s.code, point, domain=s.domain or "") for s in series for point in s.points
        ]
        _print_rows(r, rows, _TREND_BY_DOMAIN_COLUMNS, fmt)
        return 0

    rows = [_trend_row(s.code, point) for s in series for point in s.points]
    _print_rows(r, rows, _TREND_COLUMNS, fmt)
    return 0


# ---------------------------------------------------------------------------
# queue
# ---------------------------------------------------------------------------

_QUEUE_COLUMNS = [
    "class",
    "encounter_id",
    "code",
    "score",
    "code_percentile",
    "evidence_strength",
    "suggested_vehicle",
    "outcome_835",
    "payer",
    "site",
    "surgeon",
]


def _queue_row(item: QueueItem, encounter_class: str) -> dict[str, object]:
    return {
        "class": encounter_class,
        "encounter_id": item.encounter_id,
        "code": item.code,
        "score": item.score,
        "code_percentile": item.code_percentile,
        "evidence_strength": item.evidence_strength,
        "suggested_vehicle": item.suggested_vehicle,
        "outcome_835": item.outcome_835,
        "payer": item.payer_label,
        "site": item.site,
        "surgeon": item.surgeon,
    }


def _queue_matches(item: QueueItem, args: argparse.Namespace) -> bool:
    if args.surgeon is not None and item.surgeon != args.surgeon:
        return False
    if args.site is not None and item.site != args.site:
        return False
    if args.payer is not None and item.payer_label != args.payer:
        return False
    if args.bucket is not None:
        buckets = {getattr(f, "bucket", None) for f in item.flags}
        if args.bucket not in buckets:
            return False
    return True


def _cmd_queue(args: argparse.Namespace) -> int:
    r = _run_from_args(args)
    # ``QueueItem`` carries no class of its own (decision 2, MC track: the
    # model is unchanged); the class column comes from which ``ClassRun``
    # the item is still sitting in, before ``Run.queue`` flattens that away.
    tagged = [
        (c.encounter_class, item)
        for c in r.classes
        for item in c.queue
        if _queue_matches(item, args)
    ]
    rows = [_queue_row(item, cls) for cls, item in tagged]

    fmt = _format_of(args)
    if fmt == "json":
        _print_document({**_versions_envelope(r), "items": rows})
        return 0
    _print_rows(r, rows, _QUEUE_COLUMNS, fmt)
    return 0


# ---------------------------------------------------------------------------
# run / version
# ---------------------------------------------------------------------------


def _cmd_run(args: argparse.Namespace) -> int:
    r = _run_from_args(args)
    if args.json:
        print(json.dumps(run_document(r), indent=2, allow_nan=False))
    else:
        print(cmd_report(r))
    return 0


def _cmd_version(_: argparse.Namespace) -> int:
    for name, version in _VERSIONS.items():
        print(f"{name} {version}")
    return 0


# ---------------------------------------------------------------------------
# packs
# ---------------------------------------------------------------------------

_PACKS_COLUMNS = [
    "name",
    "source",
    "status",
    "rule_pack_id",
    "version",
    "weights_version",
    "encounter_class",
    "digest",
    "path",
    "note",
]


def _pack_info_row(info: PackInfo) -> dict[str, object]:
    return {
        "name": info.name,
        "source": info.source,
        "status": info.status,
        "rule_pack_id": info.rule_pack_id,
        "version": info.version,
        "weights_version": info.weights_version,
        "encounter_class": info.encounter_class,
        "digest": info.digest[:12] if info.digest else "",
        "path": info.path or "",
        "note": info.note or "",
    }


def _cmd_packs(args: argparse.Namespace) -> int:
    search = [Path(d) for d in args.rulepack_dir] if args.rulepack_dir else None
    infos = available(search=search)
    if args.json:
        print(json.dumps(to_jsonable({"packs": infos}), indent=2, allow_nan=False))
        return 0

    if not infos:
        print("(no rule packs found)")
        return 0
    rows = [_pack_info_row(info) for info in infos]
    widths = {c: len(c) for c in _PACKS_COLUMNS}
    rendered = [{c: str(row[c]) for c in _PACKS_COLUMNS} for row in rows]
    for row in rendered:
        for c in _PACKS_COLUMNS:
            widths[c] = max(widths[c], len(row[c]))
    print("  ".join(c.ljust(widths[c]) for c in _PACKS_COLUMNS))
    print("  ".join("-" * widths[c] for c in _PACKS_COLUMNS))
    for row in rendered:
        print("  ".join(row[c].ljust(widths[c]) for c in _PACKS_COLUMNS))
    return 0


# ---------------------------------------------------------------------------
# synth (CONTRACT-SEEDS.md, "worth track S1")
# ---------------------------------------------------------------------------

_SYNTH_CLASSES = ("surgical", "visit", "episode", "mixed")
"""The four ``--class`` values ``synth`` accepts, in the same order the
catalog's own ``mixed`` composes them (CONTRACT-SEEDS.md)."""


def _synth_builder(encounter_class: str) -> Callable[..., None]:
    """The scenario-aware ``build(out_dir, *, scenario, seed=None, scale=1)``
    entry point for one ``--class`` value, imported lazily so ``worth-cli
    --help`` never pays for loading all four generators (and, through them,
    ``worth-fees``' fixtures) just to print usage."""
    from worth_complexity.synthetic import episode, mixed, surgical, visit

    builders: dict[str, Callable[..., None]] = {
        "surgical": surgical.build,
        "visit": visit.build,
        "episode": episode.build,
        "mixed": mixed.build,
    }
    return builders[encounter_class]


def _cmd_synth(args: argparse.Namespace) -> int:
    from worth_complexity.synthetic.scenarios import SCENARIOS, catalog_rows

    if args.list:
        rows = catalog_rows()
        name_w = max(len("scenario"), *(len(r[0]) for r in rows))
        group_w = max(len("group"), *(len(r[1]) for r in rows))
        print(f"{'scenario'.ljust(name_w)}  {'group'.ljust(group_w)}  description")
        print(f"{'-' * name_w}  {'-' * group_w}  {'-' * 11}")
        for name, group, description in rows:
            print(f"{name.ljust(name_w)}  {group.ljust(group_w)}  {description}")
        return 0

    missing = [
        flag
        for flag, value in (
            ("--scenario", args.scenario),
            ("--class", args.encounter_class),
            ("DIR", args.dir),
        )
        if value is None
    ]
    if missing:
        print(
            f"synth: {', '.join(missing)} required (or pass --list)",
            file=sys.stderr,
        )
        return 2

    scenario = SCENARIOS.get(args.scenario)
    if scenario is None:
        names = ", ".join(sorted(SCENARIOS))
        print(f"unknown scenario: {args.scenario!r} -- one of {names}", file=sys.stderr)
        return 1

    build = _synth_builder(args.encounter_class)
    build(args.dir, scenario=scenario, seed=args.seed, scale=args.scale * scenario.scale)

    from worth_complexity.synthetic import truth as truth_mod

    written = truth_mod.read(args.dir)
    print(str(args.dir.resolve()))
    print(json.dumps(written.to_json(), indent=2, sort_keys=True, allow_nan=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The parser for every subcommand but ``price`` and ``db``.

    ``price`` and ``db`` are handled before this parser ever sees the
    arguments, see :func:`main`; they are named here only so ``worth-cli
    --help`` lists every subcommand.
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
    _add_dataset_flags(run_parser)
    run_parser.add_argument(
        "--json", action="store_true", help="print the run as JSON instead of the text report"
    )
    run_parser.set_defaults(func=_cmd_run)

    score_parser = subcommands.add_parser(
        "score", help="one adequacy document per encounter (batch: newline-delimited JSON)"
    )
    score_parser.add_argument("encounter_id", nargs="*", help="default: every scored encounter")
    _add_dataset_flags(score_parser)
    _add_output_flags(score_parser)
    score_parser.set_defaults(func=_cmd_score)

    explain_parser = subcommands.add_parser("explain", help="the full derivation for one encounter")
    explain_parser.add_argument("encounter_id")
    _add_dataset_flags(explain_parser)
    _add_output_flags(explain_parser)
    explain_parser.set_defaults(func=_cmd_explain)

    slope_parser = subcommands.add_parser(
        "slope", help="the pooled Method 0 slope for one code (decision 7)"
    )
    slope_parser.add_argument("code")
    _add_dataset_flags(slope_parser)
    _add_output_flags(slope_parser)
    slope_parser.set_defaults(func=_cmd_slope)

    code_parser = subcommands.add_parser("code", help="the population card for one code")
    code_parser.add_argument("code")
    code_parser.add_argument("--by", choices=sorted(_BY_KEYS), default=None)
    _add_dataset_flags(code_parser)
    _add_output_flags(code_parser)
    code_parser.set_defaults(func=_cmd_code)

    compare_parser = subcommands.add_parser(
        "compare", help="the cross-code compare view (decision 9)"
    )
    compare_group = compare_parser.add_mutually_exclusive_group()
    compare_group.add_argument("--codes", action="store_true", help="one row per code (default)")
    compare_group.add_argument(
        "--domains", action="store_true", help="rows grouped by service line within a class"
    )
    _add_dataset_flags(compare_parser)
    _add_output_flags(compare_parser)
    compare_parser.set_defaults(func=_cmd_compare)

    trend_parser = subcommands.add_parser(
        "trend", help="ratio over time, per code, with an optional pre/post policy split"
    )
    trend_parser.add_argument("code", nargs="?", default=None, help="default: every study code")
    trend_parser.add_argument(
        "--policy-date",
        dest="policy_date",
        default=None,
        metavar="YYYY-MM-DD",
        help="split each code's cohort into pre/post at this date, and mark it on the series",
    )
    trend_parser.add_argument(
        "--by",
        choices=("payer", "domain"),
        default=None,
        help="report each code's points broken out by payer instead of pooled",
    )
    _add_dataset_flags(trend_parser)
    _add_output_flags(trend_parser)
    trend_parser.set_defaults(func=_cmd_trend)

    queue_parser = subcommands.add_parser("queue", help="the work queue, ranked")
    queue_parser.add_argument("--surgeon", default=None)
    queue_parser.add_argument("--site", default=None)
    queue_parser.add_argument("--payer", default=None)
    queue_parser.add_argument("--bucket", choices=("missed", "mismatched", "no_code"), default=None)
    _add_dataset_flags(queue_parser)
    _add_output_flags(queue_parser)
    queue_parser.set_defaults(func=_cmd_queue)

    # `price` and `db` are listed here only so `worth-cli --help` names every
    # subcommand; `main` intercepts and forwards both before this parser ever
    # parses their arguments, see below.
    subcommands.add_parser(
        "price", help="price one code (forwards to worth-fees; see `worth-cli price --help`)"
    )
    subcommands.add_parser(
        "db", help="the fee-schedule database (forwards to worth-db; see `worth-cli db --help`)"
    )

    version_parser = subcommands.add_parser("version", help="print all four package versions")
    version_parser.set_defaults(func=_cmd_version)

    packs_parser = subcommands.add_parser(
        "packs", help="list rule packs layer 2 can see: packaged, then external"
    )
    packs_parser.add_argument(
        "--rulepack-dir",
        dest="rulepack_dir",
        action="append",
        default=None,
        metavar="DIR",
        help="a directory to search for external rule packs, before "
        "WORTH_RULEPACK_DIR (repeatable)",
    )
    packs_parser.add_argument("--json", action="store_true")
    packs_parser.set_defaults(func=_cmd_packs)

    synth_parser = subcommands.add_parser(
        "synth", help="generate a named scenario dataset (CONTRACT-SEEDS.md's seed suite)"
    )
    synth_parser.add_argument(
        "--list", action="store_true", help="list every scenario in the catalog and exit"
    )
    synth_parser.add_argument("--scenario", default=None, help="a name from `synth --list`")
    synth_parser.add_argument(
        "--class",
        dest="encounter_class",
        choices=_SYNTH_CLASSES,
        default=None,
        help="the encounter class to generate",
    )
    synth_parser.add_argument("--seed", type=int, default=None, help="default: the class's own")
    synth_parser.add_argument("--scale", type=int, default=1, help="default: 1")
    synth_parser.add_argument(
        "dir", metavar="DIR", nargs="?", type=Path, default=None, help="output directory"
    )
    synth_parser.set_defaults(func=_cmd_synth)

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
