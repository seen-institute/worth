"""Thin shim: the mixed generator now lives in
``worth_complexity.synthetic.mixed``. Keeps the exact argv contract
``just build-dataset mixed`` has always used -- ``DIR [SCALE]``.

See :func:`worth_complexity.synthetic.mixed.build_fixture` for the
parameterized function this calls, and :func:`worth_complexity.synthetic.
mixed.build` for the scenario-aware entry point ``worth-cli synth`` uses
instead (CONTRACT-SEEDS.md, "worth track S1").
"""

from __future__ import annotations

import sys
from pathlib import Path

from worth_complexity.synthetic import mixed


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("mixed-synthetic")
    scale = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    mixed.build_fixture(root, scale)


if __name__ == "__main__":
    main()
