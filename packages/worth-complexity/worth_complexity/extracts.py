"""Dispatching a partner directory to the reader for its encounter class.

Decision 3 (CONTRACT-PACKS.md): the shape of a clinical extract is dispatched
by which anchor file the directory holds, never by a flag a caller could get
wrong. ``or_log.txt`` is a surgical extract, ``visit.txt`` a visit extract,
``episode.txt`` an episode extract. Exactly one anchor must be present:
:func:`read_extract` refuses a directory that holds none of them (nothing to
read) or more than one (an ambiguous mix nobody actually delivers, and a
silent pick would hide the other file's data from the run entirely).

``ClinicalExtract`` is the interface :mod:`pipeline` reads against so that
``run()`` never needs to know which class it is scoring: a surgical, visit or
episode extract all answer the same six questions. The surgical
implementation, :class:`SurgicalExtract`, is an adapter over today's
``cases.py`` + ``markers.py``, unchanged; the visit and episode
implementations live in ``visits.py`` and ``episodes.py`` and register
themselves in :data:`READERS`, replacing the placeholder entry this module
ships for each.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from worth_complexity import cases, episodes, visits
from worth_complexity import markers as markers_mod
from worth_complexity.models import Encounter, Marker, WorthComplexityError

if TYPE_CHECKING:
    from pathlib import Path

    from worth_complexity.cases import Table
    from worth_complexity.notes import Note
    from worth_complexity.rulepack import RulePack

__all__ = [
    "READERS",
    "ClinicalExtract",
    "CombinedExtract",
    "ExtractError",
    "SurgicalExtract",
    "combine",
    "read_extract",
]


class ExtractError(WorthComplexityError):
    """No extract anchor file was found, more than one was, or the reader
    for a recognised anchor is not built yet."""


@runtime_checkable
class ClinicalExtract(Protocol):
    """What every encounter-class extract must supply to :func:`pipeline.run`.

    Structural, not a base class: :class:`SurgicalExtract` below, and
    ``visits.py``'s and ``episodes.py``'s own implementations, each satisfy
    this independently. A caller holding any of the three never needs to
    know which one it has.
    """

    @property
    def encounter_class(self) -> str:
        """``"surgical"``, ``"visit"`` or ``"episode"``. Must equal the
        ``encounter_class`` of any rule pack scored against this extract, or
        :func:`pipeline.run` refuses the run (decision 3)."""
        ...

    @property
    def notes(self) -> tuple[Note, ...]:
        """Every note delivered with this extract, in delivery order. Empty
        for a class that carries no note dataset (the episode class, per the
        methodology: "from device and alert logs alone")."""
        ...

    @property
    def tables(self) -> tuple[Table, ...]:
        """Every delimited table this extract read, for anything that needs
        to walk them all (today, just :meth:`file_hashes`)."""
        ...

    def notes_for(self, encounter_id: str, note_types: frozenset[str]) -> tuple[Note, ...]:
        """This encounter's notes of the given types, in delivery order."""
        ...

    def file_hashes(self) -> tuple[tuple[str, str], ...]:
        """``(filename, sha256)`` for every table and every note this
        extract read, and nothing else: the 835 and 837 hashes are added by
        the pipeline on top of this, as they are today."""
        ...

    def encounters(self) -> tuple[Encounter, ...]:
        """One :class:`~worth_complexity.models.Encounter` per unit of care
        this extract's anchor table names: one operative log, one visit, one
        30-day episode."""
        ...

    def markers(
        self, encounters: tuple[Encounter, ...], pack: RulePack | None = None
    ) -> dict[str, tuple[Marker, ...]]:
        """Every Layer A marker this extract can produce, keyed by
        ``encounter_id``. Structured markers always; narrative markers too
        when ``pack`` is given and this extract carries notes."""
        ...

    def facts(self, encounter_id: str) -> Mapping[str, Decimal | str | bool]:
        """The flat fact mapping :func:`method1.evaluate_work_rules` reads a
        pack's ``work_rules`` block against, e.g.
        ``total_documented_minutes``, ``coordination_minutes_clinician``.
        Empty for an extract whose class has no threshold work rules (the
        surgical extract, which uses the note-phrase ``procedures`` block
        instead)."""
        ...


