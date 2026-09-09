"""Thin shim: the surgical generator now lives in
``worth_complexity.synthetic.surgical``. This script keeps the exact
argv contract ``just build-dataset surgical`` has always used --
``DIR [STUDY_SCALE [COMPARATOR_SCALE]]`` -- so existing invocations,
including the one that built the committed ``mssm-synthetic`` fixture
(study scale 10, comparator scale 1), keep working unchanged.

See :func:`worth_complexity.synthetic.surgical.build_fixture` for the
parameterized function this calls, and :func:`worth_complexity.synthetic.
surgical.build` for the scenario-aware entry point ``worth-cli synth`` uses
instead (CONTRACT-SEEDS.md, "worth track S1").
"""

from __future__ import annotations

import sys
from pathlib import Path

from worth_complexity.synthetic import surgical


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("mssm-synthetic")
    study_scale = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    comparator_scale = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    surgical.build_fixture(root, study_scale, comparator_scale)


if __name__ == "__main__":
    main()
