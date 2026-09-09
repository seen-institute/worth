"""Named corruptions applied to an already-generated scenario directory.

Every function here takes the generated directory (the one holding
``clinical/``, ``remittance/`` and ``claims/``) as its first argument and a
keyword-only ``rng`` (even when it does not need one, for uniform dispatch
from :func:`apply`), mutates files in place, and returns ``None``. Each is
deterministic given the seed that produced ``rng`` -- no function here reads
the wall clock or anything outside the directory.

Every corruption keeps table delimiters (``|``) and X12 segment terminators
(``~``) valid syntax: what changes is *content* (a blank field, a duplicated
id, a missing claim, a renamed header), never the physical shape a reader
splits on. That is what makes the ``dirty`` scenario survivable -- the run
completes, just with real, nonzero linkage loss and missingness -- while
:func:`apply_broken`'s four variants are built to fail a specific stage
instead.

Two functions are class-specific by construction (``blank_operative_minutes``
and ``absurd_operative_time`` corrupt operative-minutes timestamps that only
the surgical class's ``or_log.txt`` carries) and no-op on a directory that
does not carry the column they target -- so the same ``dirty`` scenario spec
(:data:`worth_complexity.synthetic.scenarios.SCENARIOS`) applies to every
class without per-class branching in the catalog itself.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_ANCHORS = ("or_log.txt", "visit.txt", "episode.txt")
_ID_COLUMN = {"or_log.txt": "log_id", "visit.txt": "visit_id", "episode.txt": "episode_id"}
_PROC_TABLE = {
    "or_log.txt": "or_log_proc.txt",
    "visit.txt": "visit_proc.txt",
    "episode.txt": "episode_proc.txt",
}
_ACCOUNT_COLUMN = "billing_account_id"
#: A column present on each anchor table that nothing in the reading path
#: (``cases.py``/``visits.py``/``episodes.py``/``markers.py``) looks up by
#: name -- safe for :func:`header_case` to rename without breaking the run.
_SAFE_HEADER_COLUMN = {
    "or_log.txt": "room",
    "visit.txt": "time_attested",
    "episode.txt": "escalation_protocol_version",
}


def _anchor(clinical: Path) -> str:
    for name in _ANCHORS:
        if (clinical / name).is_file():
            return name
    msg = f"no anchor file ({', '.join(_ANCHORS)}) found in {clinical}"
    raise FileNotFoundError(msg)


def _read_rows(path: Path) -> tuple[list[str], list[list[str]]]:
    text = path.read_text(encoding="utf-8-sig")
    lines = [ln for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()]
    header = lines[0].split("|")
    rows = [ln.split("|") for ln in lines[1:]]
    return header, rows


def _write_rows(path: Path, header: list[str], rows: list[list[str]]) -> None:
    lines = ["|".join(header), *("|".join(r) for r in rows)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Clinical-table corruptions
# ---------------------------------------------------------------------------


def blank_operative_minutes(root: Path, *, rng: random.Random, p: float) -> None:
    """Blank ``procedure_close_dttm`` on a ``p`` fraction of ``or_log.txt``
    rows, so ``markers.py`` cannot derive ``operative_minutes`` for them --
    a missing structured marker (decision 6, CONTRACT-SEEDS.md), not a
    parse failure. No-op on a directory with no ``or_log.txt``."""
    clinical = root / "clinical"
    anchor = clinical / "or_log.txt"
    if not anchor.is_file():
        return
    header, rows = _read_rows(anchor)
    idx = header.index("procedure_close_dttm")
    for row in rows:
        if rng.random() < p:
            row[idx] = ""
    _write_rows(anchor, header, rows)


def absurd_operative_time(root: Path, *, rng: random.Random) -> None:
    """One case's ``procedure_close_dttm`` set 40 hours after its
    ``procedure_start_dttm`` -- past any plausible single-case operative
    time, for the out-of-range guard (>1440 minutes) to reject. No-op on a
    directory with no ``or_log.txt``."""
    clinical = root / "clinical"
    anchor = clinical / "or_log.txt"
    if not anchor.is_file():
        return
    header, rows = _read_rows(anchor)
    if not rows:
        return
    start_idx = header.index("procedure_start_dttm")
    close_idx = header.index("procedure_close_dttm")
    row = rng.choice(rows)
    start = datetime.strptime(row[start_idx], "%m/%d/%Y %H:%M:%S")
    row[close_idx] = (start + timedelta(hours=40)).strftime("%m/%d/%Y %H:%M:%S")
    _write_rows(anchor, header, rows)


def duplicate_accounts(root: Path, *, rng: random.Random, n: int) -> None:
    """``n`` encounters get another encounter's ``billing_account_id`` --
    two clinical rows sharing one account, so remittance linkage joins one
    encounter's money to the wrong (or an ambiguous second) encounter."""
    clinical = root / "clinical"
    anchor = _anchor(clinical)
    header, rows = _read_rows(clinical / anchor)
    if len(rows) < 2:
        return
    idx = header.index(_ACCOUNT_COLUMN)
    targets = rng.sample(range(len(rows)), k=min(n, len(rows)))
    for i in targets:
        donor = rng.choice([j for j in range(len(rows)) if j != i])
        rows[i][idx] = rows[donor][idx]
    _write_rows(clinical / anchor, header, rows)