@dataclass(frozen=True, slots=True)
class SurgicalExtract:
    """Adapts today's ``cases.py`` + ``markers.py`` to :class:`ClinicalExtract`.

    Every method below delegates straight to the existing, unchanged
    surgical reader: this class adds no behaviour of its own, only the
    uniform shape :mod:`pipeline` now reads every extract class through.
    """

    raw: cases.ClinicalExtract
    """The extract exactly as ``cases.read_extract`` returns it. Kept public
    so a caller who still wants the concrete surgical tables (``or_log``,
    ``or_log_proc``, ...) can reach them without an isinstance check."""

    @property
    def encounter_class(self) -> str:
        return "surgical"

    @property
    def notes(self) -> tuple[Note, ...]:
        return self.raw.notes

    @property
    def tables(self) -> tuple[Table, ...]:
        return self.raw.tables

    def notes_for(self, encounter_id: str, note_types: frozenset[str]) -> tuple[Note, ...]:
        return self.raw.notes_for(encounter_id, note_types)

    def file_hashes(self) -> tuple[tuple[str, str], ...]:
        pairs = {(t.name, t.sha256) for t in self.raw.tables}
        pairs |= {(n.filename, n.sha256) for n in self.raw.notes}
        return tuple(sorted(pairs))

    def encounters(self) -> tuple[Encounter, ...]:
        return cases.encounters(self.raw)

    def markers(
        self, encounters: tuple[Encounter, ...], pack: RulePack | None = None
    ) -> dict[str, tuple[Marker, ...]]:
        return markers_mod.extract(self.raw, encounters, pack)

    def facts(self, encounter_id: str) -> Mapping[str, Decimal | str | bool]:  # noqa: ARG002
        """The surgical pack has no ``work_rules`` block; Method 1 for this
        class is the note-phrase ``procedures`` cross-check instead."""
        return {}


@dataclass(frozen=True, slots=True)
class CombinedExtract:
    """A directory whose clinical files name more than one anchor table.

    A real partner delivery is one extract with OR cases, clinic visits and
    monitoring episodes side by side, sharing one 835/837 feed. Decision 1
    (multi-class MC track): ``read_extract`` no longer refuses a directory
    with more than one anchor; it reads one class extract per anchor found
    and wraps them here. ``encounters()`` concatenates every class's own, in
    :data:`~worth_complexity.pipeline.CLASS_ORDER` order via ``by_class``'s
    own key order (sorted, deterministic); ``notes``/``tables``/
    ``file_hashes`` are the union; ``notes_for``/``facts``/``markers``
    dispatch on the encounter's own class, resolved once at construction
    time by :func:`combine` rather than by re-reading every sub-extract's
    encounters on every call.

    A single-anchor directory never reaches this class: :func:`read_extract`
    returns that one class's own extract unchanged, so every existing
    single-class caller sees no difference at all.
    """

    by_class: dict[str, ClinicalExtract]
    """One extract per encounter class this directory's anchors named,
    keyed by ``encounter_class``. Public so a caller who wants one class's
    own concrete extract (the surgical tables, say) can reach it directly
    without an isinstance check on the union."""
    class_of: dict[str, str] = field(repr=False)
    """``encounter_id -> encounter_class``, built once by :func:`combine`."""

    @property
    def encounter_class(self) -> str:
        return "mixed"

    @property
    def classes(self) -> tuple[str, ...]:
        """Every encounter class present, sorted."""
        return tuple(sorted(self.by_class))

    @property
    def notes(self) -> tuple[Note, ...]:
        """Every note across every class, deduplicated by ``(filename,
        sha256)``. A shared clinical directory's ``notes.txt``/``notes/`` is
        one physical dataset even when more than one class's own reader
        happens to read it (the episode reader, say, reads whatever
        ``notes.txt`` a directory carries — decision: "notes/ may be
        absent" implies "read it when it is not"), so a straight
        concatenation across ``by_class`` would double-count exactly the
        notes two classes share, not add up to a real union."""
        seen: set[tuple[str, str]] = set()
        out: list[Note] = []
        for cls in self.classes:
            for n in self.by_class[cls].notes:
                key = (n.filename, n.sha256)
                if key in seen:
                    continue
                seen.add(key)
                out.append(n)
        return tuple(out)

    @property
    def tables(self) -> tuple[Table, ...]:
        """Every table across every class, deduplicated by ``(name,
        sha256)`` for the same reason :attr:`notes` is: a shared lookup
        table (``patient_lds.txt``, say) is read once by every class whose
        own reader names it, all from the identical bytes on disk."""
        seen: set[tuple[str, str]] = set()
        out: list[Table] = []
        for cls in self.classes:
            for t in self.by_class[cls].tables:
                key = (t.name, t.sha256)
                if key in seen:
                    continue
                seen.add(key)
                out.append(t)
        return tuple(out)

    def notes_for(self, encounter_id: str, note_types: frozenset[str]) -> tuple[Note, ...]:
        cls = self.class_of.get(encounter_id)
        if cls is None:
            return ()
        return self.by_class[cls].notes_for(encounter_id, note_types)

    def file_hashes(self) -> tuple[tuple[str, str], ...]:
        pairs: set[tuple[str, str]] = set()
        for cls in self.classes:
            pairs |= set(self.by_class[cls].file_hashes())
        return tuple(sorted(pairs))

    def encounters(self) -> tuple[Encounter, ...]:
        return tuple(e for cls in self.classes for e in self.by_class[cls].encounters())

    def markers(
        self, encounters: tuple[Encounter, ...], pack: RulePack | None = None
    ) -> dict[str, tuple[Marker, ...]]:
        """Group ``encounters`` by their own class and ask that class's
        sub-extract for their markers. ``pack`` is handed to a class's
        sub-extract only when it actually scores that class
        (``pack.encounter_class == cls``); a class scored by a different
        pack gets structured markers only (``pack=None``), the same
        fallback a caller who never heard of rule packs gets from any
        single-class extract."""
        by_class_encs: dict[str, list[Encounter]] = {}
        for e in encounters:
            by_class_encs.setdefault(e.encounter_class, []).append(e)
        out: dict[str, tuple[Marker, ...]] = {}
        for cls, encs in by_class_encs.items():
            sub = self.by_class.get(cls)
            if sub is None:
                continue
            sub_pack = pack if pack is not None and pack.encounter_class == cls else None
            out.update(sub.markers(tuple(encs), sub_pack))
        return out

    def facts(self, encounter_id: str) -> Mapping[str, Decimal | str | bool]:
        cls = self.class_of.get(encounter_id)
        if cls is None:
            return {}
        return self.by_class[cls].facts(encounter_id)


