"""Snapshot every visible command's --help output."""

from __future__ import annotations

import argparse
import pathlib

import pytest

from ytm_offline import cli

SNAP = pathlib.Path(__file__).parent / "snapshots" / "help"

COMMANDS = [
    (),
    ("auth",),
    ("library",),
    ("download-one",),
    ("sync",),
    ("download-manifest",),
    ("artist",),
    ("discover",),
    ("discover", "sections"),
    ("discover", "taste"),
    ("discover", "queues"),
    ("discover", "albums"),
    ("discover", "radio"),
    ("ui",),
    ("ui", "demo"),
    ("ui", "glyphs"),
    ("migrate-layout",),
]


def _subparsers(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _help_for(path):
    parser = cli.build_parser()
    if not path:
        return parser.format_help()
    node = parser
    for name in path:
        node = _subparsers(node).choices[name]
    return node.format_help()


@pytest.mark.parametrize("path", COMMANDS, ids=lambda p: "-".join(p) or "top")
def test_help_snapshot(path):
    name = "-".join(path) or "top"
    expected = (SNAP / f"{name}.txt").read_text(encoding="utf-8")
    assert _help_for(path) == expected
