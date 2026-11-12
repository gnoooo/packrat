"""CLI wiring: legacy command names, global-option placement, handlers.

Only command aliases remain; option aliases were removed in 0.2.0.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence


# Old top-level command -> new command path. The value is prepended to argv.
LEGACY_COMMANDS: dict[str, list[str]] = {
    "retry": ["download-manifest", "--failed-only"],
    "discover-phase4": ["discover", "taste"],
    "discover-queues": ["discover", "queues"],
    "discover-albums": ["discover", "albums"],
    "radio": ["discover", "radio"],
}

def _warn(message: str) -> None:
    print(message, file=sys.stderr)


def rewrite_legacy_argv(argv: Sequence[str]) -> list[str]:
    """Map deprecated top-level command names to the new hierarchy.

    Prints exactly one deprecation line to stderr when a rewrite happens.
    """
    out = list(argv)
    if out and out[0] in LEGACY_COMMANDS:
        old = out[0]
        new = LEGACY_COMMANDS[old]
        _warn(f"{old} is deprecated; use {' '.join(new)}")
        return new + out[1:]
    return out


def move_global_options_after_subcommand(
    parser: argparse.ArgumentParser, argv: list[str]
) -> list[str]:
    """Move top-level options placed before the subcommand to after it.

    argparse applies subparser defaults after top-level actions, so a value
    given before the subcommand can be overwritten by a subparser default.
    Re-ordering makes the subparser action process the value last.
    """
    option_info: dict[str, bool] = {}
    for action in parser._actions:
        if not action.option_strings:
            continue
        takes_value = action.nargs != 0
        for opt in action.option_strings:
            option_info[opt] = takes_value

    sub_action = next(
        (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)),
        None,
    )
    choices = set(sub_action.choices) if sub_action is not None else set()
    cmd_idx = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in choices:
            cmd_idx = i
            break
        base = arg.split("=", 1)[0]
        if base in option_info:
            if option_info[base] and "=" not in arg and i + 1 < len(argv):
                i += 2
            else:
                i += 1
        else:
            i += 1
    if cmd_idx is None or cmd_idx == 0:
        return argv

    head, tail = argv[:cmd_idx], argv[cmd_idx:]
    moved: list[str] = []
    kept: list[str] = []
    i = 0
    while i < len(head):
        arg = head[i]
        base = arg.split("=", 1)[0]
        if base in option_info:
            moved.append(arg)
            if "=" not in arg and option_info[base] and i + 1 < len(head):
                moved.append(head[i + 1])
                i += 2
                continue
            i += 1
        else:
            kept.append(arg)
            i += 1
    return kept + tail + moved


def _get_subparsers(
    parser: argparse.ArgumentParser,
) -> argparse._SubParsersAction | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def register_handlers(parser: argparse.ArgumentParser) -> None:
    """Attach the legacy handlers (through adapters) to the new parser."""
    from packrat import cli

    sub = _get_subparsers(parser)
    if sub is None:
        raise RuntimeError("parser has no subparsers")

    sub.choices["auth"].set_defaults(func=cli.cmd_auth)
    sub.choices["login"].set_defaults(func=cli.cmd_login)
    sub.choices["logout"].set_defaults(func=cli.cmd_logout)
    sub.choices["doctor"].set_defaults(func=cli.cmd_doctor)
    sub.choices["upgrade"].set_defaults(func=cli.cmd_upgrade)
    sub.choices["export-metadata"].set_defaults(func=cli.cmd_export_metadata)
    sub.choices["library"].set_defaults(func=cli.cmd_library)
    sub.choices["download-one"].set_defaults(func=cli.cmd_download_one)
    sub.choices["sync"].set_defaults(func=cli.cmd_sync)
    sub.choices["download-manifest"].set_defaults(func=cli.cmd_download_manifest)
    sub.choices["artist"].set_defaults(func=cli.cmd_artist)

    discover_sub = _get_subparsers(sub.choices["discover"])
    if discover_sub is not None:
        discover_sub.choices["sections"].set_defaults(func=cli.cmd_discover)
        discover_sub.choices["taste"].set_defaults(func=cli.cmd_discover_phase4)
        discover_sub.choices["queues"].set_defaults(func=cli.cmd_discover_queues)
        discover_sub.choices["albums"].set_defaults(func=cli.cmd_discover_albums)
        discover_sub.choices["radio"].set_defaults(func=cli.cmd_radio)