def strip_note_sections(root: Path, *, rng: random.Random, n: int) -> None:
    """``n`` note files lose every all-caps ``SECTION:`` header line,
    leaving the prose. No-op on a directory with no ``notes/`` (the episode
    class)."""
    notes_dir = root / "clinical" / "notes"
    if not notes_dir.is_dir():
        return
    files = sorted(notes_dir.glob("*.txt"))
    if not files:
        return
    for f in rng.sample(files, k=min(n, len(files))):
        lines = f.read_text(encoding="utf-8").splitlines()
        kept = [
            ln
            for ln in lines
            if not (ln.strip() and ln.strip().endswith(":") and ln.strip() == ln.strip().upper())
        ]
        f.write_text("\n".join(kept) + "\n", encoding="utf-8")


def header_case(root: Path, *, rng: random.Random | None = None, table: str) -> None:
    """Title-case one header column's name (``room`` -> ``Room``, the
    ``Or_Log_Id``-style variance CONTRACT-SEEDS.md's dirty scenario names).
    ``table="anchor"`` resolves to whichever anchor file is present. Targets
    only :data:`_SAFE_HEADER_COLUMN`, a column nothing reads by name, so
    parsing (and the run) still succeeds -- the point is that a reader ought
    to tolerate this, not that this package's own reader does yet."""
    clinical = root / "clinical"
    name = _anchor(clinical) if table == "anchor" else table
    path = clinical / name
    if not path.is_file():
        return
    safe_col = _SAFE_HEADER_COLUMN.get(name)
    header, rows = _read_rows(path)
    if safe_col is None or safe_col not in header:
        return
    header[header.index(safe_col)] = safe_col.title()
    _write_rows(path, header, rows)


def date_format(
    root: Path, *, rng: random.Random | None = None, table: str, fmt: str = "MM/DD/YYYY"
) -> None:
    """Reformat ``patient_lds.txt``'s ``birth_date`` (written ``MM/YYYY``)
    to ``fmt`` (a fabricated day of month inserted for ``MM/DD/YYYY``), so
    one table's date column has a visibly different shape from the rest of
    the extract's timestamps. No-op if ``table`` carries no ``birth_date``."""
    clinical = root / "clinical"
    path = clinical / table
    if not path.is_file():
        return
    header, rows = _read_rows(path)
    if "birth_date" not in header:
        return
    idx = header.index("birth_date")
    for row in rows:
        parts = row[idx].split("/")
        if fmt == "MM/DD/YYYY" and len(parts) == 2:
            month, year = parts
            row[idx] = f"{month}/01/{year}"
    _write_rows(path, header, rows)


def drop_column(root: Path, *, rng: random.Random | None = None, table: str, column: str) -> None:
    """Remove ``column`` from ``table`` entirely (header and every row).
    ``table="anchor"`` resolves to whichever anchor file is present. Used by
    :func:`apply_broken`'s ``malformed-table`` variant; ``cases.read_table``'s
    ``required=`` guard (added alongside this module) turns a missing
    required column into :class:`~worth_complexity.cases.ExtractError`
    rather than a bare ``KeyError`` the first time a row is read."""
    clinical = root / "clinical"
    name = _anchor(clinical) if table == "anchor" else table
    path = clinical / name
    header, rows = _read_rows(path)
    if column not in header:
        return
    idx = header.index(column)
    new_header = header[:idx] + header[idx + 1 :]
    new_rows = [r[:idx] + r[idx + 1 :] for r in rows]
    _write_rows(path, new_header, new_rows)


def remove_anchor(root: Path, *, rng: random.Random | None = None) -> None:
    """Delete whichever anchor file is present. Every other table stays --
    ``extracts.read_extract`` cannot even tell which class's reader to
    dispatch to, so this fails at the ``extract`` stage before any of them
    are opened."""
    clinical = root / "clinical"
    (clinical / _anchor(clinical)).unlink()


