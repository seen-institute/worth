"""Deriving ``truth.json``'s ``planted`` dict from a scenario's knobs and
what the generator actually wrote -- one implementation, shared by every
class's ``build()``, never a hand-typed band per (scenario, class) pair.

Linkage and suppression are read back from the files on disk after every
knob (payment rule, friction, dirt, broken) has already run, rather than
tracked knob-by-knob through generation: that is the one way the numbers
here are guaranteed to match what a reader of the directory would actually
find, regardless of which corruptions a scenario combined.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from worth_complexity.synthetic.dirt import _PROC_TABLE, _anchor, _read_rows

if TYPE_CHECKING:
    from worth_complexity.synthetic.friction import FrictionSummary
    from worth_complexity.synthetic.scenarios import Scenario

SUPPRESSION_FLOOR = 11

_EXPECTED_FAILURE = {
    "unpriceable-comparator": {
        "stage": "price",
        "error": "PricingError",
        "message_contains": "cannot be priced",
    },
    "malformed-table": {
        "stage": "extract",
        "error": "ExtractError",
        "message_contains": "missing required column",
    },
    "corrupt-835": {
        "stage": "remittance",
        "error": "X12Error",
        "message_contains": "AMT*B6",
    },
    "no-anchor": {
        "stage": "extract",
        "error": "ExtractError",
        # extracts.read_extract's own message (worth_complexity/extracts.py),
        # not cases.read_table's "missing extract file": dirt.remove_anchor
        # deletes whichever anchor is present and the directory is then read
        # through the generic dispatcher, which raises before ever calling a
        # class-specific reader like cases.read_extract.
        "message_contains": "no recognised extract anchor file",
    },
}


def linkage_rate(root: Path) -> float:
    """Distinct ``billing_account_id`` values in the anchor table that also
    appear as some remittance file's ``CLP01``, over the anchor table's own
    row count. 1.0 when nothing dropped an account or orphaned a claim."""
    clinical = root / "clinical"
    anchor = _anchor(clinical)
    header, rows = _read_rows(clinical / anchor)
    idx = header.index("billing_account_id")
    all_accounts = {r[idx] for r in rows}
    if not all_accounts:
        return 0.0
    remitted: set[str] = set()
    for f in (root / "remittance").glob("*.edi"):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.startswith("CLP*"):
                remitted.add(line.split("*")[1])
    return len(all_accounts & remitted) / len(all_accounts)


def per_code_counts(root: Path) -> dict[str, int]:
    """``{cpt: number of billed lines}`` from the class's own procedure
    table -- every line, not primary-only, so a study code's secondary/
    add-on lines count toward its own suppression check too."""
    clinical = root / "clinical"
    anchor = _anchor(clinical)
    proc_path = clinical / _PROC_TABLE[anchor]
    if not proc_path.is_file():
        return {}
    header, rows = _read_rows(proc_path)
    cpt_idx = header.index("cpt")
    counts: dict[str, int] = {}
    for row in rows:
        counts[row[cpt_idx]] = counts.get(row[cpt_idx], 0) + 1
    return counts


def suppressed_codes(counts: dict[str, int], *, floor: int = SUPPRESSION_FLOOR) -> list[str]:
    return sorted(code for code, n in counts.items() if n < floor)


def missingness_max(clinical: Path, encounter_class: str) -> float:
    """The highest, across every marker the class's packaged rule pack
    admits, of (encounters missing that marker / total encounters) --
    scored fresh from the files on disk, after every knob has already run,
    the same reason :func:`linkage_rate` reads the files back rather than
    tracking drops knob-by-knob through generation."""
    from worth_complexity import extracts
    from worth_complexity.models import LAYER_A
    from worth_complexity.pipeline import CLASS_DEFAULT_PACKS
    from worth_complexity.rulepack import load as load_pack
    from worth_complexity.scoring import score as score_encounter

    pack = load_pack(CLASS_DEFAULT_PACKS[encounter_class])
    extract = extracts.read_extract(clinical)
    encounters = extract.encounters()
    if not encounters:
        return 0.0
    marker_sets = extract.markers(encounters, pack)
    per_marker: dict[str, int] = {}
    for enc in encounters:
        scored = score_encounter(enc, marker_sets[enc.encounter_id], pack, LAYER_A)
        for marker_id in scored.missing:
            per_marker[marker_id] = per_marker.get(marker_id, 0) + 1
    if not per_marker:
        return 0.0
    return max(n / len(encounters) for n in per_marker.values())


def missingness_by_site(clinical: Path, encounter_class: str) -> dict[str, dict[str, list[float]]]:
    """``{site (facility NPI): {marker_id: [low, high]}}``, scored fresh
    from the files on disk -- the same reasoning as :func:`missingness_max`,
    broken out per site instead of taking the maximum across all of them.
    Reported as an inclusive band around each observed fraction, the same
    convention :func:`_dirty_linkage_band` uses, since which encounters a
    scenario's per-site draw actually lands on depends on the seed."""
    from worth_complexity import extracts
    from worth_complexity.models import LAYER_A
    from worth_complexity.pipeline import CLASS_DEFAULT_PACKS
    from worth_complexity.rulepack import load as load_pack
    from worth_complexity.scoring import score as score_encounter

    pack = load_pack(CLASS_DEFAULT_PACKS[encounter_class])
    extract = extracts.read_extract(clinical)
    encounters = extract.encounters()
    if not encounters:
        return {}
    marker_sets = extract.markers(encounters, pack)
    totals: dict[str, int] = {}
    missing_counts: dict[str, dict[str, int]] = {}
    for enc in encounters:
        site = enc.facility_npi
        totals[site] = totals.get(site, 0) + 1
        scored = score_encounter(enc, marker_sets[enc.encounter_id], pack, LAYER_A)
        bucket = missing_counts.setdefault(site, {})
        for marker_id in scored.missing:
            bucket[marker_id] = bucket.get(marker_id, 0) + 1

    out: dict[str, dict[str, list[float]]] = {}
    for site, markers in missing_counts.items():
        total = totals[site]
        out[site] = {
            marker_id: [round(max(0.0, n / total - 0.05), 4), round(min(1.0, n / total + 0.05), 4)]
            for marker_id, n in markers.items()
        }
    return out


