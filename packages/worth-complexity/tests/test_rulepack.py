"""The rule pack is data, and its invariants are enforced at load."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

import pytest
from worth_complexity import RulePackError
from worth_complexity.rulepack import _PACK_DIR, load, loads

BASE = {
    "rule_pack_id": "t",
    "version": "0",
    "markers": [
        {
            "marker_id": "a",
            "provenance": "structured",
            "weight": "0.5",
            "anchor_low": "0",
            "anchor_high": "10",
        },
        {
            "marker_id": "b",
            "provenance": "structured",
            "weight": "0.5",
            "anchor_low": "0",
            "anchor_high": "10",
        },
    ],
}


def pack(**overrides: object) -> bytes:
    doc = json.loads(json.dumps(BASE))
    doc.update(overrides)
    return json.dumps(doc).encode()


def test_the_shipped_pack_loads_and_its_weights_sum_to_one() -> None:
    p = load("gyn-surgical-v1")
    assert sum((m.weight for m in p.markers), start=Decimal(0)) == Decimal(1)


def test_the_shipped_pack_is_marked_provisional() -> None:
    """Until weights come from clinical and statistical review, nothing is publishable."""
    assert load("gyn-surgical-v1").is_provisional


def test_the_digest_is_of_the_bytes_as_shipped() -> None:
    """A published index value names the exact rules that produced it, forever."""
    shipped = (_PACK_DIR / "gyn-surgical-v1.json").read_bytes()
    assert load("gyn-surgical-v1").digest == hashlib.sha256(shipped).hexdigest()


def test_weights_that_do_not_sum_to_one_are_refused() -> None:
    """A pack summing to 0.99 produces scores quietly 1% low, comparable with nothing."""
    doc = json.loads(json.dumps(BASE))
    doc["markers"][0]["weight"] = "0.49"
    with pytest.raises(RulePackError, match="sum to exactly 1"):
        loads(json.dumps(doc).encode())


def test_inverted_anchors_are_refused() -> None:
    doc = json.loads(json.dumps(BASE))
    doc["markers"][0]["anchor_high"] = "-1"
    with pytest.raises(RulePackError, match="anchor_high"):
        loads(json.dumps(doc).encode())


def test_a_missing_pack_is_named() -> None:
    with pytest.raises(RulePackError, match="no such rule pack"):
        load("does-not-exist")


@pytest.mark.parametrize(
    ("value", "expected"),
    [("-5", "0"), ("0", "0"), ("5", "0.5"), ("10", "1"), ("500", "1")],
)
def test_normalisation_clamps_outside_the_anchors(value: str, expected: str) -> None:
    """An eleven-hour case is not twice as complex as a five-hour one on an
    ordinal instrument, and one outlier must not run the scale past 100."""
    rule = loads(pack()).markers[0]
    assert rule.normalise(Decimal(value)) == Decimal(expected)
