"""The witness bundle: what makes a published run independently checkable.

Every run is a function of three things: the files it read, the rule pack
version that scored them, and the package code that ran. The witness records
a hash of each and a hash over the whole output, so a referee re-running the
same files against the same rule pack and the same package can recompute the
digest by hand and get the identical hex string back — decision 6.

``input_hash`` is a sha256 over the sorted ``(filename, sha256)`` pairs of
every clinical, remittance and claims file a run read: a stand-in for "we ran
on exactly these bytes" that does not require shipping the bytes themselves.

``witness()`` hashes ``{input_hash, rulebook_version, weights_version,
package_versions, outputs}`` as canonical JSON — ``sort_keys=True`` and
compact separators, so key order and whitespace never change the digest.
When the caller passes a key (from the ``WORTH_WITNESS_KEY`` environment
variable — read by the caller, not here, so this module stays free of any
environment dependency) the same canonical bytes are also HMAC-SHA256 signed,
naming the scheme ``hmac-sha256``; with no key the scheme is plain ``sha256``
and there is no signature.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

type JsonValue = str | int | float | bool | dict[str, "JsonValue"] | list["JsonValue"] | None
"""Anything ``json.dumps`` accepts natively — the shape ``to_jsonable``
elsewhere in this package produces."""


def to_jsonable(obj: object) -> JsonValue:
    """A small, stdlib-only twin of ``worth_cli.serialize.to_jsonable``.

    Building an encounter or run witness needs to turn a nest of this
    package's dataclasses (``Decimal``, tuples, ``Mapping``\\ s, other
    dataclasses) into the plain JSON values :func:`witness` hashes. The real
    ``to_jsonable`` already does exactly this, but it lives in ``worth-cli``,
    and this package does not depend on that one, on purpose: a scoring
    engine importing the CLI that wraps it is the dependency running
    backwards. So this is the same conversion, kept in miniature, and kept
    here rather than duplicated at every call site that needs to hash an
    encounter's outputs.
    """
    if isinstance(obj, Enum):
        return to_jsonable(obj.value)
    if obj is None or isinstance(obj, str | int | float | bool):
        return obj
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime | date):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, Mapping):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, frozenset | set):
        items = [to_jsonable(v) for v in obj]
        try:
            return sorted(items, key=str)
        except TypeError:
            return items
    if isinstance(obj, tuple | list):
        return [to_jsonable(v) for v in obj]
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    msg = f"cannot convert {type(obj).__name__} to JSON"
    raise TypeError(msg)


@dataclass(frozen=True, slots=True)
class Witness:
    """One run's checkable bundle."""

    scheme: str
    """``"sha256"`` or ``"hmac-sha256"``."""
    input_hash: str
    rulebook_version: str
    weights_version: str
    package_versions: dict[str, str]
    outputs_hash: str
    """sha256 of the canonical JSON of ``outputs`` alone."""
    digest: str
    """sha256 of the canonical JSON of the whole envelope."""
    signature: str | None
    """HMAC-SHA256 tag over the same envelope bytes, or ``None`` when no key
    was supplied."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def input_hash(files: Iterable[tuple[str, str]]) -> str:
    """sha256 over the sorted ``(filename, sha256)`` pairs of every file read.

    Sorted so that the order files happened to be walked in — an operating
    system and filesystem detail — never changes the hash.
    """
    canonical = sorted(files)
    return hashlib.sha256(_canonical(canonical)).hexdigest()


def witness(
    *,
    input_hash: str,
    rulebook_version: str,
    weights_version: str,
    package_versions: dict[str, str],
    outputs: JsonValue,
    key: bytes | None,
) -> Witness:
    """Build the witness bundle for one run's outputs.

    ``input_hash`` here is the caller-supplied string (typically the result
    of the module-level :func:`input_hash` above), not a recursive call to
    it — the parameter simply shares its name with the function, matching
    the contract.
    """
    outputs_hash = hashlib.sha256(_canonical(outputs)).hexdigest()
    envelope = {
        "input_hash": input_hash,
        "rulebook_version": rulebook_version,
        "weights_version": weights_version,
        "package_versions": dict(package_versions),
        "outputs": outputs,
    }
    canonical = _canonical(envelope)
    digest = hashlib.sha256(canonical).hexdigest()

    if key is not None:
        scheme = "hmac-sha256"
        signature: str | None = hmac.new(key, canonical, hashlib.sha256).hexdigest()
    else:
        scheme = "sha256"
        signature = None

    return Witness(
        scheme=scheme,
        input_hash=input_hash,
        rulebook_version=rulebook_version,
        weights_version=weights_version,
        package_versions=dict(package_versions),
        outputs_hash=outputs_hash,
        digest=digest,
        signature=signature,
    )
