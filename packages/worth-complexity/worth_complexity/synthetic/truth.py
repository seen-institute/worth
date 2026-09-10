"""``truth.json``: what a generated scenario directory planted.

Written by every scenario ``build()`` (:mod:`worth_complexity.synthetic.
scenarios`), next to ``clinical/``, ``remittance/`` and ``claims/``. Read by
Meridian's Composition tab ("what was planted", beside the actual counts)
and by this package's own scenario tests, so the shape here is the contract
CONTRACT-SEEDS.md's catalog table describes:

    {"scenario": "parity", "encounter_class": "surgical", "seed": 1,
     "generator_version": "...",
     "planted": {"ratio": {"study": [0.95, 1.05]}, "method0_verdict": "rising",
                 "signature_pattern": ["structural-converging", "mixed"],
                 "linkage_rate": [1.0, 1.0],
                 "flags": {"missed": 0, "mismatched": 0, "no_code": ">=0"},
                 "suppressed_codes": [], "denial_rate": 0.0,
                 "missingness_max": 0.0, "expected_failure": None},
     "purpose": "Every study encounter is paid exactly what the comparator curve pays ...",
     "notes": ["..."]}

Bands are inclusive ``[low, high]``; a count may be an int or a
``">=N"``-style string. ``expected_failure`` (the broken group only) is
``{"stage": ..., "error": ..., "message_contains": ...}``.

``purpose`` is a one-or-two-sentence "why this dataset is useful to test
with" line, :func:`render_purpose`'s rendering of the scenario's own
:data:`worth_complexity.synthetic.scenarios.Scenario.purpose` template
against this directory's own ``planted`` values -- see that function and
``synthetic/README.md``'s "purpose" section for the placeholder list and
the bracketed-clause-dropping rule a value that was not planted follows.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, TypeGuard

_COUNT_RE = re.compile(r"^>=\d+$")


class TruthValidationError(Exception):
    """``truth.json`` does not have the shape the catalog promises."""


@dataclass(frozen=True)
class ExpectedFailure:
    stage: str
    error: str
    message_contains: str

    def to_json(self) -> dict[str, str]:
        return {"stage": self.stage, "error": self.error, "message_contains": self.message_contains}


@dataclass(frozen=True)
class Truth:
    """One generated directory's planted truth."""

    scenario: str
    encounter_class: str
    seed: int
    generator_version: str
    planted: dict[str, Any] = field(default_factory=dict)
    purpose: str = ""
    """Rendered by :func:`render_purpose` at the call site that builds this
    ``Truth`` (every class's ``build()``), from the scenario's own catalog
    template and this same ``planted`` dict -- not computed lazily here, so
    a caller that wants to inspect it before writing (or a test that wants
    to render it against a hand-built ``planted`` dict without a whole
    ``Truth``) can call :func:`render_purpose` directly."""
    notes: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "encounter_class": self.encounter_class,
            "seed": self.seed,
            "generator_version": self.generator_version,
            "planted": self.planted,
            "purpose": self.purpose,
            "notes": list(self.notes),
        }


def write(directory: Path, truth: Truth) -> None:
    """Write ``directory/truth.json``, sorted keys so regeneration is
    byte-stable too."""
    directory.mkdir(parents=True, exist_ok=True)
    text = json.dumps(truth.to_json(), indent=2, sort_keys=True) + "\n"
    (directory / "truth.json").write_text(text, encoding="utf-8")


def read(directory: Path) -> Truth:
    data = json.loads((directory / "truth.json").read_text(encoding="utf-8"))
    return Truth(
        scenario=data["scenario"],
        encounter_class=data["encounter_class"],
        seed=data["seed"],
        generator_version=data.get("generator_version", ""),
        planted=dict(data.get("planted", {})),
        purpose=data.get("purpose", ""),
        notes=tuple(data.get("notes", ())),
    )


def _is_band(value: object) -> TypeGuard[list[float]]:
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(v, int | float) for v in value)
        and value[0] <= value[1]
    )


