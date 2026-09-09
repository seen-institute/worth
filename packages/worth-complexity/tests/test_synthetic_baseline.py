"""The generators moved into ``worth_complexity.synthetic`` (CONTRACT-SEEDS.md,
"worth track S1"). This asserts the move changed no byte: each class's
``build_fixture`` at the exact scale the committed fixture was built with
must reproduce every file in that fixture, sha256-identical.

Surgical was historically built with the study cohort scaled x10 and the
comparator cohort unscaled (``build_fixture(root, 10, 1)``) -- see
``worth_complexity/synthetic/mixed.py``'s own ``_generate_class`` comment,
which encodes the same fact for the mixed fixture's surgical portion.
Visit, episode and mixed were built at scale 1.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from worth_complexity.synthetic import episode, mixed, surgical, visit

FIXTURES = Path(__file__).parent.parent / "worth_complexity" / "fixtures"


_OWNED = ("clinical", "remittance", "claims")
"""What a generator writes. Each fixture directory also carries a hand-written
``README.md`` no generator produces, so the comparison is scoped to these."""


def _sha256_tree(root: Path) -> dict[str, str]:
    """``{relative posix path: sha256}`` for every file under ``root``'s
    generator-owned subdirectories."""
    out: dict[str, str] = {}
    for owned in _OWNED:
        sub = root / owned
        if not sub.is_dir():
            continue
        for p in sub.rglob("*"):
            if p.is_file():
                rel = str(p.relative_to(root).as_posix())
                out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _assert_byte_identical(built: Path, fixture: Path) -> None:
    got = _sha256_tree(built)
    want = _sha256_tree(fixture)
    missing = sorted(set(want) - set(got))
    extra = sorted(set(got) - set(want))
    assert not missing, f"files the rebuild did not produce: {missing[:10]}"
    assert not extra, f"files the rebuild produced that the fixture does not have: {extra[:10]}"
    mismatched = sorted(rel for rel in want if want[rel] != got[rel])
    assert not mismatched, f"byte-different files: {mismatched[:10]}"


def test_surgical_fixture_is_byte_identical(tmp_path: Path) -> None:
    out = tmp_path / "mssm-synthetic"
    surgical.build_fixture(out, 10, 1)
    _assert_byte_identical(out, FIXTURES / "mssm-synthetic")


def test_visit_fixture_is_byte_identical(tmp_path: Path) -> None:
    out = tmp_path / "visit-synthetic"
    visit.build_fixture(out, 1)
    _assert_byte_identical(out, FIXTURES / "visit-synthetic")


def test_episode_fixture_is_byte_identical(tmp_path: Path) -> None:
    out = tmp_path / "episode-synthetic"
    episode.build_fixture(out, 1)
    _assert_byte_identical(out, FIXTURES / "episode-synthetic")


def test_mixed_fixture_is_byte_identical(tmp_path: Path) -> None:
    """Composes all three generators, so this one costs real seconds."""
    out = tmp_path / "mixed-synthetic"
    mixed.build_fixture(out, 1)
    _assert_byte_identical(out, FIXTURES / "mixed-synthetic")
    readme = FIXTURES / "mixed-synthetic" / "README.md"
    assert (out / "README.md").read_bytes() == readme.read_bytes()