def unpriceable_comparator(
    root: Path, *, rng: random.Random | None = None, code: str = "99999"
) -> None:
    """Rewrite one comparator encounter's primary billed code to ``code`` --
    not a real CPT, so ``worth-fees`` cannot price it. Payments were already
    computed and written to the 835/837 under the *original* code before
    this runs (:func:`worth_complexity.synthetic.dirt.apply_broken` is
    applied after generation, never during it, so curve-fitting during
    generation never sees the bad code); only the clinical panel the
    pipeline's ``price`` stage reads is touched."""
    clinical = root / "clinical"
    anchor = _anchor(clinical)
    header, rows = _read_rows(clinical / anchor)
    id_idx = header.index(_ID_COLUMN[anchor])
    if anchor == "or_log.txt":
        service_idx = header.index("service")
        comparator_ids = {r[id_idx] for r in rows if r[service_idx] in {"GENSURG", "ORTHO", "URO"}}
    else:
        cohort_idx = header.index("cohort")
        comparator_ids = {r[id_idx] for r in rows if r[cohort_idx] == "comparator"}
    if not comparator_ids:
        return
    target_id = sorted(comparator_ids)[0]

    proc_path = clinical / _PROC_TABLE[anchor]
    proc_header, proc_rows = _read_rows(proc_path)
    proc_id_idx = proc_header.index(_ID_COLUMN[anchor])
    cpt_idx = proc_header.index("cpt")
    for row in proc_rows:
        if row[proc_id_idx] != target_id:
            continue
        is_primary = (
            "primary_yn" in proc_header and row[proc_header.index("primary_yn")] == "Y"
        ) or ("sequence" in proc_header and row[proc_header.index("sequence")] == "1")
        if is_primary:
            row[cpt_idx] = code
            break
    _write_rows(proc_path, proc_header, proc_rows)


# ---------------------------------------------------------------------------
# Remittance/claims corruptions
# ---------------------------------------------------------------------------


def _all_835_blocks(
    remittance: Path,
) -> tuple[dict[Path, list[str]], list[tuple[Path, int, int, str]]]:
    """``(file -> lines, [(file, start, end, account), ...])`` for every CLP
    claim block across every 835 in ``remittance``. ``end`` is the line
    index just past the block (the next CLP, or SE)."""
    file_lines: dict[Path, list[str]] = {}
    blocks: list[tuple[Path, int, int, str]] = []
    for f in sorted(remittance.glob("*.edi")):
        lines = f.read_text(encoding="utf-8").splitlines()
        file_lines[f] = lines
        clp_positions = [i for i, ln in enumerate(lines) if ln.startswith("CLP*")]
        se_position = next(i for i, ln in enumerate(lines) if ln.startswith("SE*"))
        boundaries = [*clp_positions[1:], se_position]
        for start, end in zip(clp_positions, boundaries, strict=True):
            account = lines[start].split("*")[1]
            blocks.append((f, start, end, account))
    return file_lines, blocks


def _drop_blocks(
    file_lines: dict[Path, list[str]], chosen: list[tuple[Path, int, int, str]]
) -> None:
    by_file: dict[Path, list[tuple[int, int]]] = {}
    for f, start, end, _account in chosen:
        by_file.setdefault(f, []).append((start, end))
    for f, ranges in by_file.items():
        lines = file_lines[f]
        for start, end in sorted(ranges, reverse=True):
            del lines[start:end]
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")


def drop_835_for(root: Path, *, rng: random.Random, share: float) -> None:
    """Remove a ``share`` fraction of claim blocks (the whole ``CLP``
    through its service lines) across every 835 file -- those encounters'
    remittance side disappears entirely, so they read as unlinked."""
    remittance = root / "remittance"
    file_lines, blocks = _all_835_blocks(remittance)
    n = round(len(blocks) * share)
    if n <= 0 or not blocks:
        return
    _drop_blocks(file_lines, rng.sample(blocks, k=min(n, len(blocks))))


def claim_without_835(root: Path, *, rng: random.Random, n: int) -> None:
    """Remove ``n`` specific claim blocks' 835 side while their 837 stays --
    a submitted claim with no remittance for it at all, the mirror image of
    :func:`drop_835_for`'s per-share removal."""
    remittance = root / "remittance"
    file_lines, blocks = _all_835_blocks(remittance)
    if not blocks:
        return
    _drop_blocks(file_lines, rng.sample(blocks, k=min(n, len(blocks))))


