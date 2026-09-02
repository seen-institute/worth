"""Joining an operative case to the money that was paid for it.

This is the most under-specified step in the whole methodology and the most
likely place the instrument quietly breaks. "Joined on claim number or
encounter ID" is one sentence in the partner brief and it is doing enormous
work.

The failure that matters is not that some encounters fail to link. It is that
the failures are *not random*: complex, multi-payer, multi-claim cases fail
most, and those are precisely the encounters that carry the finding. A cohort
that silently drops them is biased toward adequacy. It will understate the gap
and nobody will be able to tell from the published number.

So the linkage rate is returned as a first-class part of the result, not logged
and forgotten, and it is meant to be published alongside every index value.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from worth_complexity.money import money_context

if TYPE_CHECKING:
    from worth_complexity.models import Encounter, RemitLine


@dataclass(frozen=True, slots=True)
class LinkedEncounter:
    """An encounter together with every remittance line matched to it."""

    encounter: Encounter
    lines: tuple[RemitLine, ...]
    rule: str
    """Which linkage rule matched, e.g. ``account_id``."""

    @property
    def payer_id(self) -> str:
        return self.lines[0].payer_id

    @property
    def payer_name(self) -> str:
        return self.lines[0].payer_name

    @property
    def primary_lines(self) -> tuple[RemitLine, ...]:
        """The remittance lines billed under the primary procedure code."""
        return tuple(line for line in self.lines if line.cpt == self.encounter.primary_cpt)

    @property
    def realized(self) -> Decimal:
        """Allowed amount on the primary procedure's line, the numerator.

        The primary line and not the whole claim, because the expected payment
        prices the primary code; a secondary procedure on the claim would
        otherwise inflate the ratio. The allowed amount, not the paid amount:
        patient responsibility is a function of benefit design rather than of
        what the work was worth, and including it would make the index a
        measure of deductible season. See :attr:`realized_claim`.
        """
        with money_context():
            return sum((line.allowed for line in self.primary_lines), start=Decimal(0))

    @property
    def realized_claim(self) -> Decimal:
        """Allowed amount across every line on the claim. Reported beside
        :attr:`realized` so nobody has to ask where the rest of the claim went."""
        with money_context():
            return sum((line.allowed for line in self.lines), start=Decimal(0))

    @property
    def denied_lines(self) -> tuple[RemitLine, ...]:
        """Lines that were submitted and allowed nothing.

        Work that was documented, coded, submitted and refused is a fourth gap
        alongside the three the methodology names, and it is measurable at no
        marginal cost because the CAS segments are already parsed.
        """
        return tuple(line for line in self.lines if line.allowed == 0)


@dataclass(frozen=True, slots=True)
class Linkage:
    """The result of matching a cohort of encounters to a set of remittances."""

    linked: tuple[LinkedEncounter, ...]
    unlinked: tuple[Encounter, ...]
    orphan_lines: tuple[RemitLine, ...]
    """Remittance lines whose account number matched no encounter in the extract."""

    @property
    def rate(self) -> Decimal:
        """Share of encounters that matched a remittance, to four places."""
        total = len(self.linked) + len(self.unlinked)
        if total == 0:
            return Decimal(0)
        with money_context():
            return (Decimal(len(self.linked)) / Decimal(total)).quantize(Decimal("0.0001"))

    def render(self) -> str:
        pct = self.rate * 100
        return (
            f"linkage rate {pct:.2f}%  "
            f"({len(self.linked)} linked, {len(self.unlinked)} unlinked, "
            f"{len(self.orphan_lines)} orphan remittance lines)"
        )


def link(encounters: tuple[Encounter, ...], lines: tuple[RemitLine, ...]) -> Linkage:
    """Match encounters to remittance lines on the billing account number.

    One rule, because the synthetic dataset is a happy path. Real data needs the
    documented priority chain, payer claim control number, then the submitted
    patient control number, then encounter id, then a
    (patient, service date, procedure) tuple, with each linked pair recording
    which rule matched and at what confidence.
    """
    by_account: dict[str, list[RemitLine]] = defaultdict(list)
    for line in lines:
        by_account[line.account_id].append(line)

    linked: list[LinkedEncounter] = []
    unlinked: list[Encounter] = []
    matched_accounts: set[str] = set()
    for enc in encounters:
        found = by_account.get(enc.account_id)
        if not found:
            unlinked.append(enc)
            continue
        matched_accounts.add(enc.account_id)
        linked.append(LinkedEncounter(enc, tuple(found), "account_id"))

    orphans = tuple(
        line
        for account, group in by_account.items()
        if account not in matched_accounts
        for line in group
    )
    return Linkage(tuple(linked), tuple(unlinked), orphans)
