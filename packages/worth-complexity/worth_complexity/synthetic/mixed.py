"""Compose a mixed extract from the three single-class generators.

A real partner delivery is one extract with OR cases, clinic visits and
monitoring episodes side by side, sharing one 835/837 feed — decision 7,
CONTRACT-PACKS-MC.md ("MC", the multi-class-runs track). This script does not
duplicate any of the three classes' own generative logic (``make_synthetic_
dataset.py``, ``make_visit_dataset.py``, ``make_episode_dataset.py``, each
already committed and already producing its own single-class fixture, which
this script never touches or regenerates): it runs each of the three as a
subprocess into its own scratch directory, at the caller's scale, then merges
their three ``clinical/``, ``remittance/`` and ``claims/`` directories into
one.

Three merge shapes, chosen per file by what each generator actually writes:

* **Class-only tables** (``or_log.txt``, ``visit_proc.txt``,
  ``device_readings.txt``, ...) are copied through unchanged: only one
  class's reader ever opens that filename.
* **Identical-schema shared tables** (``patient_lds.txt`` — same five columns
  in every class; ``problem_list.txt`` — same three columns in visit and
  episode; ``notes.txt``, the note index — same seven columns in surgical and
  visit) are concatenated: one header, every source's data rows appended.
  Each generator already writes patient/encounter ids in a distinct,
  non-overlapping namespace (``Z``/``W``/``P`` patient ids, ``A7``/``B7``+
  ``B8``/``A9`` billing account ids, ``88``-/``V``-/``E``-prefixed encounter
  ids, ``10``-/``20``-prefixed CSNs — checked against each generator's own id
  arithmetic, not assumed), so a straight concatenation of these lookup
  tables never lets one class's row shadow another's.
* **Union-schema shared tables** (``encounter_dx.txt``: surgical's columns
  are ``csn|icd10|seq|poa_yn``, visit's are ``csn|icd10|sequence|addressed`` —
  the same idea, different column names) get one header that is the ordered
  union of every source's own columns, and each row keeps only its own
  source's columns filled, blank for the rest. Every reader in this package
  looks up a table's columns by name (``worth_complexity.cases.Table.rows``
  is ``dict[str, str]`` per row), and reads only the names it wrote itself,
  so the extra blank columns are inert to a reader that never asked for them.

``notes/`` (surgical and visit only; episode carries no note dataset) is
merged by copying every file from both classes' ``notes/`` into one
directory — safe because surgical's note filenames always end in an ``O`` or
``D`` before ``.txt`` and visit's never do, so the two id spaces cannot
collide there either.

835/837 files get a class-prefixed filename (``surgical_835_...edi``,
``visit_835_...edi``, ...) so three classes' files can never collide on disk
even if two generators happened to batch on the same payer and month, plus a
class-specific offset added to every *envelope* control number (ISA13,
GS06/GE02, ST02/SE02, IEA02, BHT03) so the three classes' remittance/claims
directories do not carry duplicate interchange control numbers either. Inner
trace numbers (835 TRN02, CLP07) are left as each generator wrote them:
neither X12 nor this package's own ``x12.py``/``claims.py`` readers require
them to be globally unique, and offsetting them would mean parsing composite
elements rather than a single positional field, for no correctness gain here
— the join key the pipeline actually uses is ``billing_account_id`` /
``CLP01`` / ``CLM01``, already collision-free by construction (see above).

Usage: ``uv run python tools/make_mixed_dataset.py DIR [SCALE]``, or
``just build-dataset mixed [DIR] [SCALE]``.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from worth_complexity.synthetic import episode as episode_mod
from worth_complexity.synthetic import surgical as surgical_mod
from worth_complexity.synthetic import visit as visit_mod

if TYPE_CHECKING:
    from worth_complexity.synthetic.scenarios import Scenario

CLASS_ORDER: tuple[str, ...] = ("surgical", "visit", "episode")

SEED = 20260909
"""Default seed :func:`build` falls back to when the caller passes none --
the same seed value is handed to all three per-class ``build()`` calls
(each seeds its own independent ``random.Random``, so this is not a shared
RNG state, just one number so ``worth-cli synth --class mixed`` without
``--seed`` is still deterministic)."""

CONTROL_OFFSET: dict[str, int] = {"surgical": 0, "visit": 100_000, "episode": 200_000}
"""Added to every envelope control number (see the module docstring) so the
three classes' 835/837 directories never carry a duplicate one, even though
each generator's own ``write_remittance``/``write_claims`` numbers its own
batches starting from 101."""

_CLASS_ONLY_TABLES: dict[str, tuple[str, ...]] = {
    "surgical": ("or_log.txt", "or_log_proc.txt", "or_staff.txt"),
    "visit": (
        "visit.txt",
        "visit_proc.txt",
        "orders.txt",
        "med_orders.txt",
        "window_encounters.txt",
    ),
    "episode": (
        "episode.txt",
        "episode_proc.txt",
        "device_readings.txt",
        "alerts.txt",
        "interventions.txt",
        "time_log.txt",
        "outcomes.txt",
    ),
}
"""Tables only one class's reader ever opens: copied through unchanged."""

