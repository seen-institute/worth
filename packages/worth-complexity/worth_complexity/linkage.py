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

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from worth_complexity.money import money_context

if TYPE_CHECKING:
    from worth_complexity.models import Encounter, RemitLine


#: CPT codes billed once for several linked encounters rather than once per
#: encounter (decision 7, CONTRACT-SEEDS.md's ``policy-change`` scenario):
#: the antepartum-care global-bundle codes, billed once per pregnancy for
#: every prenatal visit it covers. A claim whose lines carry one of these is
#: treated as a legitimate multi-encounter bundle rather than an ambiguous
#: "duplicate account" (see :func:`link`'s docstring).
BUNDLE_CODES: frozenset[str] = frozenset({"59426", "59425"})


def _is_bundle_claim(lines: tuple[RemitLine, ...]) -> bool:
    return any(line.cpt in BUNDLE_CODES for line in lines)


@dataclass(frozen=True, slots=True)
class LinkedEncounter:
    """An encounter together with every remittance line matched to it."""

    encounter: Encounter
    lines: tuple[RemitLine, ...]
    rule: str
    """Which linkage rule matched, e.g. ``account_id``, or
    ``account_id/bundle:N`` when :attr:`bundle_size` is greater than 1."""
    bundle_size: int = 1
    """How many encounters this claim's lines are shared across (decision 7's
    antepartum-bundle carve-out): ``1`` for the ordinary one-claim-per-
    encounter case. :attr:`realized` divides the primary line's allowed
    amount by this count, so each linked encounter carries its own share of
    one claim rather than the whole claim's value repeated once per
    encounter -- the allocation :func:`link`'s docstring promises is
    recorded in the trace."""

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

        Divided by :attr:`bundle_size` when this claim is a shared bundle:
        the allowed amount on a bundle's own line is the whole pregnancy's
        payment, and each linked visit's realized share of it is that total
        split evenly across every visit the bundle covers.
        """
        with money_context():
            total = sum((line.allowed for line in self.primary_lines), start=Decimal(0))
            if self.bundle_size > 1:
                return (total / Decimal(self.bundle_size)).quantize(Decimal("0.01"))
            return total

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
    reasons: dict[str, int] = field(default_factory=dict)
    """Why each unlinked encounter stayed unlinked, tallied by reason
    (decision 6, CONTRACT-SEEDS.md): ``"no remittance"`` (no line at all
    named that account), ``"reversed without correction"`` (every claim
    occurrence on the account was reversed and nothing replaced it), or
    ``"duplicate account"`` (two encounters in this cohort share one account
    id and the claim on it gives no reason to expect that -- neither is
    linked, both are counted here). Sums to ``len(unlinked)``. An account
    shared by several encounters *because* the claim on it is a legitimate
    bundle (:data:`BUNDLE_CODES`) is not counted here at all -- every
    encounter it covers links, each with its own allocated share of the
    claim (see :attr:`LinkedEncounter.bundle_size`)."""

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
        reasons = ", ".join(f"{reason}: {n}" for reason, n in sorted(self.reasons.items()))
        return (
            f"linkage rate {pct:.2f}%  "
            f"({len(self.linked)} linked, {len(self.unlinked)} unlinked"
            f"{f' [{reasons}]' if reasons else ''}, "
            f"{len(self.orphan_lines)} orphan remittance lines)"
        )


def _net_reversals(
    group: list[RemitLine],
) -> tuple[tuple[RemitLine, ...] | None, bool]:
    """One account's remittance lines, resolved for reversals (decision 6).

    Lines are grouped by ``claim_seq`` -- one group per CLP occurrence --
    and walked in the order they were received (``claim_seq`` order, itself
    file-then-segment order, see ``x12.read_directory``). A reversal
    (``claim_status == "22"``) nets against the most recent not-yet-reversed
    forward occurrence: both are excluded from the result. Whatever forward
    occurrence remains once every reversal has been netted is "the one that
    counts" -- the latest survivor when more than one is still standing.

    Returns ``(lines, reversed_without_correction)``: ``lines`` is ``None``
    when nothing survived netting (every occurrence got reversed and nothing
    replaced it) -- ``reversed_without_correction`` is ``True`` in exactly
    that case, and only when the account carried at least one reversal at
    all (an account with no reversals and no lines simply never happens,
    since ``group`` is only built from lines that already named this
    account).
    """
    by_seq: dict[int, list[RemitLine]] = defaultdict(list)
    seq_order: list[int] = []
    saw_reversal = False
    for line in group:
        if line.claim_seq not in by_seq:
            seq_order.append(line.claim_seq)
        by_seq[line.claim_seq].append(line)
        if line.is_reversal:
            saw_reversal = True

    stack: list[int] = []
    for seq in seq_order:
        if by_seq[seq][0].is_reversal:
            if stack:
                stack.pop()
        else:
            stack.append(seq)

    if stack:
        return tuple(by_seq[stack[-1]]), False
    return None, saw_reversal


def link(encounters: tuple[Encounter, ...], lines: tuple[RemitLine, ...]) -> Linkage:
    """Match encounters to remittance lines on the billing account number.

    One join key, because the synthetic dataset is mostly a happy path. Real
    data needs the documented priority chain, payer claim control number,
    then the submitted patient control number, then encounter id, then a
    (patient, service date, procedure) tuple, with each linked pair recording
    which rule matched and at what confidence.

    Four complications the "dirty" and "policy-change" scenarios plant are
    handled here (decision 6 and decision 7, CONTRACT-SEEDS.md): a reversal
    nets against the earlier remit for the same account rather than
    double-counting or refusing to parse (see :func:`_net_reversals`); two
    encounters that share a billing account id are both left unlinked rather
    than one arbitrarily claiming the money, *unless* the claim on that
    account is a legitimate bundle (:data:`BUNDLE_CODES`), in which case
    every encounter it covers links with its own share of it instead; and
    every reason an encounter stayed unlinked is tallied on
    :attr:`Linkage.reasons`, not just logged and forgotten.
    """
    by_account: dict[str, list[RemitLine]] = defaultdict(list)
    for line in lines:
        by_account[line.account_id].append(line)

    realized_by_account: dict[str, tuple[RemitLine, ...]] = {}
    reversed_without_correction: set[str] = set()
    for account, group in by_account.items():
        resolved, reversed_out = _net_reversals(group)
        if resolved is not None:
            realized_by_account[account] = resolved
        elif reversed_out:
            reversed_without_correction.add(account)

    account_counts = Counter(enc.account_id for enc in encounters)
    duplicate_accounts = {account for account, n in account_counts.items() if n > 1}

    # A shared account is only ambiguous when the claim on it gives no
    # reason to expect several encounters on one account. A claim whose
    # lines carry a global/antepartum bundle code is legitimately one claim
    # for several linked encounters (decision 7): carved out of
    # ``duplicate_accounts`` so every encounter it covers links below,
    # instead of all of them landing in ``reasons["duplicate account"]``.
    bundle_accounts = {
        account
        for account in duplicate_accounts
        if account in realized_by_account and _is_bundle_claim(realized_by_account[account])
    }
    duplicate_accounts -= bundle_accounts

    linked: list[LinkedEncounter] = []
    unlinked: list[Encounter] = []
    reasons: Counter[str] = Counter()
    matched_accounts: set[str] = set()

    for enc in encounters:
        if enc.account_id in duplicate_accounts:
            unlinked.append(enc)
            reasons["duplicate account"] += 1
            continue
        found = realized_by_account.get(enc.account_id)
        if not found:
            unlinked.append(enc)
            if enc.account_id in reversed_without_correction:
                reasons["reversed without correction"] += 1
            else:
                reasons["no remittance"] += 1
            continue
        matched_accounts.add(enc.account_id)
        if enc.account_id in bundle_accounts:
            bundle_size = account_counts[enc.account_id]
            rule = f"account_id/bundle:{bundle_size}"
        else:
            bundle_size = 1
            rule = "account_id"
        linked.append(LinkedEncounter(enc, found, rule, bundle_size=bundle_size))

    orphans = tuple(
        line
        for account, group in by_account.items()
        if account not in matched_accounts
        for line in group
    )
    return Linkage(tuple(linked), tuple(unlinked), orphans, dict(reasons))
