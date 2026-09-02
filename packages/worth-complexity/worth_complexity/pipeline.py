"""One run, end to end: files in, index records out.

The engine reads a directory and writes a result. It does no network calls,
takes no credentials, reads no clock and imports no cloud SDK. That is not
minimalism for its own sake, it is what makes "run our code inside your
environment" a deployment target rather than an eighteen-month rewrite, and it
is what lets a partner's security review conclude that nothing can leave.

Three things a run needs that the extract does not carry: the institution's
Medicare locality, the place-of-service setting, and the fee schedule itself.
The first two are parameters, refused rather than guessed. The third defaults
to the fixture ``worth-fees`` ships and can be supplied by a caller holding
the full release.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from worth_fees import PlaceOfService
from worth_fees.sources import vintage_for, vintage_for_date

from worth_complexity import cases, markers, scoring, x12
from worth_complexity.adequacy import (
    Multiplier,
    Observation,
    PricedEncounter,
    SchedulePool,
    adequacy,
    fixture_schedules,
    index_records,
    method_zero_slopes,
    payer_multipliers,
    price_comparators,
    schedule_curve,
)
from worth_complexity.linkage import Linkage, link
from worth_complexity.models import (
    LAYER_A,
    Adequacy,
    Cohort,
    ExtrapolationError,
    IndexRecord,
    blind_payers,
)
from worth_complexity.rulepack import RulePack, load
from worth_complexity.version import __version__

if TYPE_CHECKING:
    from collections.abc import Callable

    from worth_fees.sources import Vintage

    from worth_complexity.curve import Fit

STAGES: tuple[str, ...] = (
    "extract",
    "encounters",
    "markers",
    "score",
    "remittance",
    "link",
    "price",
    "curve",
    "multipliers",
    "slopes",
    "adequacy",
    "records",
)
"""The stage names :func:`run` reports to its ``progress`` callback, in order.

The engine reads no clock. A caller that wants to know how long each stage took
passes a callback and times the gaps itself, so the timing lives with whoever
asked for it and the arithmetic stays free of anything a referee cannot re-run.
"""


@dataclass(frozen=True, slots=True)
class Run:
    """Everything one pass over a partner extract produced."""

    pack: RulePack
    provenance_filter: tuple[str, ...]
    locality: str
    locality_name: str
    setting: PlaceOfService
    reference: Vintage
    """The CMS release the schedule curve was fitted at."""
    linkage: Linkage
    observations: tuple[Observation, ...]
    priced: dict[str, PricedEncounter]
    """Every comparator encounter's fee-schedule amounts, by encounter id."""
    schedule_curve: Fit
    """The fee schedule's complexity relation, fitted on the comparator cohort."""
    payer_labels: dict[str, str]
    """Blinded label per payer id. The identities stay where the data stays."""
    multipliers: dict[str, Multiplier]
    """Each payer's multiple of the schedule, by payer id. Partner-only."""
    thin_payers: tuple[str, ...]
    """Payers with too few comparator encounters for a multiplier, with the reason."""
    slopes: dict[tuple[str, str], Fit]
    thin_strata: tuple[str, ...]
    """(code, payer) strata with too few encounters to fit a Method 0 slope."""
    adequacies: tuple[Adequacy, ...]
    records: tuple[IndexRecord, ...]
    """One per study code: the unit the registry accumulates."""
    extrapolated: tuple[str, ...]
    """Study encounters excluded because they scored outside the fitted curve."""
    unmultiplied: tuple[str, ...]
    """Study encounters excluded because their payer has no multiplier."""

    @property
    def study(self) -> tuple[Observation, ...]:
        return tuple(o for o in self.observations if o.cohort is Cohort.STUDY)

    @property
    def comparator(self) -> tuple[Observation, ...]:
        return tuple(o for o in self.observations if o.cohort is Cohort.COMPARATOR)