def build_planted(
    scenario: Scenario,
    *,
    root: Path,
    friction_summary: FrictionSummary | None = None,
    ratio_band: list[float] | None = None,
    ratio_bands: dict[str, list[float]] | None = None,
    missingness_max: float = 0.0,
    method0_verdict: str = "flat",
    sites: int | None = None,
    site_missingness: dict[str, dict[str, list[float]]] | None = None,
    periods: int | None = None,
    policy_date_iso: str | None = None,
    distribution_shift: bool | None = None,
    flags: dict[str, int] | None = None,
) -> dict[str, Any]:
    """The ``planted`` dict for one generated directory. ``root`` is read
    back for linkage and suppression (see the module docstring); everything
    else is the caller's own knowledge of what it planted (the ratio band
    from :func:`worth_complexity.synthetic.pricing.ratio_band`, the
    method0 verdict the payment rule implies, ``flags`` the Method 1 counts
    ``knobs.omit_secondary_codes`` returned for ``method1-heavy``)."""
    if scenario.broken:
        return {"expected_failure": dict(_EXPECTED_FAILURE[scenario.broken])}

    counts = per_code_counts(root)
    codes_suppressed = suppressed_codes(counts)
    linkage_band = (
        _dirty_linkage_band(root) if scenario.dirt else [round(linkage_rate(root), 4)] * 2
    )
    planted: dict[str, Any] = {
        "linkage_rate": linkage_band,
        "suppressed_codes": codes_suppressed,
        "missingness_max": missingness_max,
        "method0_verdict": method0_verdict,
    }
    if counts:
        # Fraction of billed codes at or under the n=11 suppression floor --
        # a legitimate, always-computable reading for every scenario, not
        # only thin-cohorts (whose whole point is that this comes out
        # nonzero); the purpose text's {suppressed_share} placeholder is
        # this value.
        planted["suppressed_share"] = round(len(codes_suppressed) / len(counts), 4)
    if flags:
        planted["flags"] = dict(flags)
    if ratio_band is not None:
        planted["ratio"] = {"study": ratio_band}
    if ratio_bands:
        planted.setdefault("ratio", {}).update(ratio_bands)
    if friction_summary is not None and (
        friction_summary.denial_rate or friction_summary.downcode_rate
    ):
        planted["denial_rate"] = round(friction_summary.denial_rate, 4)
        planted["downcode_rate"] = round(friction_summary.downcode_rate, 4)
        planted["friction_loss"] = float(friction_summary.friction_loss)
    if sites is not None:
        planted["sites"] = sites
    if site_missingness:
        planted["missingness_by_site"] = site_missingness
    if periods is not None:
        planted["periods"] = periods
    if policy_date_iso is not None:
        planted["policy_date"] = policy_date_iso
    if distribution_shift is not None:
        planted["distribution_shift"] = distribution_shift
    return planted


def _dirty_linkage_band(root: Path) -> list[float]:
    """The dirty scenario reports an inclusive band around the observed
    rate (+/- 1 encounter's worth) rather than a point value, since which
    exact accounts a fractional ``share`` touches depends on the seed."""
    rate = linkage_rate(root)
    return [round(max(0.0, rate - 0.03), 4), round(min(1.0, rate + 0.01), 4)]
