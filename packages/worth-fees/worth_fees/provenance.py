"""Provenance: what file a number came from, and what that file hashed to.

Every derived value in WORTH carries a chain back to bytes that a hostile
referee can re-download and re-hash themselves. The chain has up to three
links:

    fixture CSV  ->  CMS member file  ->  CMS release archive (.zip)

The fixture we commit is a *derivative*: a subset of rows from the CMS member
file with the AMA-copyrighted descriptor column blanked. It therefore has its
own hash, and points at the member file's hash, which points at the archive's.
Offline runs can still name the upstream hashes because they are recorded in
the fixture manifest at build time.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

_CHUNK = 1024 * 1024


def sha256_bytes(data: bytes) -> str:
    """Hex sha256 of a byte string."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Hex sha256 of a file, read in chunks so large archives stay cheap."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class SourceFile:
    """One link in the provenance chain."""

    filename: str
    sha256: str
    release_date: date
    """The date CMS published the release this file belongs to."""
    role: str
    """What the file supplied, e.g. ``pprrvu``, ``gpci``, ``archive``."""
    derived_from: SourceFile | None = None
    """The upstream file this one was cut from, if any."""
    note: str | None = None

    def chain(self) -> tuple[SourceFile, ...]:
        """This file and every upstream ancestor, nearest first."""
        link: SourceFile | None = self
        out: list[SourceFile] = []
        while link is not None:
            out.append(link)
            link = link.derived_from
        return tuple(out)

    def render(self, indent: str = "") -> list[str]:
        """Human-readable provenance lines, one chain link per stanza."""
        lines: list[str] = []
        for depth, link in enumerate(self.chain()):
            pad = indent + ("    " * depth)
            arrow = "" if depth == 0 else "derived from "
            lines.append(f"{pad}{arrow}{link.filename}  [{link.role}]")
            lines.append(f"{pad}  sha256:   {link.sha256}")
            lines.append(f"{pad}  released: {link.release_date.isoformat()}")
            if link.note:
                lines.append(f"{pad}  note:     {link.note}")
        return lines


@dataclass(frozen=True, slots=True)
class Sources:
    """The full set of files a single derivation depended on.

    A fee derivation reads two files -- the RVU file and the GPCI file -- so a
    single hash would be a lie about where the number came from. Both are
    cut from the same CMS release archive, which is named in ``release``.
    """

    release: str
    """The CMS release label, e.g. ``RVU26A``."""
    release_date: date
    rvu: SourceFile
    gpci: SourceFile

    @property
    def files(self) -> tuple[SourceFile, ...]:
        return (self.rvu, self.gpci)

    def render(self, indent: str = "") -> list[str]:
        lines = [f"{indent}CMS release {self.release}, published {self.release_date.isoformat()}"]
        for f in self.files:
            lines.extend(f.render(indent + "  "))
        return lines
