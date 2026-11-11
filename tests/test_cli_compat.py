"""Tests for the temporary CLI compatibility layer."""

from __future__ import annotations

import argparse

import pytest

from packrat import cli_compat


@pytest.mark.parametrize(
    "old,new",
    [
        ("retry", ["download-manifest", "--failed-only"]),
        ("discover-phase4", ["discover", "taste"]),
        ("discover-queues", ["discover", "queues"]),
        ("discover-albums", ["discover", "albums"]),
        ("radio", ["discover", "radio"]),
    ],
)
def test_rewrite_legacy_argv(old, new, capsys):
    assert cli_compat.rewrite_legacy_argv([old, "--x"]) == new + ["--x"]
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert old in err and "deprecated" in err


def test_rewrite_legacy_argv_leaves_new_commands_alone(capsys):
    assert cli_compat.rewrite_legacy_argv(["sync", "--source", "liked"]) == [
        "sync",
        "--source",
        "liked",
    ]
    assert capsys.readouterr().err == ""




def test_move_global_options_after_subcommand():
    parser = argparse.ArgumentParser()
    parser.add_argument("--headers")
    parser.add_argument("--tui", action="store_true")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("sync")
    assert cli_compat.move_global_options_after_subcommand(
        parser, ["--headers", "x.json", "--tui", "sync", "--source", "liked"]
    ) == ["sync", "--source", "liked", "--headers", "x.json", "--tui"]