def _is_count(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value >= 0
    return isinstance(value, str) and bool(_COUNT_RE.match(value))


def _is_fraction(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and 0.0 <= value <= 1.0


_OPTIONAL_CLAUSE_RE = re.compile(r"\[([^\[\]]*)\]")
"""Matches one ``[...]`` clause in a ``Scenario.purpose`` template -- see
:func:`render_purpose`."""


def _pct(value: float) -> str:
    """``0.834`` -> ``"83%"``: the whole-percent rendering every purpose
    placeholder that is a fraction in ``planted`` (denial rate, suppressed
    share, missingness, linkage) uses."""
    return f"{round(value * 100)}%"


def _format_policy_date(iso: str) -> str:
    """``"2027-01-01"`` -> ``"January 1, 2027"`` -- ``date.strftime``'s own
    ``%d`` pads to two digits (platform-dependent to strip), so this builds
    the string by hand instead."""
    d = date.fromisoformat(iso)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def _purpose_context(planted: dict[str, Any]) -> dict[str, str]:
    """Every placeholder a ``Scenario.purpose`` template may reference,
    already formatted as display text, for whichever of them this
    particular directory's ``planted`` dict actually has a value for. A
    placeholder missing from the returned dict is what
    :func:`render_purpose` treats as "not planted for this seed" -- its
    ``[bracketed clause]`` is dropped, and a reference to it outside any
    bracket (none in the catalog today) would raise.
    """
    ctx: dict[str, str] = {}

    ratio = planted.get("ratio")
    if isinstance(ratio, dict):
        study = ratio.get("study")
        if _is_band(study):
            ctx["ratio"] = f"{(study[0] + study[1]) / 2:.2f}"
        p90 = ratio.get("study_p90")
        if _is_band(p90):
            ctx["ratio_p90"] = f"{(p90[0] + p90[1]) / 2:.2f}"

    flags = planted.get("flags")
    if isinstance(flags, dict):
        for key in ("missed", "mismatched", "n"):
            value = flags.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                ctx[key] = str(value)

    denial_rate = planted.get("denial_rate")
    if isinstance(denial_rate, int | float) and not isinstance(denial_rate, bool):
        ctx["denial_rate"] = _pct(denial_rate)

    suppressed_share = planted.get("suppressed_share")
    if (
        isinstance(suppressed_share, int | float)
        and not isinstance(suppressed_share, bool)
        and suppressed_share > 0
    ):
        ctx["suppressed_share"] = _pct(suppressed_share)

    missingness_max = planted.get("missingness_max")
    if (
        isinstance(missingness_max, int | float)
        and not isinstance(missingness_max, bool)
        and missingness_max > 0
    ):
        ctx["missingness_max"] = _pct(missingness_max)

    linkage_rate = planted.get("linkage_rate")
    if _is_band(linkage_rate):
        ctx["linkage"] = _pct((linkage_rate[0] + linkage_rate[1]) / 2)

    policy_date = planted.get("policy_date")
    if isinstance(policy_date, str) and policy_date:
        ctx["policy_date"] = _format_policy_date(policy_date)

    failure = planted.get("expected_failure")
    if isinstance(failure, dict):
        if failure.get("stage"):
            ctx["stage"] = str(failure["stage"])
        if failure.get("error"):
            ctx["error"] = str(failure["error"])

    return ctx


def render_purpose(scenario_name: str, planted: dict[str, Any]) -> str:
    """Render ``scenarios.SCENARIOS[scenario_name].purpose``'s template
    against one generated directory's own ``planted`` dict: every
    ``{placeholder}`` filled from :func:`_purpose_context`, every
    ``[bracketed clause]`` dropped whole (rather than left with an unfilled
    placeholder) when a value it needs was not planted for this seed --
    the rendered sentence reads well either way. ``""`` for an unknown
    scenario name or one with no ``purpose`` template (there should be
    neither in the catalog, but keeps this function total rather than
    raising on a caller's typo).
    """
    from worth_complexity.synthetic.scenarios import SCENARIOS

    scenario = SCENARIOS.get(scenario_name)
    if scenario is None or not scenario.purpose:
        return ""

    context = _purpose_context(planted)

    def _render_optional(match: re.Match[str]) -> str:
        try:
            return match.group(1).format(**context)
        except KeyError:
            return ""

    rendered = _OPTIONAL_CLAUSE_RE.sub(_render_optional, scenario.purpose)
    return rendered.format(**context)


def validate(truth: Truth) -> None:
    """A small JSON-schema-like checker, not a full schema: required
    top-level fields, well-formed bands/counts/fractions for whichever
    ``planted`` keys are present, and the ``expected_failure`` shape for the
    broken group. Every scenario carries a different subset of ``planted``
    keys (the catalog table), so nothing here requires a key to be present
    that a particular scenario has no reason to plant -- it only rejects a
    present key with the wrong shape.
    """
    if not truth.scenario:
        raise TruthValidationError("truth.scenario is required")
    if truth.encounter_class not in {"surgical", "visit", "episode", "mixed"}:
        raise TruthValidationError(f"unknown encounter_class: {truth.encounter_class!r}")
    if not isinstance(truth.seed, int):
        raise TruthValidationError("truth.seed must be an int")
    if not isinstance(truth.planted, dict):
        raise TruthValidationError("truth.planted must be an object")
    if not isinstance(truth.purpose, str) or not truth.purpose:
        raise TruthValidationError("truth.purpose is required")
    if "{" in truth.purpose or "}" in truth.purpose:
        raise TruthValidationError(
            f"truth.purpose has an unrendered placeholder: {truth.purpose!r}"
        )

    planted = truth.planted

    failure = planted.get("expected_failure")
    if failure is not None:
        if not isinstance(failure, dict):
            raise TruthValidationError("expected_failure must be an object")
        for key in ("stage", "error", "message_contains"):
            if not isinstance(failure.get(key), str) or not failure[key]:
                raise TruthValidationError(f"expected_failure.{key} must be a non-empty string")
        # A broken directory plants nothing else worth checking: the run
        # never reaches a stage that would produce it.
        return

    if "ratio" in planted:
        ratio = planted["ratio"]
        if not isinstance(ratio, dict) or not ratio:
            raise TruthValidationError("planted.ratio must be a non-empty object of bands")
        for key, band in ratio.items():
            if not _is_band(band):
                raise TruthValidationError(
                    f"planted.ratio[{key!r}] is not a [low, high] band: {band!r}"
                )

    if "linkage_rate" in planted and not _is_band(planted["linkage_rate"]):
        raise TruthValidationError(
            f"planted.linkage_rate is not a band: {planted['linkage_rate']!r}"
        )

    if "flags" in planted:
        flags = planted["flags"]
        if not isinstance(flags, dict):
            raise TruthValidationError("planted.flags must be an object")
        for key, value in flags.items():
            if not _is_count(value):
                raise TruthValidationError(f"planted.flags[{key!r}] is not a count: {value!r}")

    if "suppressed_codes" in planted:
        codes = planted["suppressed_codes"]
        if not isinstance(codes, list) or not all(isinstance(c, str) for c in codes):
            raise TruthValidationError("planted.suppressed_codes must be a list of strings")

    for fraction_key in (
        "denial_rate",
        "missingness_max",
        "downcode_rate",
        "friction_loss",
        "suppressed_share",
    ):
        if fraction_key not in planted:
            continue
        if fraction_key == "friction_loss":
            # friction_loss is a dollar figure or a boolean-ish ">0" claim in
            # this catalog, not a fraction -- only require it is a number.
            if not isinstance(planted[fraction_key], int | float):
                raise TruthValidationError("planted.friction_loss must be numeric")
        elif not _is_fraction(planted[fraction_key]):
            raise TruthValidationError(f"planted.{fraction_key} must be a fraction in [0, 1]")

    if "signature_pattern" in planted:
        pattern = planted["signature_pattern"]
        if not isinstance(pattern, list) or not all(isinstance(p, str) for p in pattern):
            raise TruthValidationError("planted.signature_pattern must be a list of strings")

    if "method0_verdict" in planted and not isinstance(planted["method0_verdict"], str):
        raise TruthValidationError("planted.method0_verdict must be a string")

    if "periods" in planted and not _is_count(planted["periods"]):
        raise TruthValidationError("planted.periods must be a count")

    if "sites" in planted and not _is_count(planted["sites"]):
        raise TruthValidationError("planted.sites must be a count")

    if "policy_date" in planted and not isinstance(planted["policy_date"], str):
        raise TruthValidationError("planted.policy_date must be an ISO date string")

    if "distribution_shift" in planted and not isinstance(planted["distribution_shift"], bool):
        raise TruthValidationError("planted.distribution_shift must be a bool")

    if "missingness_by_site" in planted:
        by_site = planted["missingness_by_site"]
        if not isinstance(by_site, dict) or not by_site:
            raise TruthValidationError("planted.missingness_by_site must be a non-empty object")
        for site, markers in by_site.items():
            # An individual site's own object may be empty -- a fully
            # documented site (no marker ever missing there) is a real,
            # valid reading, not a malformed one.
            if not isinstance(markers, dict):
                raise TruthValidationError(
                    f"planted.missingness_by_site[{site!r}] must be an object"
                )
            for marker_id, band in markers.items():
                if not _is_band(band):
                    raise TruthValidationError(
                        f"planted.missingness_by_site[{site!r}][{marker_id!r}] "
                        f"is not a band: {band!r}"
                    )
