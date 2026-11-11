"""Tests for the temporary CLI compatibility layer."""

from __future__ import annotations

import argparse

import pytest

from ytm_offline import cli_compat


@pytest.mark.parametrize(
    "old,new",
    [
        ("retry", ["download-manifest", "--failed-only"]),
        ("discover-phase4", ["discover", "taste"]),
        ("discover-queues", ["discover", "queues"]),
        ("discover-albums", ["discover", "albums"]),
        ("radio", ["discover", "radio"]),
        ("tui-demo", ["ui", "demo"]),
        ("tui-glyphs", ["ui", "glyphs"]),
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


def test_warn_deprecated_options_is_one_line_per_option(capsys):
    cli_compat.warn_deprecated_options(["--pick", "2", "--top-songs"])
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 2
    assert "--pick" in lines[0]
    assert "--top-songs" in lines[1]


def test_move_global_options_after_subcommand():
    parser = argparse.ArgumentParser()
    parser.add_argument("--headers")
    parser.add_argument("--tui", action="store_true")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("sync")
    assert cli_compat.move_global_options_after_subcommand(
        parser, ["--headers", "x.json", "--tui", "sync", "--source", "liked"]
    ) == ["sync", "--source", "liked", "--headers", "x.json", "--tui"]


def test_adapt_library_maps_top_to_limit(monkeypatch):
    seen = {}

    def fake_handler(name):
        def run(args):
            seen["limit"] = args.limit
            return 0

        return run

    monkeypatch.setattr(cli_compat, "_handler", fake_handler)
    args = argparse.Namespace(top=7)
    assert cli_compat.adapt_library(args) == 0
    assert seen["limit"] == 7
