"""Hashing, so that every value can name the bytes it came from.

worth-fees carries a richer provenance model because its inputs are published
CMS releases with their own derivation chain. A partner extract has no such
chain: the file as received *is* the origin, and the only question a referee
can ask is whether the bytes we scored are the bytes they were given. So this
module is deliberately smaller, a hash of the delivered file, recorded
against every marker read out of it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 1024 * 1024


def sha256_bytes(data: bytes) -> str:
    """Hex sha256 of a byte string."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Hex sha256 of a file, read in chunks so large extracts stay cheap."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