def run(
    extract_dir: Path,
    remittance_dir: Path,
    *,
    locality: str,
    setting: PlaceOfService | str = PlaceOfService.FACILITY,
    schedules: SchedulePool | None = None,
    reference: tuple[int, int] | None = None,
    pack_name: str = "gyn-surgical-v1",
    provenance_filter: frozenset[str] = LAYER_A,
    progress: Callable[[str], None] | None = None,
) -> Run:
    """Score, link, price, fit and divide.

    Args:
        extract_dir: The clinical extract: five delimited tables and the notes.
        remittance_dir: The 835 files.
        locality: The institution's Medicare locality, e.g. ``NY01``. Required:
            the extract does not carry it and guessing it would misprice every
            comparator encounter silently.
        setting: ``facility`` for OR cases, the default.
        schedules: Where fee schedules come from. Default: the worth-fees fixture.
        reference: The (rule year, quarter) the schedule curve is fitted at.
            Default: the release in force on the latest service date.
        progress: Called with each name in :data:`STAGES` as that stage begins.
    """
    place = PlaceOfService(setting)
    pool = schedules or fixture_schedules

    def stage(name: str) -> None:
        if progress is not None:
            progress(name)

    pack = load(pack_name)
    stage("extract")
    extract = cases.read_extract(extract_dir)
    stage("encounters")
    encounters = cases.encounters(extract)
    stage("markers")
    marker_sets = markers.extract(extract, encounters, pack)

    stage("score")
    scored = {
        enc.encounter_id: scoring.score(enc, marker_sets[enc.encounter_id], pack, provenance_filter)
        for enc in encounters
    }

    stage("remittance")
    remit_lines = x12.read_directory(remittance_dir)
    stage("link")
    linkage = link(encounters, remit_lines)
    observations = tuple(
        Observation(scored[le.encounter.encounter_id], le) for le in linkage.linked
    )
    labels = blind_payers(o.payer_id for o in observations)

    stage("price")
    reference_vintage = (
        vintage_for(*reference)
        if reference is not None
        else vintage_for_date(max(e.service_date for e in encounters))
    )
    priced = price_comparators(
        observations,
        locality=locality,
        setting=place,
        schedules=pool,
        reference=reference_vintage,
    )
    # worth-fees normalises the locality (``ny-01`` becomes ``NY01``) and names
    # it; take both from what it actually priced.
    first = next(iter(priced.values()), None)
    priced_locality = first.at_reference.locality if first else locality.strip().upper()
    locality_name = first.at_reference.locality_name if first else ""

    stage("curve")
    curve = schedule_curve(observations, priced, reference_vintage)
    stage("multipliers")
    multipliers, thin_payers = payer_multipliers(observations, priced, labels)
    stage("slopes")
    slopes, thin_strata = method_zero_slopes(observations, labels)

    stage("adequacy")
    results: list[Adequacy] = []
    extrapolated: list[str] = []
    unmultiplied: list[str] = []
    for obs in observations:
        if obs.cohort is not Cohort.STUDY:
            continue
        multiplier = multipliers.get(obs.payer_id)
        if multiplier is None:
            unmultiplied.append(obs.scored.encounter.encounter_id)
            continue
        try:
            results.append(
                adequacy(
                    obs,
                    curve,
                    multiplier,
                    reference=reference_vintage,
                    locality=priced_locality,
                    setting=place,
                )
            )
        except ExtrapolationError:
            extrapolated.append(obs.scored.encounter.encounter_id)

    stage("records")
    records = index_records(
        observations,
        tuple(results),
        institution=next((e.facility_npi for e in encounters), "unknown"),
        labels=labels,
        linkage_rate=linkage.rate,
        pack=pack,
        provenance_filter=tuple(sorted(provenance_filter)),
        package_version=__version__,
        locality=priced_locality,
        setting=place,
        reference=reference_vintage,
    )

    return Run(
        pack=pack,
        provenance_filter=tuple(sorted(provenance_filter)),
        locality=priced_locality,
        locality_name=locality_name,
        setting=place,
        reference=reference_vintage,
        linkage=linkage,
        observations=observations,
        priced=priced,
        schedule_curve=curve,
        payer_labels=labels,
        multipliers=multipliers,
        thin_payers=thin_payers,
        slopes=slopes,
        thin_strata=thin_strata,
        adequacies=tuple(results),
        records=records,
        extrapolated=tuple(extrapolated),
        unmultiplied=tuple(unmultiplied),
    )
