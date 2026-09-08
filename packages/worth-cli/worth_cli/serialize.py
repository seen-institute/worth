"""Convert dataclasses and their usual companions into plain, JSON-safe Python.

Standard library only. Money is never emitted as a float: a :class:`Decimal`
crosses as a string, the same rule ``worth-fees`` and ``worth-complexity``
apply everywhere else. An object this cannot place is a bug to find, not a
value to paper over with ``str()``, so :func:`to_jsonable` raises
``TypeError`` naming exactly which attribute path could not be converted.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

JsonValue = Any
"""The recursive JSON-value type. ``Any`` because Python's type system has no
better name for "str | int | float | bool | None | list[JsonValue] |
dict[str, JsonValue]" short of a recursive alias mypy handles inconsistently."""


def to_jsonable(obj: object, *, _path: str = "$") -> JsonValue:
    """Recursively convert ``obj`` into something ``json.dumps`` can write.

    Handled, in order:

    * ``Enum`` (including ``StrEnum`` and ``IntEnum``): its ``.value``,
      itself converted. Checked first because a ``StrEnum`` member is also a
      ``str``, and would otherwise fall through to the next rule unconverted.
    * ``None``, ``bool``, ``int``, ``float``, ``str``: returned as is.
    * ``Decimal``: as a string. Never a float; a float cannot represent every
      decimal this project computes, so no derived amount ever round-trips
      through one on the way to JSON.
    * ``date`` / ``datetime``: ``.isoformat()``.
    * ``re.Pattern``: its source pattern string. The one extension beyond the
      literal type list this project's dataclasses are built from, needed
      because a rule pack's note-matching patterns are compiled at load time
      and are otherwise unrepresentable.
    * ``Path``: ``str(path)``.
    * ``Mapping``: a ``dict``, with every key coerced to ``str``.
    * ``frozenset`` / ``set``: a list, sorted when its converted elements
      support ordering (so output is deterministic across runs even though
      set iteration order is not), otherwise left in whatever order
      iteration produced.
    * ``tuple`` / ``list``: converted element-wise, order preserved. A tuple
      is ordered data here (an arithmetic derivation trace, a procedure
      panel), never treated as a set.
    * a dataclass instance (not a dataclass *type*): a ``dict`` of its fields,
      by name, each converted in turn.

    Anything else raises ``TypeError`` naming ``_path``, the dotted and
    indexed attribute path that reached it, so a genuinely new field type
    fails loudly during development instead of silently becoming a string.
    """
    # Enum before the primitive check: StrEnum and IntEnum are also `str`/`int`
    # instances, and would otherwise be returned as themselves rather than as
    # the plain builtin `.value` this function promises.
    if isinstance(obj, Enum):
        return to_jsonable(obj.value, _path=_path)
    if obj is None or isinstance(obj, str | int | float | bool):
        return obj
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime | date):
        return obj.isoformat()
    if isinstance(obj, re.Pattern):
        return obj.pattern
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, Mapping):
        return {
            key if isinstance(key, str) else str(key): to_jsonable(value, _path=f"{_path}[{key!r}]")
            for key, value in obj.items()
        }
    if isinstance(obj, frozenset | set):
        items = [to_jsonable(v, _path=f"{_path}[*]") for v in obj]
        return _sorted_if_possible(items)
    if isinstance(obj, tuple | list):
        return [to_jsonable(v, _path=f"{_path}[{i}]") for i, v in enumerate(obj)]
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {
            f.name: to_jsonable(getattr(obj, f.name), _path=f"{_path}.{f.name}")
            for f in dataclasses.fields(obj)
        }
    raise TypeError(f"{_path}: cannot convert {type(obj).__name__} to JSON")


def _sorted_if_possible(items: list[JsonValue]) -> list[JsonValue]:
    try:
        return sorted(items)
    except TypeError:
        return items
