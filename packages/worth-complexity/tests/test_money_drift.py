"""worth_complexity.money must not drift from worth_fees.money.

The two packages deliberately carry the same twenty lines of decimal-context
discipline rather than sharing a ``worth-core``. The reason is auditability:
each package is published, versioned and read on its own, and an auditor should
not have to install a second thing to satisfy themselves about the arithmetic
in the first.

The cost of that choice is that a fix applied to one copy can silently miss the
other, which for money handling would be a genuine defect. This test is the
guard. If it fails, apply the change to both files, do not delete the test,
and do not merge the modules.

Only the module docstrings are allowed to differ.
"""

from __future__ import annotations

import ast
from pathlib import Path

import worth_complexity.money as complexity_money
import worth_fees.money as fees_money


def _body_without_docstring(path: Path) -> str:
    tree = ast.parse(path.read_text())
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        tree.body = tree.body[1:]
    return ast.dump(tree, indent=2)


def test_module_bodies_are_identical() -> None:
    ours = Path(complexity_money.__file__)
    theirs = Path(fees_money.__file__)
    assert _body_without_docstring(ours) == _body_without_docstring(theirs), (
        "worth_complexity/money.py has drifted from worth_fees/money.py. "
        "Apply the change to both, or explain in both docstrings why they now differ."
    )


def test_the_pinned_context_matches() -> None:
    assert complexity_money.WORTH_CONTEXT.prec == fees_money.WORTH_CONTEXT.prec
    assert complexity_money.WORTH_CONTEXT.rounding == fees_money.WORTH_CONTEXT.rounding
    assert set(complexity_money.WORTH_CONTEXT.traps.items()) == set(
        fees_money.WORTH_CONTEXT.traps.items()
    )
