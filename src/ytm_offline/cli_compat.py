"""Compatibility layer between the new CLI parser and the legacy handlers.

This module is intentionally the ONLY place where deprecated command names,
renamed options and the old ``argparse.Namespace`` attribute names are mapped.
Handlers keep their existing signatures; when they are migrated in a later
release this module can shrink and eventually disappear.

Do not add new behaviour here. New behaviour belongs in ``cli_args`` or in the
handlers themselves.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Callable, Sequence

from ytm_offline.naming import SINGLES_DIR_DEFAULT

# Old top-level command -> new command path. The value is prepended to argv.
LEGACY_COMMANDS: dict[str, list[str]] = {
    "retry": ["download-manifest", "--failed-only"],
    "discover-phase4": ["discover", "taste"],
    "discover-queues": ["discover", "queues"],
    "discover-albums": ["discover", "albums"],
    "radio": ["discover", "radio"],
    "tui-demo": ["ui", "demo"],
    "tui-glyphs": ["ui", "glyphs"],
}

LEGACY_OPTION_DEPRECATIONS = {
    "--pick": "--candidate",
    "--top-songs": "--scope top-songs",
    "--quota-deep": "--deep",
    "--quota-adjacent": "--adjacent",
    "--quota-radio": "--radio",
    "--quota-external": "--external",
    "--quota-tag": "--tag",
    "--per-queue-limit": "--per-queue",
    "--include-phase3": "--no-phase3",
    "--browser-profile": "--cookies-from-browser BROWSER[:PROFILE]",
    "--singles-dir": "YTM_SINGLES_DIR",
    "--manifest-format": "YTM_MANIFEST_FORMAT",
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


def _option_present(argv: Sequence[str], option: str) -> bool:
    return any(arg == option or arg.startswith(option + "=") for arg in argv)


def warn_deprecated_options(argv: Sequence[str]) -> None:
    """Emit one line per deprecated option used on the command line."""
    for old, new in LEGACY_OPTION_DEPRECATIONS.items():
        if _option_present(argv, old):
            _warn(f"{old} is deprecated; use {new}")


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
        takes_value = not isinstance(
            action,
            (argparse._StoreTrueAction, argparse._StoreFalseAction, argparse._CountAction),
        )
        for opt in action.option_strings:
            option_info[opt] = takes_value

    sub_action = next(
        (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)),
        None,
    )
    choices = set(sub_action.choices) if sub_action is not None else set()
    cmd_idx = next((i for i, arg in enumerate(argv) if arg in choices), None)
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


def setattrs_missing(args: argparse.Namespace, **kwargs: Any) -> None:
    """Set attributes only when absent; used to keep old handlers happy."""
    for name, value in kwargs.items():
        if not hasattr(args, name):
            setattr(args, name, value)


def _handler(name: str) -> Callable[[argparse.Namespace], int]:
    from ytm_offline import cli

    return getattr(cli, name)


def adapt_library(args: argparse.Namespace) -> int:
    args.limit = args.top
    return _handler("cmd_library")(args)


def adapt_download_one(args: argparse.Namespace) -> int:
    return _handler("cmd_download_one")(args)


def adapt_sync(args: argparse.Namespace) -> int:
    setattrs_missing(args, singles_dir=SINGLES_DIR_DEFAULT)
    return _handler("cmd_sync")(args)


def adapt_download_manifest(args: argparse.Namespace) -> int:
    return _handler("cmd_download_manifest")(args)


def adapt_migrate_layout(args: argparse.Namespace) -> int:
    setattrs_missing(
        args,
        singles_dir=SINGLES_DIR_DEFAULT,
        yes=False,
        dry_run=False,
        undo=False,
    )
    return _handler("cmd_migrate_layout")(args)


def adapt_artist(args: argparse.Namespace) -> int:
    targets = list(getattr(args, "target", []))
    targets.extend(getattr(args, "also", []))
    args.artists = targets
    args.pick = getattr(args, "candidate", None)
    args.limit = getattr(args, "limit", None)

    for flag in (
        "include_live",
        "include_compilations",
        "include_remixes",
        "albums_only",
        "no_singles",
    ):
        setattrs_missing(args, **{flag: False})

    setattrs_missing(
        args,
        duration_window=getattr(args, "duration_window_alias", False),
        scope="all",
        top_limit=None,
        max_per_album=None,
        singles_dir=SINGLES_DIR_DEFAULT,
    )
    return _handler("cmd_artist")(args)


def adapt_discover_sections(args: argparse.Namespace) -> int:
    setattrs_missing(args, singles_dir=SINGLES_DIR_DEFAULT)
    return _handler("cmd_discover")(args)


def adapt_discover_taste(args: argparse.Namespace) -> int:
    args.quota_deep = args.deep
    args.quota_adjacent = args.adjacent
    args.quota_radio = args.radio
    args.quota_external = args.external
    args.quota_tag = args.tag
    args.include_phase3 = not args.no_phase3
    return _handler("cmd_discover_phase4")(args)


def adapt_discover_queues(args: argparse.Namespace) -> int:
    args.download = getattr(args, "download", False) or not args.dry_run
    setattrs_missing(args, singles_dir=SINGLES_DIR_DEFAULT)
    return _handler("cmd_discover_queues")(args)


def adapt_discover_albums(args: argparse.Namespace) -> int:
    args.download = getattr(args, "download", False) or not args.dry_run
    return _handler("cmd_discover_albums")(args)


def adapt_discover_radio(args: argparse.Namespace) -> int:
    if args.limit is None:
        args.limit = 50
    args.download = getattr(args, "download", False) or not args.dry_run
    return _handler("cmd_radio")(args)


def _get_subparsers(
    parser: argparse.ArgumentParser,
) -> argparse._SubParsersAction | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def register_handlers(parser: argparse.ArgumentParser) -> None:
    """Attach the legacy handlers (through adapters) to the new parser."""
    from ytm_offline import cli

    sub = _get_subparsers(parser)
    if sub is None:
        raise RuntimeError("parser has no subparsers")

    sub.choices["auth"].set_defaults(func=cli.cmd_auth)
    sub.choices["library"].set_defaults(func=adapt_library)
    sub.choices["download-one"].set_defaults(func=adapt_download_one)
    sub.choices["sync"].set_defaults(func=adapt_sync)
    sub.choices["download-manifest"].set_defaults(func=adapt_download_manifest)
    sub.choices["artist"].set_defaults(func=adapt_artist)
    sub.choices["migrate-layout"].set_defaults(func=adapt_migrate_layout)

    discover_sub = _get_subparsers(sub.choices["discover"])
    if discover_sub is not None:
        discover_sub.choices["sections"].set_defaults(func=adapt_discover_sections)
        discover_sub.choices["taste"].set_defaults(func=adapt_discover_taste)
        discover_sub.choices["queues"].set_defaults(func=adapt_discover_queues)
        discover_sub.choices["albums"].set_defaults(func=adapt_discover_albums)
        discover_sub.choices["radio"].set_defaults(func=adapt_discover_radio)

    ui_sub = _get_subparsers(sub.choices["ui"])
    if ui_sub is not None:
        ui_sub.choices["demo"].set_defaults(func=cli.cmd_tui_demo)
        ui_sub.choices["glyphs"].set_defaults(func=cli.cmd_tui_glyphs)
