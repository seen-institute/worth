"""Payer friction applied to an already-generated scenario directory's
remittance: denials, downcodes, reversal-then-corrected pairs, and a payer
that never pays the modifier-22 line. Text-level, after generation -- the
same technique :mod:`worth_complexity.synthetic.dirt` uses, reused here
(:func:`worth_complexity.synthetic.dirt._all_835_blocks`) because a
``Scenario``'s friction knobs are read the same way across every class: the
835 files' shape (``ST/BPR/TRN/.../CLP/NM1/REF/DTM/SVC/DTM/AMT/CAS/.../SE``)
is identical for surgical, visit and episode.

Only the 835 side changes. The 837 keeps the original code and amount, which
is the whole point -- friction is only visible by comparing what was
submitted against what was allowed, denied or downcoded (the fixture
READMEs' own framing for Method 1's claims cross-check).
"""

from __future__ import annotations

import random
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from worth_complexity.synthetic.dirt import _all_835_blocks

if TYPE_CHECKING:
    from worth_complexity.synthetic.scenarios import Scenario

CENTS = Decimal("0.01")


class FrictionSummary(NamedTuple):
    denial_rate: float
    """Denied primary lines / all primary lines seen across every payer this
    scenario targeted with ``denial_share`` (the friction-scenario payer's
    own rate, not pooled across payers with no denial knob)."""
    downcode_rate: float
    friction_loss: Decimal
    """Total dollars removed from allowed amounts by denial, downcode and
    the modifier-22 payer, across the whole directory."""


def _payer_id_of(path: Path) -> str:
    # 835_{YYYYMMDD}_{payer_id}_{control}.edi
    return path.stem.split("_")[2]


def _deny_primary_line(lines: list[str], start: int, end: int) -> Decimal:
    """Zero the first SVC line's AMT*B6 in this claim block and mark it
    CO-97/denied. Rewrites lines in place only -- never inserts or deletes,
    so a block's ``(start, end)`` stays valid even when several claims in
    the same file each get denied in one pass. Returns the dollars removed."""
    billed = Decimal("0.00")
    removed = Decimal("0.00")
    in_target_line = False
    for i in range(start, end):
        line = lines[i]
        if line.startswith("SVC*"):
            if in_target_line:
                break  # a later service line on the same claim: stop here
            billed = Decimal(line.split("*")[2])
            in_target_line = True
        elif in_target_line and line.startswith("AMT*B6*"):
            removed = Decimal(line.split("*")[2].rstrip("~"))
            lines[i] = "AMT*B6*0.00~"
        elif in_target_line and line.startswith("CAS*"):
            lines[i] = f"CAS*CO*97*{billed:.2f}~"
    return removed


def _downcode_primary_line(lines: list[str], start: int, end: int) -> Decimal:
    """Shift the first SVC line's CPT by one digit and pay 80% of the
    original allowed amount; returns the dollars removed."""
    removed = Decimal("0.00")
    for i in range(start, end):
        if lines[i].startswith("SVC*"):
            parts = lines[i].split("*")
            composite = parts[1].split(":")
            cpt = composite[1]
            composite[1] = cpt[:-1] + str((int(cpt[-1]) - 1) % 10)
            parts[1] = ":".join(composite)
            lines[i] = "*".join(parts)
        elif lines[i].startswith("AMT*B6*"):
            original = Decimal(lines[i].split("*")[2].rstrip("~"))
            reduced = (original * Decimal("0.80")).quantize(CENTS)
            removed = original - reduced
            lines[i] = f"AMT*B6*{reduced:.2f}~"
            break
    return removed


def _deny_modifier22_line(lines: list[str], start: int, end: int) -> Decimal:
    """Zero AMT*B6 on whichever SVC line in this claim carries modifier 22,
    if any; returns the dollars removed (0 if the claim carries no
    modifier-22 line -- most won't)."""
    within = False
    for i in range(start, end):
        if lines[i].startswith("SVC*"):
            within = ":22" in lines[i].split("*")[1]
        elif within and lines[i].startswith("AMT*B6*"):
            removed = Decimal(lines[i].split("*")[2].rstrip("~"))
            lines[i] = "AMT*B6*0.00~"
            return removed
    return Decimal("0.00")


def _append_reversal_pair(lines: list[str], start: int, end: int, se_index: int) -> int:
    """Insert a reversal (CLP02=22) of this claim followed by a corrected
    resubmission, both before ``SE``. Returns how many lines were inserted."""
    original = lines[start:end]
    reversal = list(original)
    parts = reversal[0].split("*")
    parts[2] = "22"
    reversal[0] = "*".join(parts)
    corrected = list(original)
    inserted = [*reversal, *corrected]
    lines[se_index:se_index] = inserted
    return len(inserted)


def apply(root: Path, scenario: Scenario, *, rng: random.Random) -> FrictionSummary:
    """Apply every friction knob ``scenario`` sets, in a fixed order
    (denials, then downcodes, then modifier-22, then reversals) so results
    are deterministic given ``rng``. A no-op, zero-summary call when the
    scenario sets none of them (every class calls this unconditionally;
    cheap when there is nothing to do)."""
    remittance = root / "remittance"
    knobs = (
        scenario.denial_share,
        scenario.downcode_share,
        scenario.no_modifier22_payer,
        scenario.reversal_pairs,
    )
    if not any(knobs):
        return FrictionSummary(0.0, 0.0, Decimal("0.00"))

    file_lines, blocks = _all_835_blocks(remittance)
    total_loss = Decimal("0.00")

    total_lines_seen = len(blocks)
    denied = 0
    downcoded = 0

    for payer_id, share in scenario.denial_share.items():
        candidates = [b for b in blocks if _payer_id_of(b[0]) == payer_id]
        n = round(len(candidates) * share)
        for f, start, end, _account in rng.sample(candidates, k=min(n, len(candidates))):
            total_loss += _deny_primary_line(file_lines[f], start, end)
            denied += 1

    for payer_id, share in scenario.downcode_share.items():
        candidates = [b for b in blocks if _payer_id_of(b[0]) == payer_id]
        n = round(len(candidates) * share)
        for f, start, end, _account in rng.sample(candidates, k=min(n, len(candidates))):
            total_loss += _downcode_primary_line(file_lines[f], start, end)
            downcoded += 1

    if scenario.no_modifier22_payer is not None:
        candidates = [b for b in blocks if _payer_id_of(b[0]) == scenario.no_modifier22_payer]
        for f, start, end, _account in candidates:
            total_loss += _deny_modifier22_line(file_lines[f], start, end)

    if scenario.reversal_pairs:
        chosen = rng.sample(blocks, k=min(scenario.reversal_pairs, len(blocks)))
        # Group by file and insert from the bottom up so earlier indices in
        # the same file stay valid across multiple insertions.
        by_file: dict[Path, list[tuple[int, int]]] = {}
        for f, start, end, _account in chosen:
            by_file.setdefault(f, []).append((start, end))
        for f, ranges in by_file.items():
            lines = file_lines[f]
            se_index = next(i for i, ln in enumerate(lines) if ln.startswith("SE*"))
            for start, end in sorted(ranges, reverse=True):
                _append_reversal_pair(lines, start, end, se_index)

    for f, lines in file_lines.items():
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")

    denial_rate = denied / total_lines_seen if total_lines_seen else 0.0
    downcode_rate = downcoded / total_lines_seen if total_lines_seen else 0.0
    return FrictionSummary(denial_rate, downcode_rate, total_loss)
