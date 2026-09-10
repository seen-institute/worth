"""Synthetic partner-dataset generators and the scenario suite built on them.

``surgical.py``, ``visit.py``, ``episode.py`` and ``mixed.py`` are the four
class generators (moved from ``tools/make_*_dataset.py``; those scripts are
now thin shims onto ``build_fixture``/``build`` here). ``edi.py`` holds the
835/837 writers shared by the surgical and visit generators. ``scenarios.py``
defines the scenario catalog (CONTRACT-SEEDS.md); ``dirt.py`` applies named
corruptions to a generated directory; ``truth.py`` writes and reads each
generated directory's ``truth.json``.
"""

from __future__ import annotations
