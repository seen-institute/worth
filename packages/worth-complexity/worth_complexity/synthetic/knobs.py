"""Small generation-time knobs shared across classes, factored out because
every class's per-encounter record (``surgical.Encounter``,
``visit.VisitRecord``, ``episode.Episode``) carries a ``cpts: list[tuple[str,
str]]`` field the same shape, even though nothing else about the three
dataclasses is shared.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any, Protocol


class _HasCpts(Protocol):
    cpts: list[tuple[str, str]]


def omit_secondary_codes(items: Sequence[_HasCpts], *, rng: random.Random, rate: float) -> int:
    """For every item billing more than one code, drop its last (non-primary)
    code with probability ``rate`` -- the documented secondary step never
    makes it onto the claim. Returns how many were dropped."""
    dropped = 0
    for item in items:
        if len(item.cpts) > 1 and rng.random() < rate:
            item.cpts.pop()
            dropped += 1
    return dropped


def _month_boundaries(period_start: date, months: int) -> list[tuple[date, int]]:
    out: list[tuple[date, int]] = []
    y, m = period_start.year, period_start.month
    for _ in range(months):
        start = date(y, m, 1)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        end = date(y, m, 1)
        out.append((start, (end - start).days))
    return out


def stratify_study_months(
    items: Sequence[Any], *, period_start: date, months: int, rng: random.Random, date_attr: str
) -> None:
    """Reassign every item's own date, one item per calendar month in
    round-robin over ``months`` consecutive months starting at
    ``period_start``'s own month, day-of-month redrawn uniformly within it.
    Mutates ``getattr(item, date_attr)`` in place; every other field, and
    ``items``' own order, is untouched.

    The ``policy-change`` scenario's Over-time output (CONTRACT-SEEDS.md
    decision 7) needs a real point in every one of its 24 months; a single
    uniform draw over the whole window, which is what every other scenario
    still uses (:func:`worth_complexity.synthetic.surgical.build_encounters`
    and its siblings' own date draw), leaves real gaps at this small a
    fixture size (~1 case per code per month on average -- a plain Poisson
    draw's chance of a genuinely empty month is not small). Round-robin
    guarantees every month gets at least one item whenever there are at
    least ``months`` of them; call this once per group that needs its own
    guaranteed coverage (one study code, one study program, one service
    line), never across groups pooled together.
    """
    if months <= 0:
        return
    boundaries = _month_boundaries(period_start, months)
    for i, item in enumerate(items):
        start, days = boundaries[i % months]
        setattr(item, date_attr, start + timedelta(days=rng.randrange(max(1, days))))
