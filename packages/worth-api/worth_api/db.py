"""Postgres: the schema, the load, and what is already there.

The database holds public CMS fee-schedule data and nothing else. It is a
rebuildable cache of the pinned releases, in the schema ``worth-fees`` ships,
loaded from the script ``worth-fees`` generates. This module runs those two
artefacts; it does not restate what they contain.

The load script is psql-shaped: plain statements plus ``COPY ... FROM STDIN``
blocks terminated by ``\\.``. It is fed through the driver here rather than
through a ``psql`` binary so the same code path works on a laptop against the
compose database, in the image, and in CI. The text executed is the script,
verbatim; only the transport differs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psycopg
import worth_fees
from worth_fees.sql import export

if TYPE_CHECKING:
    from worth_fees.sources import FeeSchedule

MIGRATION = Path(worth_fees.__file__).resolve().parents[1] / "migrations" / "0001_fee_schedule.sql"

_COPY_HEADER = re.compile(r"^COPY \S+ \(.*\) FROM STDIN;$")
_COPY_END = "\\."

type Connection = psycopg.Connection[Any]


def connect(url: str) -> Connection:
    """Autocommit, so the scripts' own BEGIN and COMMIT are the transactions."""
    return psycopg.connect(url, autocommit=True)


def ensure_schema(conn: Connection) -> bool:
    """Apply ``0001_fee_schedule.sql`` if the release table is missing. True if applied.

    Local compose applies it through the initdb hook on an empty volume; a
    managed database in production has no such hook, so the server does it.
    """
    row = conn.execute("SELECT to_regclass('fee_schedule_release')").fetchone()
    if row is not None and row[0] is not None:
        return False
    conn.execute(MIGRATION.read_text())
    return True


def run_script(conn: Connection, script: str) -> None:
    """Execute a worth-fees load script: statements as written, COPY blocks streamed."""
    lines = script.split("\n")
    pending: list[str] = []

    def flush() -> None:
        sql = "\n".join(pending).strip()
        pending.clear()
        if sql:
            conn.execute(sql)  # multi-statement text, no parameters

    i = 0
    while i < len(lines):
        line = lines[i]
        if _COPY_HEADER.match(line):
            flush()
            i += 1
            with conn.cursor().copy(line[:-1]) as copy:
                while lines[i] != _COPY_END:
                    copy.write(lines[i] + "\n")
                    i += 1
            i += 1
            continue
        pending.append(line)
        i += 1
    flush()


@dataclass(frozen=True, slots=True)
class Release:
    """One release as the database holds it."""

    release_id: str
    rule_year: int
    quarter: int
    coverage: str
    """``archive`` when loaded from the full CMS file, ``fixture`` from the committed sample."""
    rvu_rows: int
    localities: int


def releases(conn: Connection) -> dict[tuple[int, int], Release]:
    rows = conn.execute(
        """
        SELECT r.release_id, r.rule_year, r.quarter,
               EXISTS (SELECT 1 FROM source_file s
                       WHERE s.release_id = r.release_id AND s.role LIKE '%%_fixture'),
               (SELECT count(*) FROM rvu  WHERE release_id = r.release_id),
               (SELECT count(*) FROM gpci WHERE release_id = r.release_id)
        FROM fee_schedule_release r
        """
    ).fetchall()
    return {
        (year, quarter): Release(
            release_id=release_id,
            rule_year=year,
            quarter=quarter,
            coverage="fixture" if from_fixture else "archive",
            rvu_rows=rvu_rows,
            localities=localities,
        )
        for release_id, year, quarter, from_fixture, rvu_rows, localities in rows
    }


def coverage_of(schedule: FeeSchedule) -> str:
    return "fixture" if "(stripped fixture)" in schedule.sources.rvu.role else "archive"


def ensure_loaded(conn: Connection, schedule: FeeSchedule) -> str:
    """Load ``schedule`` unless the database already holds it as well or better.

    Returns ``present``, ``loaded`` or ``upgraded``. An upgrade is the one case
    where a release is replaced: the database was loaded from the eight-code
    fixture and the full archive is now available. The script deletes and
    reloads the release in one transaction, so a reader never sees half.
    """
    key = (schedule.vintage.rule_year, schedule.vintage.quarter)
    have = releases(conn).get(key)
    want = coverage_of(schedule)
    if have is not None and not (have.coverage == "fixture" and want == "archive"):
        return "present"
    run_script(conn, export(schedule))
    return "loaded" if have is None else "upgraded"
