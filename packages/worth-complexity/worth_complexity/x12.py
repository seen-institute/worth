"""A small ANSI X12 835 reader, scoped to the loops WORTH actually needs.

There is no friendlier form of this data. An 835 is how a payer tells a
provider what it paid, it is a federally mandated transaction, and it arrives
as a segment stream. Epic's billing module offers a summarised view of the same
information; Mount Sinai's own requirements brief rejects it in one line, "this is payment reality;
the billing module's summary view is not", because
the summary drops the CAS adjustment segments, and the adjustments are where
denials, contractual write-offs and their reason codes live.

This reads perhaps a dozen segment types rather than the full implementation
guide, and it is written by hand rather than delegated to a general-purpose
parser for one reason: every value has to carry a pointer back to the segment
and element it came from. A referee is entitled to ask where a particular
dollar amount originated and get an answer precise enough to find by eye in the
raw file. A parser that returns nested dictionaries cannot answer that.

Not handled here, deliberately, because the synthetic dataset is a happy path:
reversals paired with their forward claim, replacement claims, split
professional and facility remits for one encounter, secondary-payer
coordination of benefits, capitated zero-dollar remits, and provider-level
adjustments that claw back across claims. Each needs its own modelling
decision, and each is a place the index can be wrong in a way nobody notices
for a year. Reversals are detected and refused rather than silently summed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from worth_complexity.models import RemitLine, SourceRef, WorthComplexityError
from worth_complexity.provenance import sha256_file

SEGMENT_TERMINATOR = "~"
ELEMENT_SEPARATOR = "*"
COMPONENT_SEPARATOR = ":"


class X12Error(WorthComplexityError):
    """The 835 is malformed, or uses a construct this reader does not model."""


@dataclass(frozen=True, slots=True)
class Segment:
    """One X12 segment, with the physical position it was read from."""

    tag: str
    elements: tuple[str, ...]
    index: int
    """1-based ordinal of this segment within the file."""

    def get(self, position: int) -> str:
        """Element at a 1-based position, or empty string if absent."""
        return self.elements[position - 1] if 0 < position <= len(self.elements) else ""

    def component(self, position: int, part: int) -> str:
        return self.get(position).split(COMPONENT_SEPARATOR)[part - 1] if self.get(position) else ""

    def ref(self, filename: str, sha256: str, position: int) -> SourceRef:
        return SourceRef(filename, sha256, self.index, f"{self.tag}{position:02d}")


def segments(text: str) -> tuple[Segment, ...]:
    """Split a raw 835 into segments. Newlines are optional padding, not structure."""
    raw = text.replace("\r", "").replace("\n", "")
    out: list[Segment] = []
    for i, chunk in enumerate(raw.split(SEGMENT_TERMINATOR), start=1):
        if not chunk.strip():
            continue
        parts = chunk.split(ELEMENT_SEPARATOR)
        out.append(Segment(parts[0].strip(), tuple(parts[1:]), i))
    return tuple(out)


def _x12_date(value: str, where: str) -> date:
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as exc:
        msg = f"{where}: not a CCYYMMDD date: {value!r}"
        raise X12Error(msg) from exc


def _money(value: str, where: str) -> Decimal:
    try:
        return Decimal(value or "0")
    except ArithmeticError as exc:
        msg = f"{where}: not an amount: {value!r}"
        raise X12Error(msg) from exc


def read_835(path: Path) -> tuple[RemitLine, ...]:
    """Read every service line of one 835 file."""
    sha = sha256_file(path)
    return parse_835(path.read_text(encoding="utf-8"), path.name, sha)


def parse_835(text: str, filename: str, sha256: str) -> tuple[RemitLine, ...]:
    """Parse an 835 into flat service lines."""
    out: list[RemitLine] = []

    payer_id = ""
    payer_name = ""
    account = ""
    status = ""
    claim_date = date(1900, 1, 1)

    line_open = False
    cpt = ""
    modifiers: tuple[str, ...] = ()
    billed = Decimal(0)
    paid = Decimal(0)
    allowed: Decimal | None = None
    svc_date: date | None = None
    adjustments: list[tuple[str, str, Decimal]] = []
    svc_ref: SourceRef | None = None

    def flush() -> None:
        nonlocal line_open, allowed, svc_date, adjustments, svc_ref
        if not line_open:
            return
        if allowed is None:
            msg = (
                f"{filename}: service line {cpt} on claim {account} carries no AMT*B6 "
                "allowed amount. The adequacy ratio is defined on the allowed amount; "
                "deriving it from billed less adjustments guesses at bundling."
            )
            raise X12Error(msg)
        assert svc_ref is not None
        out.append(
            RemitLine(
                account_id=account,
                payer_id=payer_id,
                payer_name=payer_name,
                claim_status=status,
                cpt=cpt,
                modifiers=modifiers,
                billed=billed,
                allowed=allowed,
                paid=paid,
                service_date=svc_date or claim_date,
                adjustments=tuple(adjustments),
                source_ref=svc_ref,
            )
        )
        line_open = False
        allowed = None
        svc_date = None
        adjustments = []
        svc_ref = None

    for seg in segments(text):
        if seg.tag == "N1" and seg.get(1) == "PR":
            payer_name = seg.get(2)
            payer_id = ""
        elif seg.tag == "REF" and seg.get(1) == "2U" and not payer_id:
            payer_id = seg.get(2)
        elif seg.tag == "CLP":
            flush()
            account = seg.get(1)
            status = seg.get(2)
            if status == "22":
                msg = (
                    f"{filename}: claim {account} is a reversal (CLP02=22). Reversal and "
                    "replacement handling is not modelled; summing it as a forward claim "
                    "would double-count."
                )
                raise X12Error(msg)
        elif seg.tag == "DTM" and seg.get(1) in {"232", "233"} and not line_open:
            claim_date = _x12_date(seg.get(2), f"{filename}:{seg.index}")
        elif seg.tag == "SVC":
            flush()
            line_open = True
            cpt = seg.component(1, 2)
            modifiers = tuple(p for p in seg.get(1).split(COMPONENT_SEPARATOR)[2:] if p)
            billed = _money(seg.get(2), f"{filename}:{seg.index} SVC02")
            paid = _money(seg.get(3), f"{filename}:{seg.index} SVC03")
            svc_ref = seg.ref(filename, sha256, 3)
        elif seg.tag == "AMT" and seg.get(1) == "B6" and line_open:
            allowed = _money(seg.get(2), f"{filename}:{seg.index} AMT02")
        elif seg.tag == "DTM" and seg.get(1) == "472" and line_open:
            svc_date = _x12_date(seg.get(2), f"{filename}:{seg.index}")
        elif seg.tag == "CAS" and line_open:
            group = seg.get(1)
            position = 2
            while seg.get(position):
                adjustments.append(
                    (
                        group,
                        seg.get(position),
                        _money(seg.get(position + 1), f"{filename}:{seg.index} CAS"),
                    )
                )
                position += 3
    flush()

    if not out:
        msg = f"{filename}: no service lines found"
        raise X12Error(msg)
    return tuple(out)


def read_directory(directory: Path) -> tuple[RemitLine, ...]:
    """Read every ``.edi`` file in a directory, in sorted order."""
    lines: list[RemitLine] = []
    for path in sorted(directory.glob("*.edi")):
        lines.extend(read_835(path))
    if not lines:
        msg = f"no .edi remittance files in {directory}"
        raise X12Error(msg)
    return tuple(lines)
