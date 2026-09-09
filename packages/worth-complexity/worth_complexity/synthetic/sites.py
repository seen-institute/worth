"""Facility assignment and per-site documentation habits for the
``multi-site`` scenario (CONTRACT-SEEDS.md's catalog).

Three facility NPIs stand in for three physical sites. Site A is the
existing single-site NPI every class's baseline fixture already writes
(``edi.FACILITY_NPI``, ``"1234567893"``) -- unaffected, full documentation.
Sites B and C are new, and each has one documentation habit that a real
multi-site rollup would actually see:

* **Site B** under-documents the narrative markers its class has: 60% of the
  time, the note a rule-based marker would read (the surgical operative
  note's adhesion/anatomic-extent language, the visit note's shared
  decision-making language) is not written at all. The marker is then
  genuinely absent for that encounter -- decision 6, "missing markers
  default to zero" -- not merely a note that mentions nothing, which would
  still score a legitimate zero.
* **Site C** leaves one structured field blank: the surgical class's
  ``or_log.txt`` ``ebl_ml`` (:mod:`worth_complexity.markers` already treats
  a blank as a missing ``estimated_blood_loss_ml`` marker, the same
  omit-rather-than-fail treatment as a blank operative-time timestamp), and
  the episode class's ``time_log.txt`` (a fraction of episodes carry no time
  log rows at all, so :mod:`worth_complexity.episodes`'s
  ``oversight_minutes`` marker is omitted rather than reported as a
  legitimate zero).

The episode class has no note dataset and so nothing for site B's habit to
touch -- an episode still gets assigned one of the three site NPIs (the
"three sites" the scenario plants), but only site C's structured-field
habit changes what it reads back as. Every rate below is deterministic
given the caller's own ``random.Random``, drawn in id order so the same seed
always assigns the same sites and the same drop set.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from typing import Protocol

SITE_A = "1234567893"
"""The existing single-site NPI (``edi.FACILITY_NPI``). Full documentation."""
SITE_B = "1245319599"
"""Under-documents narrative markers (adhesion/anatomic-extent, shared
decision-making) 60% of the time -- :data:`SITE_B_NOTE_OMISSION_RATE`."""
SITE_C = "1972604431"
"""Leaves a structured field blank/missing most of the time -- surgical's
EBL (:data:`SITE_C_EBL_BLANK_RATE`), episode's time log
(:data:`SITE_C_TIME_LOG_DROP_RATE`)."""

SITE_NPIS: tuple[str, str, str] = (SITE_A, SITE_B, SITE_C)

SITE_B_NOTE_OMISSION_RATE = 0.6
"""Site B's operative/visit note is not written at all, this fraction of
the time -- CONTRACT-SEEDS.md's catalog: "site B's ... notes omit the
adhesion, anatomic-extent and shared-decision language 60% of the time".
Withholding the whole note is what actually registers as *missing* for the
narrative markers that read only that note type (structured markers, and
any other note type, are unaffected); a note that merely never uses the
tracked phrases would still score a true, present zero, not a missing
marker."""
SITE_C_EBL_BLANK_RATE = 0.95
"""Surgical: fraction of site-C ``or_log.txt`` rows with a blank
``ebl_ml``. High enough that ``estimated_blood_loss_ml`` missingness at
site C comfortably clears the planted >= 0.9 band regardless of how the
site draw itself lands."""
SITE_C_TIME_LOG_DROP_RATE = 0.4
"""Episode: fraction of site-C episodes with every ``time_log.txt`` row
withheld -- CONTRACT-SEEDS.md's catalog: "site C's ... episode time log is
missing 40% of entries", read here as 40% of episodes' logs being entirely
absent (the granularity at which a missing log actually registers as a
missing ``oversight_minutes`` marker; see :mod:`worth_complexity.episodes`'s
``_oversight_minutes``), rather than 40% of individual rows, most of which
would leave an episode with a real, present, merely-smaller total."""


class _HasSiteNpi(Protocol):
    site_npi: str


def assign_sites(records: Sequence[_HasSiteNpi], *, rng: random.Random) -> None:
    """Set ``record.site_npi`` on every record to one of :data:`SITE_NPIS`,
    drawn independently and roughly evenly. Mutates in place, in record
    order, so the assignment is a deterministic function of ``rng``'s state
    when this is called and nothing else."""
    for record in records:
        record.site_npi = rng.choice(SITE_NPIS)


def sample_ids[R: _HasSiteNpi](
    records: Sequence[R],
    *,
    rng: random.Random,
    site: str,
    rate: float,
    id_of: Callable[[R], str],
) -> frozenset[str]:
    """The ids of whichever ``records`` are at ``site`` and independently
    drawn at ``rate`` -- one Bernoulli draw per record at that site, in
    record order, so this is deterministic given ``rng``'s state. ``id_of``
    is a ``record -> str`` accessor, since the three classes' per-record id
    attribute (``log_id``, ``visit_id``, ``episode_id``) is not the same
    name on any two of them."""
    return frozenset(
        id_of(record) for record in records if record.site_npi == site and rng.random() < rate
    )
