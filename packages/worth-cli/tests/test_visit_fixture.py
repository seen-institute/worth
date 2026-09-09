"""``worth-cli run`` against the visit-synthetic fixture, with no ``--pack``
and no ``--setting`` (CONTRACT-PACKS.md, "Visit class (agent V)"): the class
default pack (``visit-em-v1``) and the class default setting
(non-facility) must both resolve on their own from the extract's own
``visit.txt`` anchor, the same way the surgical fixture resolves to
``surgical-v1``/facility with nothing passed.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from worth_cli.cli import main
from worth_complexity.cli import FIXTURE

if TYPE_CHECKING:
    import pytest

VISIT_CLINICAL = FIXTURE.parent / "visit-synthetic" / "clinical"
VISIT_REMITTANCE = FIXTURE.parent / "visit-synthetic" / "remittance"


def test_run_with_no_pack_or_setting_flags(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        main(
            [
                "run",
                "--clinical",
                str(VISIT_CLINICAL),
                "--remittance",
                str(VISIT_REMITTANCE),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "visit-em" in out
    assert "99214" in out


def test_run_json_carries_the_resolved_pack_and_setting(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "run",
                "--clinical",
                str(VISIT_CLINICAL),
                "--remittance",
                str(VISIT_REMITTANCE),
                "--json",
            ]
        )
        == 0
    )
    doc = json.loads(capsys.readouterr().out)
    assert doc["run"]["pack"]["rule_pack_id"] == "visit-em"
    assert doc["run"]["pack"]["encounter_class"] == "visit"
    assert doc["run"]["setting"] == "non-facility"
    assert doc["run"]["records"]