_IDENTICAL_SCHEMA_TABLES: tuple[str, ...] = ("patient_lds.txt", "problem_list.txt", "notes.txt")
"""Shared tables whose column names already agree across every class that
carries them: concatenated, one header."""

_UNION_SCHEMA_TABLES: tuple[str, ...] = ("encounter_dx.txt",)
"""Shared tables whose column *names* differ by class: merged to the ordered
union of every source's own columns, each row's foreign columns left blank."""


# ---------------------------------------------------------------------------
# Pipe-delimited table merging
# ---------------------------------------------------------------------------


def _read_table_lines(path: Path) -> tuple[list[str], list[str]]:
    """``(header columns, raw data lines)``, or ``([], [])`` if ``path``
    does not exist — a class that never wrote this shared table (episode has
    no ``notes.txt``, surgical has no ``problem_list.txt``)."""
    if not path.is_file():
        return [], []
    text = path.read_text(encoding="utf-8-sig")
    lines = [ln for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()]
    if not lines:
        return [], []
    header = [c.strip() for c in lines[0].split("|")]
    return header, lines[1:]


def merge_identical_schema(sources: dict[str, Path], filename: str, out_path: Path) -> int:
    """Concatenate ``filename`` from every source directory that has it.
    Every source must agree on the header exactly. Returns the row count
    written; writes nothing when no source carries the file."""
    header: list[str] | None = None
    rows: list[str] = []
    for cls in sorted(sources):
        cols, data = _read_table_lines(sources[cls] / filename)
        if not cols:
            continue
        if header is None:
            header = cols
        elif cols != header:
            msg = f"{filename}: {cls}'s header {cols} does not match {header}"
            raise ValueError(msg)
        rows.extend(data)
    if header is None:
        return 0
    out_path.write_text("|".join(header) + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return len(rows)


def merge_union_schema(sources: dict[str, Path], filename: str, out_path: Path) -> int:
    """Merge ``filename`` across sources whose column *names* may differ,
    into one file whose header is the ordered union of every source's own
    columns; each row keeps only its own source's columns filled, blank for
    the rest. See the module docstring for why that is safe here."""
    per_source: dict[str, tuple[list[str], list[str]]] = {}
    for cls in sorted(sources):
        cols, data = _read_table_lines(sources[cls] / filename)
        if cols:
            per_source[cls] = (cols, data)
    if not per_source:
        return 0

    union: list[str] = []
    for cls in sorted(per_source):
        for col in per_source[cls][0]:
            if col not in union:
                union.append(col)

    out_lines = ["|".join(union)]
    total = 0
    for cls in sorted(per_source):
        cols, data = per_source[cls]
        index = {c: i for i, c in enumerate(cols)}
        for line in data:
            cells = [c.strip() for c in line.split("|")]
            row = [cells[index[c]] if c in index else "" for c in union]
            out_lines.append("|".join(row))
            total += 1
    out_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return total


def merge_clinical(sources: dict[str, Path], out_dir: Path) -> None:
    """Merge any number of classes' own ``clinical/`` directories into
    ``out_dir``. ``sources`` maps class name to that class's own
    ``clinical/`` directory (a freshly generated one, or a committed
    fixture's own — either works, since this only ever reads files, never
    the generator's other state)."""
    out_dir.mkdir(parents=True, exist_ok=True)

    for cls, tables in _CLASS_ONLY_TABLES.items():
        src = sources.get(cls)
        if src is None:
            continue
        for name in tables:
            path = src / name
            if path.is_file():
                shutil.copy2(path, out_dir / name)

    for name in _IDENTICAL_SCHEMA_TABLES:
        merge_identical_schema(sources, name, out_dir / name)
    for name in _UNION_SCHEMA_TABLES:
        merge_union_schema(sources, name, out_dir / name)

    note_sources = [
        sources[cls] / "notes" for cls in sorted(sources) if (sources[cls] / "notes").is_dir()
    ]
    if note_sources:
        notes_out = out_dir / "notes"
        notes_out.mkdir(exist_ok=True)
        for notes_dir in note_sources:
            for f in sorted(notes_dir.iterdir()):
                shutil.copy2(f, notes_out / f.name)


# ---------------------------------------------------------------------------
# 835/837 merging: class-prefixed filenames, offset envelope control numbers
# ---------------------------------------------------------------------------

_ENVELOPE_FIELD: dict[str, int] = {
    "ISA": 13, "IEA": 2, "GS": 6, "GE": 2, "ST": 2, "SE": 2, "BHT": 3,
}  # fmt: skip
"""0-indexed field position of the control number in each envelope segment,
as every generator in this package writes it (see ``write_remittance``/
``write_claims`` in ``make_synthetic_dataset.py``)."""
_PAD9 = frozenset({"ISA", "IEA"})
_PAD4 = frozenset({"ST", "SE", "BHT"})


def _bump_envelope_control(text: str, offset: int) -> str:
    """Add ``offset`` to the control number in every ISA/IEA/GS/GE/ST/SE/BHT
    segment of one EDI file's text, preserving each field's own zero-padding.
    Any other segment (CLP, SVC, CLM, SV1, ...) is left untouched — this
    only ever rewrites the outer envelope, see the module docstring."""
    if offset == 0:
        return text
    out_lines: list[str] = []
    for line in text.split("\n"):
        if not line:
            out_lines.append(line)
            continue
        tag = line.split("*", 1)[0]
        idx = _ENVELOPE_FIELD.get(tag)
        terminated = line.endswith("~")
        body = line[:-1] if terminated else line
        fields = body.split("*")
        if idx is None or idx >= len(fields) or not fields[idx].strip().isdigit():
            out_lines.append(line)
            continue
        bumped = int(fields[idx]) + offset
        if tag in _PAD9:
            fields[idx] = f"{bumped:09d}"
        elif tag in _PAD4:
            fields[idx] = f"{bumped:04d}"
        else:
            fields[idx] = str(bumped)
        out_lines.append("*".join(fields) + ("~" if terminated else ""))
    return "\n".join(out_lines)


def merge_edi(sources: dict[str, Path], out_dir: Path, subdir: str) -> int:
    """Merge one of ``remittance``/``claims`` across classes: every ``*.edi``
    file is copied under a class-prefixed name with its envelope control
    numbers offset by :data:`CONTROL_OFFSET`. Returns the file count."""
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for cls in sorted(sources):
        src = sources[cls] / subdir
        if not src.is_dir():
            continue
        offset = CONTROL_OFFSET.get(cls, 0)
        for f in sorted(src.glob("*.edi")):
            text = f.read_text(encoding="utf-8")
            (out_dir / f"{cls}_{f.name}").write_text(
                _bump_envelope_control(text, offset), encoding="utf-8"
            )
            n += 1
    return n


# ---------------------------------------------------------------------------
# Driving the three generators
# ---------------------------------------------------------------------------


def _generate_class(cls: str, tmp_root: Path, scale: int) -> Path:
    """Run one class's own generator into ``tmp_root/<cls>``, at ``scale``.
    Never touches that class's committed fixture — a distinct scratch
    directory every time.

    Calls each class's own ``build_fixture`` in-process now (each generator
    used to be run as a subprocess script; the RNG each one seeds is its own
    ``random.Random`` instance, so calling the function directly instead of
    shelling out to the script changes nothing about the bytes written)."""
    out = tmp_root / cls
    # Match the committed single-class fixtures at scale 1: the surgical one
    # was built with the study cohort scaled x10 (60 cases per code, above the
    # suppression floor) and the comparator cohort unscaled.
    if cls == "surgical":
        surgical_mod.build_fixture(out, 10 * scale, scale)
    elif cls == "visit":
        visit_mod.build_fixture(out, scale)
    else:
        episode_mod.build_fixture(out, scale)
    return out


_README = """\
# Mixed-synthetic dataset

**Fabricated data. No patient, encounter, clinician, payer contract or dollar
amount here corresponds to anything real.** It exists so `pipeline.run` can
be exercised on one extract holding OR cases, clinic visits and monitoring
episodes side by side, sharing one 835/837 feed — the shape a real partner
delivery actually takes (CONTRACT-PACKS-MC.md, "a real partner delivery is
one extract with OR cases, clinic visits and monitoring episodes side by
side").

Built by `tools/make_mixed_dataset.py`, which runs the surgical
(`make_synthetic_dataset.py`), visit (`make_visit_dataset.py`) and episode
(`make_episode_dataset.py`) generators each into their own scratch directory
and merges the three `clinical/`, `remittance/` and `claims/` directories
into this one. It does not regenerate, and never writes into, any of the
three single-class fixtures those generators otherwise produce
(`mssm-synthetic/`, `visit-synthetic/`, `episode-synthetic/`).

Regenerate it with `just build-dataset mixed`. It is deterministic: every
class's own generator is (each individually documented in its own module
docstring), so the merge is too.

## What is here

Every table `cases.py`/`visits.py`/`episodes.py` reads, for all three
classes, in one `clinical/` directory: `or_log.txt` (surgical's anchor),
`visit.txt` (visit's), `episode.txt` (episode's), and every table each class
needs beside its own anchor. Three tables are shared rather than duplicated:
`patient_lds.txt` and `problem_list.txt` are a straight union of rows (every
class that carries them uses the same columns); `encounter_dx.txt` is a
union of columns as well as rows, since surgical's and visit's own column
names for the same idea (`seq`/`poa_yn` vs `sequence`/`addressed`) differ.
`notes/` merges surgical's and visit's own note files (episode carries none).

`remittance/` and `claims/` hold every class's own 835/837 files, prefixed
by class (`surgical_835_...edi`, `visit_835_...edi`, `episode_835_...edi`)
so the three can never collide on disk, with each class's envelope control
numbers (ISA/GS/ST/SE/GE/IEA) offset so they do not collide inside the
files either. See `tools/make_mixed_dataset.py`'s own module docstring for
exactly which id spaces were checked non-colliding and why (billing account
ids, patient ids, encounter/visit/episode ids, CSNs) and which numbers were
deliberately left alone (835 TRN02, CLP07 — trace numbers neither X12 nor
this package's readers require to be globally unique).

## Running it

```
uv run worth-cli run --clinical mixed-synthetic/clinical \\
    --remittance mixed-synthetic/remittance --locality NY01
uv run worth-cli compare --domains --clinical mixed-synthetic/clinical \\
    --remittance mixed-synthetic/remittance --locality NY01
```

`pipeline.run` resolves `extracts.CombinedExtract` for this directory (three
anchors present) and produces a `Run` with three `ClassRun`s, one per class,
each with its own packaged pack (`surgical-v1`, `visit-em-v1`,
`episode-rpm-v1`) and its own place-of-service setting (facility for
surgical, non-facility for visit and episode) — nothing passed, the class
defaults resolving exactly as they do for each single-class fixture.
"""


def build_fixture(out_dir: Path, scale: int = 1) -> None:
    """Build the committed mixed-synthetic fixture (or a scaled variant of
    its exact shape) into ``out_dir``, by composing the three classes' own
    ``build_fixture`` at ``scale``. See :func:`build` for the scenario-aware
    entry point."""
    with tempfile.TemporaryDirectory(prefix="worth-mixed-") as tmp:
        tmp_root = Path(tmp)
        class_dirs = {cls: _generate_class(cls, tmp_root, scale) for cls in CLASS_ORDER}

        for owned in ("clinical", "remittance", "claims"):
            shutil.rmtree(out_dir / owned, ignore_errors=True)

        merge_clinical({cls: d / "clinical" for cls, d in class_dirs.items()}, out_dir / "clinical")
        remits = merge_edi(class_dirs, out_dir / "remittance", "remittance")
        claims_n = merge_edi(class_dirs, out_dir / "claims", "claims")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "README.md").write_text(_README, encoding="utf-8")

    anchor_names = {"or_log.txt", "visit.txt", "episode.txt"}
    anchors = sorted(f.name for f in (out_dir / "clinical").glob("*.txt") if f.name in anchor_names)
    print(f"mixed dataset ({', '.join(anchors)}) -> {out_dir}")
    print(f"  remittance {remits} 835 files, claims {claims_n} 837 files")