def combine(by_class: Mapping[str, ClinicalExtract]) -> CombinedExtract:
    """Wrap one extract per class into a :class:`CombinedExtract`.

    Reads every sub-extract's ``encounters()`` exactly once to build the
    ``encounter_id -> encounter_class`` index every dispatching method
    needs, and refuses two classes that somehow named the same encounter id
    — the id spaces are meant to be class-namespaced by convention (an OR
    log id, a visit id, an episode id never collide), and a collision here
    would silently misroute that encounter's notes and facts to the wrong
    class's reader.
    """
    index: dict[str, str] = {}
    for cls, sub in by_class.items():
        for enc in sub.encounters():
            prior = index.get(enc.encounter_id)
            if prior is not None and prior != cls:
                msg = (
                    f"encounter id {enc.encounter_id!r} appears in both the {prior!r} and "
                    f"{cls!r} class extracts"
                )
                raise ExtractError(msg)
            index[enc.encounter_id] = cls
    return CombinedExtract(by_class=dict(by_class), class_of=index)


def _read_surgical(directory: Path) -> ClinicalExtract:
    return SurgicalExtract(cases.read_extract(directory))


def _read_visit_not_built(directory: Path) -> ClinicalExtract:  # noqa: ARG001
    msg = "visit extracts are not built yet"
    raise ExtractError(msg)


def _read_episode_not_built(directory: Path) -> ClinicalExtract:  # noqa: ARG001
    msg = "episode extracts are not built yet"
    raise ExtractError(msg)


READERS: dict[str, Callable[[Path], ClinicalExtract]] = {
    "or_log.txt": _read_surgical,
    "visit.txt": visits.read_visit_extract,
    "episode.txt": episodes.read_episode_extract,
}
"""Anchor filename to reader, decision 3. ``visits.py`` and ``episodes.py``
each replace their own entry with their real reader; this module ships the
surgical entry plus a placeholder for the other two so ``read_extract``
already dispatches correctly the moment either lands."""


def read_extract(directory: Path) -> ClinicalExtract:
    """Read a clinical extract, choosing the reader(s) by which anchor
    file(s) the directory holds.

    At least one of :data:`READERS`' anchor files must be present; none is
    an empty or unrecognised directory. Exactly one anchor returns that
    class's own extract, unchanged (every existing single-class caller sees
    no difference). More than one anchor — a real partner delivery mixing
    OR cases, clinic visits and monitoring episodes in one directory — reads
    each class's reader over the same directory and wraps the result in a
    :class:`CombinedExtract` (decision 1, MC track), rather than refusing an
    extract shape partners actually deliver.
    """
    present = sorted(anchor for anchor in READERS if (directory / anchor).is_file())
    if not present:
        msg = f"no recognised extract anchor file in {directory}: expected one of {sorted(READERS)}"
        raise ExtractError(msg)
    if len(present) == 1:
        return READERS[present[0]](directory)
    by_class: dict[str, ClinicalExtract] = {}
    for anchor in present:
        sub = READERS[anchor](directory)
        by_class[sub.encounter_class] = sub
    return combine(by_class)
