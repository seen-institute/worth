"""Argument handling for worth-db's CLI. No database needed."""

from __future__ import annotations

import argparse

import pytest
from worth_db.cli import _resolve_url, _vintage_keys, build_parser, database_label
from worth_fees.sources import PINNED_VINTAGES


def test_database_label_strips_credentials() -> None:
    label = database_label("postgresql://worth:s3cret@db.internal:5432/worth")
    assert label == "db.internal:5432/worth"
    assert "worth:s3cret" not in label
    assert "s3cret" not in label


def test_migrate_parses_and_dispatches() -> None:
    args = build_parser().parse_args(["migrate"])
    assert args.command == "migrate"
    assert getattr(args, "url", None) is None  # not provided: attribute is suppressed
    assert args.func.__name__ == "_migrate"


def test_load_parses_vintage_and_full() -> None:
    args = build_parser().parse_args(["load", "--vintage", "2026Q1", "--full"])
    assert args.command == "load"
    assert args.vintage == "2026Q1"
    assert args.full is True
    assert args.func.__name__ == "_load"


def test_load_defaults_to_no_vintage_and_no_full() -> None:
    args = build_parser().parse_args(["load"])
    assert args.vintage is None
    assert args.full is False


def test_status_parses() -> None:
    args = build_parser().parse_args(["status"])
    assert args.func.__name__ == "_status"


def test_url_flag_works_before_and_after_the_subcommand() -> None:
    before = build_parser().parse_args(["--url", "postgresql://h/db", "migrate"])
    after = build_parser().parse_args(["migrate", "--url", "postgresql://h/db"])
    assert before.url == after.url == "postgresql://h/db"


def test_a_missing_command_is_an_error() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_resolve_url_refuses_when_neither_flag_nor_env_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    args = argparse.Namespace(url=None)
    with pytest.raises(SystemExit):
        _resolve_url(args)


def test_resolve_url_prefers_the_flag_over_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://env/db")
    args = argparse.Namespace(url="postgresql://flag/db")
    assert _resolve_url(args) == "postgresql://flag/db"


def test_resolve_url_falls_back_to_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://env/db")
    args = argparse.Namespace(url=None)
    assert _resolve_url(args) == "postgresql://env/db"


def test_vintage_keys_defaults_to_every_pinned_vintage() -> None:
    assert _vintage_keys(None) == sorted(PINNED_VINTAGES)


def test_vintage_keys_resolves_one_pinned_vintage() -> None:
    (year, quarter) = sorted(PINNED_VINTAGES)[0]
    assert _vintage_keys(f"{year}Q{quarter}") == [(year, quarter)]


def test_vintage_keys_refuses_an_unpinned_vintage() -> None:
    with pytest.raises(SystemExit):
        _vintage_keys("1999Q1")