# ---------------------------------------------------------------------------
# Scenario-aware entry point (CONTRACT-SEEDS.md, "worth track S1")
# ---------------------------------------------------------------------------


def _build_class(cls: str, out: Path, *, scenario: Scenario, seed: int, scale: int) -> None:
    """One class's own scenario-aware ``build()``, dispatched the same way
    :func:`_generate_class` dispatches ``build_fixture`` -- an explicit
    branch per class rather than a ``dict[str, ModuleType]``, since the
    three modules' ``build`` functions are structurally identical but not
    the same object mypy can call through a module-typed value."""
    if cls == "surgical":
        surgical_mod.build(out, scenario=scenario, seed=seed, scale=scale)
    elif cls == "visit":
        visit_mod.build(out, scenario=scenario, seed=seed, scale=scale)
    else:
        episode_mod.build(out, scenario=scenario, seed=seed, scale=scale)


def build(out_dir: Path, *, scenario: Scenario, seed: int | None = None, scale: int = 1) -> None:
    """The scenario-aware entry point ``worth-cli synth`` calls for
    ``--class mixed`` (CONTRACT-SEEDS.md's "worth track S1"): the same
    ``scenario``, generated by all three per-class ``build()`` entry points
    into their own scratch directories at the same seed and scale, then
    merged exactly as :func:`build_fixture` merges the baseline fixtures.

    A broken scenario's corruption is applied independently by each of the
    three classes to its own anchor/table/835 (every per-class ``build()``
    already handles ``scenario.broken`` on its own directory) -- the merged
    directory carries all three classes' corruption, so the planted failure
    does not depend on which class's anchor or file a reader happens to
    reach first.

    ``truth.json``'s ``linkage_rate``/``suppressed_codes`` are read back from
    the merged directory the same way a single-class ``build()`` reads its
    own (:mod:`worth_complexity.synthetic.planted`'s ``_anchor`` finds
    whichever anchor is present, ``or_log.txt`` first): for a non-broken
    mixed scenario that reports the surgical component's own numbers, not a
    three-class pool -- a known simplification, not a three-way average.
    ``missingness_max`` is the real maximum across all three classes' own
    marker coverage, computed per class before the merge; ``multi-site``'s
    ``missingness_by_site`` is the union of all three classes' own (marker
    namespaces never collide across classes, so a union is exact, not an
    average); ``policy-change``'s ``ratio.study_pre``/``study_post`` are the
    same closed-form fee-step band ``surgical.build``/``episode.build`` each
    write on their own (:func:`worth_complexity.synthetic.pricing.
    ratio_band` times :data:`worth_complexity.synthetic.pricing.
    FEE_SCHEDULE_STEP`), not the visit class's own pipeline-read-back
    maternity-bundle band (that band needs the merged directory's own
    835/837 pair, computed after the merge, which is more than this
    already-simplified truth is worth doing twice). ``method1-heavy``'s
    ``flags`` are the sum of all three classes' own ``missed``/
    ``mismatched``/``no_code``/``n``; ``payer-friction``'s ``denial_rate``/
    ``downcode_rate`` are the plain average of whichever classes planted
    them and ``friction_loss`` their sum -- each class's own truth.json,
    read back after its own ``build()`` already wrote it, not re-derived
    from the merged 835s.
    """
    from worth_complexity.synthetic import planted, pricing, sites
    from worth_complexity.synthetic import truth as truth_mod
    from worth_complexity.version import __version__

    seed_value = seed if seed is not None else SEED

    per_class_missingness: list[float] = []
    site_missingness: dict[str, dict[str, list[float]]] = {}
    m1_flags: dict[str, int] | None = None
    denial_rates: list[float] = []
    downcode_rates: list[float] = []
    friction_loss_total = 0.0
    with tempfile.TemporaryDirectory(prefix="worth-mixed-") as tmp:
        tmp_root = Path(tmp)
        class_dirs: dict[str, Path] = {}
        for cls in CLASS_ORDER:
            d = tmp_root / cls
            _build_class(cls, d, scenario=scenario, seed=seed_value, scale=scale)
            class_dirs[cls] = d
            if not scenario.broken and (scenario.dirt or scenario.multi_site):
                per_class_missingness.append(planted.missingness_max(d / "clinical", cls))
            if not scenario.broken and scenario.multi_site:
                for site, markers in planted.missingness_by_site(d / "clinical", cls).items():
                    site_missingness.setdefault(site, {}).update(markers)
            if not scenario.broken:
                # Each class's own build() already wrote its own truth.json
                # with its own Method 1 flags and friction summary (see
                # surgical.py/visit.py/episode.py); sum/average the three
                # rather than re-deriving them from the merged directory,
                # the same simplification this function's own docstring
                # already makes for linkage_rate.
                class_planted = truth_mod.read(d).planted
                class_flags = class_planted.get("flags")
                if scenario.m1_omission_rate and isinstance(class_flags, dict):
                    if m1_flags is None:
                        m1_flags = {"missed": 0, "mismatched": 0, "no_code": 0, "n": 0}
                    for key in ("missed", "mismatched", "no_code", "n"):
                        m1_flags[key] += int(class_flags.get(key, 0))
                if "denial_rate" in class_planted:
                    denial_rates.append(float(class_planted["denial_rate"]))
                if "downcode_rate" in class_planted:
                    downcode_rates.append(float(class_planted["downcode_rate"]))
                if "friction_loss" in class_planted:
                    friction_loss_total += float(class_planted["friction_loss"])

        for owned in ("clinical", "remittance", "claims"):
            shutil.rmtree(out_dir / owned, ignore_errors=True)

        merge_clinical({cls: d / "clinical" for cls, d in class_dirs.items()}, out_dir / "clinical")
        remits = merge_edi(class_dirs, out_dir / "remittance", "remittance")
        claims_n = merge_edi(class_dirs, out_dir / "claims", "claims")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "README.md").write_text(_README, encoding="utf-8")

    ratio_band = None
    ratio_bands = None
    if not scenario.broken:
        ratio_band = pricing.ratio_band(scenario.payment_rule, scenario.payment_share)
        if scenario.policy_date is not None:
            step = float(pricing.FEE_SCHEDULE_STEP)
            ratio_bands = {
                "study_pre": ratio_band,
                "study_post": [round(v * step, 4) for v in ratio_band],
            }
    planted_truth = planted.build_planted(
        scenario,
        root=out_dir,
        ratio_band=ratio_band,
        ratio_bands=ratio_bands,
        missingness_max=max(per_class_missingness) if per_class_missingness else 0.0,
        method0_verdict="flat" if scenario.payment_rule != "on-curve" else "rising",
        sites=len(sites.SITE_NPIS) if scenario.multi_site else None,
        site_missingness=site_missingness or None,
        periods=2 if scenario.policy_date is not None else None,
        policy_date_iso=scenario.policy_date.isoformat() if scenario.policy_date else None,
        distribution_shift=False if scenario.policy_date is not None else None,
        flags=m1_flags,
    )
    if denial_rates:
        planted_truth["denial_rate"] = round(sum(denial_rates) / len(denial_rates), 4)
    if downcode_rates:
        planted_truth["downcode_rate"] = round(sum(downcode_rates) / len(downcode_rates), 4)
    if friction_loss_total:
        planted_truth["friction_loss"] = round(friction_loss_total, 2)
    truth_mod.write(
        out_dir,
        truth_mod.Truth(
            scenario=scenario.name,
            encounter_class="mixed",
            seed=seed_value,
            generator_version=__version__,
            planted=planted_truth,
            purpose=truth_mod.render_purpose(scenario.name, planted_truth),
            notes=scenario.notes,
        ),
    )

    anchor_names = {"or_log.txt", "visit.txt", "episode.txt"}
    anchors = sorted(f.name for f in (out_dir / "clinical").glob("*.txt") if f.name in anchor_names)
    print(f"mixed dataset ({', '.join(anchors)}) -> {out_dir} [scenario={scenario.name}]")
    print(f"  remittance {remits} 835 files, claims {claims_n} 837 files")