def orphan_835s(root: Path, *, rng: random.Random, n: int) -> None:
    """Insert ``n`` fabricated claim blocks (a duplicate of the first real
    one, account id replaced) into the first 835 file -- remittance lines
    with no clinical encounter and no 837 behind them."""
    remittance = root / "remittance"
    files = sorted(remittance.glob("*.edi"))
    if not files:
        return
    target = files[0]
    lines = target.read_text(encoding="utf-8").splitlines()
    clp_positions = [i for i, ln in enumerate(lines) if ln.startswith("CLP*")]
    se_position = next(i for i, ln in enumerate(lines) if ln.startswith("SE*"))
    if not clp_positions:
        return
    first_end = clp_positions[1] if len(clp_positions) > 1 else se_position
    template = lines[clp_positions[0] : first_end]
    inserted: list[str] = []
    for k in range(n):
        block = list(template)
        parts = block[0].split("*")
        parts[1] = f"ORPHAN{k + 1:04d}"
        block[0] = "*".join(parts)
        inserted.extend(block)
    new_lines = [*lines[:se_position], *inserted, *lines[se_position:]]
    target.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def truncate_clp(root: Path, *, rng: random.Random | None = None, file: str | None = None) -> None:
    """Remove one claim's ``AMT*B6`` (allowed-amount) segment -- a service
    line the 835 never states an allowed amount for, which ``x12.parse_835``
    already refuses (\"carries no AMT*B6 allowed amount\") rather than guess
    at bundling. ``file`` names one remittance file; default: the first,
    sorted. Used by :func:`apply_broken`'s ``corrupt-835`` variant."""
    remittance = root / "remittance"
    files = sorted(remittance.glob("*.edi"))
    if not files:
        return
    target = remittance / file if file else files[0]
    lines = target.read_text(encoding="utf-8").splitlines()
    amt_idx = next((i for i, ln in enumerate(lines) if ln.startswith("AMT*B6*")), None)
    if amt_idx is None:
        return
    del lines[amt_idx]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_FUNCTIONS: dict[str, Any] = {
    "blank_operative_minutes": blank_operative_minutes,
    "absurd_operative_time": absurd_operative_time,
    "duplicate_accounts": duplicate_accounts,
    "drop_835_for": drop_835_for,
    "orphan_835s": orphan_835s,
    "strip_note_sections": strip_note_sections,
    "header_case": header_case,
    "date_format": date_format,
    "claim_without_835": claim_without_835,
    "truncate_clp": truncate_clp,
    "drop_column": drop_column,
    "unpriceable_comparator": unpriceable_comparator,
    "remove_anchor": remove_anchor,
}


def apply(root: Path, dirt: tuple[tuple[str, dict[str, Any]], ...], *, rng: random.Random) -> None:
    """Apply every ``(function name, kwargs)`` pair in ``dirt``, in order --
    what a scenario's ``Scenario.dirt`` plants (:mod:`worth_complexity.
    synthetic.scenarios`)."""
    for name, kwargs in dirt:
        func = _FUNCTIONS.get(name)
        if func is None:
            msg = f"unknown dirt corruption: {name!r}"
            raise ValueError(msg)
        func(root, rng=rng, **kwargs)


def apply_broken(root: Path, broken: str, *, rng: random.Random) -> None:
    """Apply one of the four ``broken/*`` variants (CONTRACT-SEEDS.md's
    broken group), each built to fail a specific, known pipeline stage:

    * ``unpriceable-comparator`` -> fails at ``price`` (:class:`~worth_fees.
      models.WorthFeesError` subclass wrapped by :class:`~worth_complexity.
      adequacy.PricingError`).
    * ``malformed-table`` -> fails at ``extract``
      (:class:`~worth_complexity.cases.ExtractError`).
    * ``corrupt-835`` -> fails at ``remittance`` (:class:`~worth_complexity.
      x12.X12Error`).
    * ``no-anchor`` -> fails at ``extract`` (:class:`~worth_complexity.
      cases.ExtractError`, "missing extract file").
    """
    if broken == "unpriceable-comparator":
        unpriceable_comparator(root, rng=rng)
    elif broken == "malformed-table":
        clinical = root / "clinical"
        drop_column(root, rng=rng, table=_anchor(clinical), column=_ACCOUNT_COLUMN)
    elif broken == "corrupt-835":
        truncate_clp(root, rng=rng)
    elif broken == "no-anchor":
        remove_anchor(root, rng=rng)
    else:
        msg = f"unknown broken variant: {broken!r}"
        raise ValueError(msg)
