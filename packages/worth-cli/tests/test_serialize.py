"""Unit tests for worth_cli.serialize.to_jsonable."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum, StrEnum
from pathlib import Path

import pytest
from worth_cli.serialize import to_jsonable


def test_decimal_becomes_a_string_never_a_float() -> None:
    assert to_jsonable(Decimal("12.50")) == "12.50"
    assert isinstance(to_jsonable(Decimal("0.10")), str)


def test_date_and_datetime_become_isoformat() -> None:
    assert to_jsonable(date(2026, 3, 14)) == "2026-03-14"
    assert to_jsonable(datetime(2026, 3, 14, 9, 30)) == "2026-03-14T09:30:00"


class Color(Enum):
    RED = "red"
    BLUE = "blue"


class Setting(StrEnum):
    FACILITY = "facility"
    NON_FACILITY = "non-facility"


def test_plain_enum_becomes_its_value() -> None:
    assert to_jsonable(Color.RED) == "red"


def test_str_enum_becomes_a_plain_str_not_the_enum_member() -> None:
    result = to_jsonable(Setting.FACILITY)
    assert result == "facility"
    assert type(result) is str  # not a StrEnum instance


def test_path_becomes_a_string() -> None:
    assert to_jsonable(Path("/a/b.txt")) == "/a/b.txt"


def test_regex_pattern_becomes_its_source_string() -> None:
    assert to_jsonable(re.compile(r"foo\d+")) == r"foo\d+"


def test_mapping_gets_string_keys() -> None:
    assert to_jsonable({(2026, 1): "x"}) == {"(2026, 1)": "x"}
    assert to_jsonable({"a": 1}) == {"a": 1}


def test_tuple_and_list_preserve_order() -> None:
    assert to_jsonable(("b", "a", "c")) == ["b", "a", "c"]
    assert to_jsonable(["b", "a", "c"]) == ["b", "a", "c"]


def test_frozenset_and_set_are_sorted_when_possible() -> None:
    assert to_jsonable(frozenset({"c", "a", "b"})) == ["a", "b", "c"]
    assert to_jsonable({3, 1, 2}) == [1, 2, 3]


@dataclass(frozen=True, slots=True)
class Inner:
    amount: Decimal
    when: date


@dataclass(frozen=True, slots=True)
class Outer:
    name: str
    inner: Inner
    children: tuple[Inner, ...]


def test_nested_dataclass_is_walked_field_by_field() -> None:
    outer = Outer(
        name="case",
        inner=Inner(amount=Decimal("1.00"), when=date(2026, 1, 1)),
        children=(Inner(amount=Decimal("2.00"), when=date(2026, 1, 2)),),
    )
    assert to_jsonable(outer) == {
        "name": "case",
        "inner": {"amount": "1.00", "when": "2026-01-01"},
        "children": [{"amount": "2.00", "when": "2026-01-02"}],
    }


def test_the_whole_thing_is_json_dumpable() -> None:
    outer = Outer(
        name="case", inner=Inner(amount=Decimal("1.00"), when=date(2026, 1, 1)), children=()
    )
    json.dumps(to_jsonable(outer))  # must not raise


@dataclass(frozen=True, slots=True)
class HasAnUnsupportedField:
    label: str
    payload: object


def test_an_unsupported_type_raises_type_error_naming_the_attribute_path() -> None:
    bad = HasAnUnsupportedField(label="x", payload=object())
    with pytest.raises(TypeError, match=r"\$\.payload"):
        to_jsonable(bad)


def test_an_unsupported_type_nested_inside_a_dataclass_names_the_full_path() -> None:
    @dataclass(frozen=True, slots=True)
    class Wrapper:
        inner: HasAnUnsupportedField

    with pytest.raises(TypeError, match=r"\$\.inner\.payload"):
        to_jsonable(Wrapper(inner=HasAnUnsupportedField(label="x", payload=object())))


def test_a_bare_unsupported_object_at_the_top_raises_too() -> None:
    with pytest.raises(TypeError, match=r"\$: cannot convert object"):
        to_jsonable(object())
