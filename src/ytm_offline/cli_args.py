"""Shared CLI argument definitions for ytm_offline.

Design goals
------------
* Every option is declared once and reused via parent parsers.
* Options that make sense globally can appear before or after the subcommand.
* Env-var precedence (CLI > env > default) is handled through argparse defaults
  and post-parse resolution helpers.
* Authentication never accepts secrets on the command line by default; API keys
  stay in env vars.
"""

from __future__ import annotations

import argparse
import os
import pathlib
from typing import Any

from ytm_offline.naming import SINGLES_DIR_DEFAULT
from ytm_offline.sizes import parse_size

DEFAULT_OUTPUT = "music"
DEFAULT_ARCHIVE = ".ytm_offline_archive"
DEFAULT_DELAY = 7.0
DEFAULT_THEME = "ocean"
DEFAULT_PALETTE = "auto"
DEFAULT_MANIFEST_FORMAT = "json"

HEADERS_CONFIG_PATH = "~/.config/ytmusic-composer/browser.json"


# ---------------------------------------------------------------------------
# Env-aware defaults
# ---------------------------------------------------------------------------

def _headers_default() -> str | None:
    env = os.environ.get("YTM_HEADERS")
    if env:
        return env
    p = pathlib.Path(HEADERS_CONFIG_PATH).expanduser()
    return str(p) if p.exists() else None


def _cookies_default() -> str | None:
    return os.environ.get("YTM_COOKIES")


def _cookies_from_browser_default() -> str | None:
    return os.environ.get("YTM_COOKIES_FROM_BROWSER")


def _output_default() -> str:
    return os.environ.get("YTM_OUTPUT", DEFAULT_OUTPUT)


def _archive_default() -> str:
    return os.environ.get("YTM_ARCHIVE", DEFAULT_ARCHIVE)


def _singles_dir_default() -> str:
    return os.environ.get("YTM_SINGLES_DIR", SINGLES_DIR_DEFAULT)


def _manifest_format_default() -> str:
    return os.environ.get("YTM_MANIFEST_FORMAT", DEFAULT_MANIFEST_FORMAT)


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

class ArgumentTypeError(argparse.ArgumentTypeError):  # noqa: N818
    """Subclass so our validators exit 2 like argparse expects."""


def positive_int(value: str) -> int:
    try:
        iv = int(value)
    except ValueError as exc:
        raise ArgumentTypeError(f"must be an integer, got {value!r}") from exc
    if iv <= 0:
        raise ArgumentTypeError(f"must be positive, got {iv}")
    return iv


def non_negative_int(value: str) -> int:
    try:
        iv = int(value)
    except ValueError as exc:
        raise ArgumentTypeError(f"must be an integer, got {value!r}") from exc
    if iv < 0:
        raise ArgumentTypeError(f"must be non-negative, got {iv}")
    return iv


def non_negative_float(value: str) -> float:
    try:
        fv = float(value)
    except ValueError as exc:
        raise ArgumentTypeError(f"must be a number, got {value!r}") from exc
    if fv < 0:
        raise ArgumentTypeError(f"must be non-negative, got {fv}")
    return fv


def valid_size(value: str) -> str:
    """Validate a size string; keep the original for late parsing."""
    try:
        parse_size(value)
    except Exception as exc:  # noqa: BLE001 (size parser may raise generic errors)
        raise ArgumentTypeError(f"invalid size {value!r}: {exc}") from exc
    return value


def existing_path(value: str) -> str:
    p = pathlib.Path(value)
    if not p.exists():
        raise ArgumentTypeError(f"path not found: {value}")
    return str(p)


def valid_singles_dir(value: str) -> str:
    if not value:
        raise ArgumentTypeError("singles folder name cannot be empty")
    if value == "..":
        raise ArgumentTypeError("singles folder name cannot be '..'")
    if os.sep in value or (os.altsep and os.altsep in value):
        raise ArgumentTypeError(
            "singles folder name must be a single folder name, not a path"
        )
    return value


def valid_manifest_format(value: str) -> str:
    """Accept json, csv (deprecated alias), or json+csv; return canonical value."""
    v = value.lower()
    if v == "json":
        return "json"
    if v in {"csv", "json+csv"}:
        return "json+csv"
    raise ArgumentTypeError(
        f"manifest-format must be 'json' or 'json+csv', got {value!r}"
    )


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

class CountVerboseAction(argparse.Action):
    """`-v` increments once; `-vv` increments twice.

    0 = default (INFO), 1 = our DEBUG, 2 = also yt-dlp verbose.
    """

    def __init__(self, option_strings: list[str], dest: str, default: int = 0, **kwargs: Any):
        kwargs["nargs"] = 0
        super().__init__(option_strings, dest, default=default, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        current = getattr(namespace, self.dest, 0) or 0
        setattr(namespace, self.dest, current + 1)


# ---------------------------------------------------------------------------
# Parent parser builders
#
# suppress_defaults=True  -> default=argparse.SUPPRESS so a value given before
#                            the subcommand is not clobbered by a default.
# suppress_help=True      -> help=argparse.SUPPRESS (for silent global copies).
# ---------------------------------------------------------------------------

def _help_text(text: str, suppress: bool) -> str:
    return argparse.SUPPRESS if suppress else text


def build_auth_parent(suppress_defaults: bool = False, suppress_help: bool = False) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    g = p.add_argument_group("Authentication")

    default = argparse.SUPPRESS if suppress_defaults else _headers_default()
    g.add_argument(
        "--headers",
        default=default,
        help=_help_text(
            "path to ytmusicapi browser-headers JSON file (env: YTM_HEADERS)",
            suppress_help,
        ),
    )

    default = argparse.SUPPRESS if suppress_defaults else _cookies_default()
    g.add_argument(
        "--cookies",
        default=default,
        help=_help_text(
            "path to Netscape cookies.txt for yt-dlp (env: YTM_COOKIES)",
            suppress_help,
        ),
    )

    default = argparse.SUPPRESS if suppress_defaults else _cookies_from_browser_default()
    g.add_argument(
        "--cookies-from-browser",
        dest="cookies_from_browser",
        default=default,
        help=_help_text(
            "browser/profile for yt-dlp --cookies-from-browser (env: YTM_COOKIES_FROM_BROWSER)",
            suppress_help,
        ),
    )

    # Deprecated alias: combined with --cookies-from-browser in post-processing.
    g.add_argument(
        "--browser-profile",
        dest="browser_profile_alias",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    return p


_UNSET = object()


def build_run_control_parent(
    suppress_defaults: bool = False,
    suppress_help: bool = False,
    limit_default: int | None = None,
    limit_help: str | None = None,
    output_default: str | None | object = _UNSET,
    output_help: str | None = None,
) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    g = p.add_argument_group("Run control")

    if output_default is _UNSET:
        default = argparse.SUPPRESS if suppress_defaults else _output_default()
    else:
        default = output_default
    help_text = output_help or f"output root directory (env: YTM_OUTPUT; default: {DEFAULT_OUTPUT})"
    g.add_argument(
        "--output",
        "-o",
        dest="output",
        default=default,
        help=_help_text(help_text, suppress_help),
    )

    default = argparse.SUPPRESS if suppress_defaults else _archive_default()
    g.add_argument(
        "--archive",
        dest="archive",
        default=default,
        help=_help_text(
            f"download archive file (env: YTM_ARCHIVE; default: {DEFAULT_ARCHIVE})",
            suppress_help,
        ),
    )

    default = argparse.SUPPRESS if suppress_defaults else DEFAULT_DELAY
    g.add_argument(
        "--delay",
        dest="delay",
        type=non_negative_float,
        default=default,
        help=_help_text(
            f"seconds between downloads (default: {DEFAULT_DELAY})",
            suppress_help,
        ),
    )

    default = argparse.SUPPRESS if suppress_defaults else None
    g.add_argument(
        "--max-size",
        dest="max_size",
        type=valid_size,
        default=default,
        help=_help_text(
            "skip tracks estimated larger than SIZE (e.g. 3GB)",
            suppress_help,
        ),
    )

    limit_action_default = (
        argparse.SUPPRESS
        if (suppress_defaults and limit_default is None)
        else limit_default
    )
    limit_help_text = (
        limit_help
        if limit_help is not None
        else "max new tracks to download this run (after dedupe and selection)"
    )
    g.add_argument(
        "--limit",
        dest="limit",
        type=positive_int,
        default=limit_action_default,
        help=_help_text(limit_help_text, suppress_help),
    )

    dry_default = argparse.SUPPRESS if suppress_defaults else False
    g.add_argument(
        "--dry-run",
        "-n",
        dest="dry_run",
        action="store_true",
        default=dry_default,
        help=_help_text(
            "write the manifest, download nothing, touch no archive",
            suppress_help,
        ),
    )

    yes_default = argparse.SUPPRESS if suppress_defaults else False
    g.add_argument(
        "--yes",
        "-y",
        dest="yes",
        action="store_true",
        default=yes_default,
        help=_help_text(
            "skip confirmation prompts and use the default selection",
            suppress_help,
        ),
    )

    tui_default = argparse.SUPPRESS if suppress_defaults else False
    g.add_argument(
        "--tui",
        dest="tui",
        action="store_true",
        default=tui_default,
        help=_help_text("use the curses TUI", suppress_help),
    )

    # Hidden safety valve for new-archive detection.
    g.add_argument(
        "--force-new-archive",
        dest="force_new_archive",
        action="store_true",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )

    # Deprecated layout/format env aliases.
    g.add_argument(
        "--singles-dir",
        dest="singles_dir_alias",
        type=valid_singles_dir,
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    g.add_argument(
        "--manifest-format",
        dest="manifest_format_alias",
        type=valid_manifest_format,
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    return p


def build_duration_parent(
    suppress_defaults: bool = False, suppress_help: bool = False
) -> argparse.ArgumentParser:
    """Duration filter only (used by commands without a picker)."""
    p = argparse.ArgumentParser(add_help=False)
    g = p.add_argument_group("Selection")

    default_min = argparse.SUPPRESS if suppress_defaults else 90
    g.add_argument(
        "--min-duration",
        dest="min_duration",
        type=non_negative_int,
        default=default_min,
        help=_help_text(
            "minimum track duration in seconds (default: 90)",
            suppress_help,
        ),
    )

    default_max = argparse.SUPPRESS if suppress_defaults else 540
    g.add_argument(
        "--max-duration",
        dest="max_duration",
        type=non_negative_int,
        default=default_max,
        help=_help_text(
            "maximum track duration in seconds (default: 540)",
            suppress_help,
        ),
    )
    return p


def build_selection_parent(
    suppress_defaults: bool = False, suppress_help: bool = False
) -> argparse.ArgumentParser:
    p = build_duration_parent(suppress_defaults, suppress_help)
    g = p._action_groups[0]

    g.add_argument(
        "--select",
        dest="select",
        default=argparse.SUPPRESS if suppress_defaults else None,
        help=_help_text(
            "non-interactive selection expression (e.g. A[1-3],S[*])",
            suppress_help,
        ),
    )
    return p


def build_artist_selection_parent(
    suppress_defaults: bool = False, suppress_help: bool = False
) -> argparse.ArgumentParser:
    """Artist-specific selection knobs."""
    p = argparse.ArgumentParser(add_help=False)
    g = p.add_argument_group("Selection")

    g.add_argument(
        "--candidate",
        "-c",
        dest="candidate",
        type=positive_int,
        default=argparse.SUPPRESS if suppress_defaults else None,
        help=_help_text(
            "pick the Nth search candidate when the name is ambiguous",
            suppress_help,
        ),
    )

    # Deprecated alias for --candidate.
    g.add_argument(
        "--pick",
        dest="candidate",
        type=positive_int,
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )

    # Deprecated artist include/exclude flags.
    for flag in (
        "--include-live",
        "--include-compilations",
        "--include-remixes",
        "--albums-only",
        "--no-singles",
    ):
        dest = flag.lstrip("-").replace("-", "_")
        g.add_argument(
            flag,
            dest=dest,
            action="store_true",
            default=argparse.SUPPRESS,
            help=argparse.SUPPRESS,
        )

    g.add_argument(
        "--duration-window",
        dest="duration_window_alias",
        action="store_true",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )

    return p


def build_display_parent(
    suppress_defaults: bool = False, suppress_help: bool = False
) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    g = p.add_argument_group("Display")

    g.add_argument(
        "--theme",
        dest="theme",
        default=argparse.SUPPRESS if suppress_defaults else DEFAULT_THEME,
        help=_help_text(
            f"TUI color theme (ocean/sunset/matrix/mono) (default: {DEFAULT_THEME})",
            suppress_help,
        ),
    )

    g.add_argument(
        "--no-anim",
        dest="no_anim",
        action="store_true",
        default=argparse.SUPPRESS if suppress_defaults else False,
        help=_help_text(
            "disable TUI animations (env: YTM_TUI_ANIM=0)",
            suppress_help,
        ),
    )

    g.add_argument(
        "--no-color",
        dest="no_color",
        action="store_true",
        default=argparse.SUPPRESS if suppress_defaults else False,
        help=_help_text(
            "disable TUI colors (env: NO_COLOR)",
            suppress_help,
        ),
    )

    g.add_argument(
        "--ascii",
        dest="ascii",
        action="store_true",
        default=argparse.SUPPRESS if suppress_defaults else False,
        help=_help_text("use ASCII progress bars in the TUI", suppress_help),
    )

    g.add_argument(
        "--palette",
        dest="palette",
        choices=["auto", "custom", "fixed", "basic"],
        default=argparse.SUPPRESS if suppress_defaults else DEFAULT_PALETTE,
        help=_help_text(
            f"color tier (auto/custom/fixed/basic) (default: {DEFAULT_PALETTE})",
            suppress_help,
        ),
    )
    return p


def build_verbose_parent(
    suppress_defaults: bool = False, suppress_help: bool = False
) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    default = argparse.SUPPRESS if suppress_defaults else 0
    p.add_argument(
        "-v",
        "--verbose",
        dest="verbose",
        action=CountVerboseAction,
        default=default,
        help=_help_text(
            "verbose logging (-v = debug, -vv = yt-dlp verbose)",
            suppress_help,
        ),
    )
    return p


# ---------------------------------------------------------------------------
# Help formatter: hide subcommands whose help is None.
# ---------------------------------------------------------------------------

class _HelpFormatter(argparse.HelpFormatter):
    """Hides subcommands marked with _hidden=True from usage and choices list."""

    def _metavar_formatter(
        self, action: argparse.Action, default_metavar: str
    ) -> Any:
        if isinstance(action, argparse._SubParsersAction):
            visible = [
                name
                for name, p in action.choices.items()
                if not getattr(p, "_hidden", False)
            ]
            text = "{%s}" % ",".join(visible)

            def format_func(tuple_size: int) -> tuple[str, ...]:
                return (text,) * tuple_size

            return format_func
        return super()._metavar_formatter(action, default_metavar)

    def _format_action(self, action: argparse.Action) -> str:
        if isinstance(action, argparse._SubParsersAction):
            visible_choices = {
                name: p
                for name, p in action.choices.items()
                if not getattr(p, "_hidden", False)
            }
            if not visible_choices:
                return ""
            old_choices = action.choices
            old_actions = action._choices_actions
            action.choices = visible_choices
            action._choices_actions = [
                a for a in old_actions if a.dest in visible_choices
            ]
            result = super()._format_action(action)
            action.choices = old_choices
            action._choices_actions = old_actions
            return result
        return super()._format_action(action)


# ---------------------------------------------------------------------------
# Post-parse resolution helpers
# ---------------------------------------------------------------------------

def _cli_set(args: argparse.Namespace, dest: str) -> bool:
    """True if the value was provided on the CLI (not inherited from a default)."""
    return hasattr(args, dest)


def parse_cookies_from_browser(
    spec: str,
) -> tuple[str, str | None, str | None, str | None]:
    """Parse yt-dlp's BROWSER[+KEYRING][:PROFILE][::CONTAINER] syntax."""
    import re

    from yt_dlp.cookies import SUPPORTED_BROWSERS, SUPPORTED_KEYRINGS

    match = re.fullmatch(
        r"""(?x)
        (?P<name>[^+:]+)
        (?:\s*\+\s*(?P<keyring>[^:]+))?
        (?:\s*:\s*(?!:)(?P<profile>.+?))?
        (?:\s*::\s*(?P<container>.+))?
        """,
        spec,
    )
    if match is None:
        raise ValueError(f"invalid cookies-from-browser value: {spec!r}")
    browser = match.group("name").strip().lower()
    keyring = match.group("keyring")
    profile = match.group("profile")
    container = match.group("container")
    if browser not in SUPPORTED_BROWSERS:
        raise ValueError(
            f"unsupported browser {browser!r}; supported: "
            + ", ".join(sorted(SUPPORTED_BROWSERS))
        )
    if keyring is not None:
        keyring = keyring.strip().upper()
        if keyring not in SUPPORTED_KEYRINGS:
            raise ValueError(
                f"unsupported keyring {keyring!r}; supported: "
                + ", ".join(sorted(SUPPORTED_KEYRINGS))
            )
    return browser, profile, keyring, container


def _has_option(argv: list[str], name: str) -> bool:
    return any(arg == name or arg.startswith(f"{name}=") for arg in argv)


def resolve_auth(args: argparse.Namespace, argv: list[str] | None = None) -> None:
    """Enforce cookie auth rules and fold deprecated aliases.

    Rules (CLI > env > default):
    * --cookies and --cookies-from-browser are mutually exclusive only when both
      are given on the CLI.
    * A CLI value overrides an env var of the other kind without error.
    * --browser-profile P given with --cookies-from-browser BROWSER becomes
      BROWSER:P with a deprecation line printed to stderr.
    """
    import sys

    if argv is None:
        argv = sys.argv[1:]

    cookies_cli = _has_option(argv, "--cookies")
    cfb_cli = _has_option(argv, "--cookies-from-browser")

    if cookies_cli and cfb_cli:
        print(
            "error: --cookies and --cookies-from-browser are mutually exclusive",
            file=sys.stderr,
        )
        raise SystemExit(2)

    # A CLI value of one kind overrides an env value of the other kind.
    if cookies_cli:
        args.cookies_from_browser = None
    elif cfb_cli:
        args.cookies = None

    # Fold deprecated --browser-profile alias.
    if _has_option(argv, "--browser-profile"):
        alias = args.browser_profile_alias
        if not cfb_cli:
            print(
                "error: --browser-profile requires --cookies-from-browser",
                file=sys.stderr,
            )
            raise SystemExit(2)
        args.cookies_from_browser = f"{args.cookies_from_browser}:{alias}"
        print(
            "--browser-profile is deprecated; use --cookies-from-browser BROWSER:PROFILE",
            file=sys.stderr,
        )
        delattr(args, "browser_profile_alias")

    if cfb_cli and not args.cookies_from_browser:
        print("error: --cookies-from-browser must not be empty", file=sys.stderr)
        raise SystemExit(2)
    if args.cookies_from_browser:
        try:
            parse_cookies_from_browser(args.cookies_from_browser)
        except ValueError as exc:
            print(f"error: --cookies-from-browser: {exc}", file=sys.stderr)
            raise SystemExit(2) from None


def resolve_run_control_aliases(args: argparse.Namespace) -> None:
    """Fold deprecated --singles-dir and --manifest-format flag aliases."""
    import sys

    if _cli_set(args, "singles_dir_alias"):
        args.singles_dir = args.singles_dir_alias
        print(
            "--singles-dir is deprecated; set YTM_SINGLES_DIR instead",
            file=sys.stderr,
        )
        delattr(args, "singles_dir_alias")
    elif not hasattr(args, "singles_dir"):
        try:
            args.singles_dir = valid_singles_dir(_singles_dir_default())
        except ArgumentTypeError as exc:
            print(f"error: YTM_SINGLES_DIR: {exc}", file=sys.stderr)
            raise SystemExit(2) from None

    if _cli_set(args, "manifest_format_alias"):
        fmt = args.manifest_format_alias
        print(
            "--manifest-format is deprecated; it now writes JSON + CSV sidecar (set YTM_MANIFEST_FORMAT instead)",
            file=sys.stderr,
        )
        args.manifest_format = fmt
        delattr(args, "manifest_format_alias")
    elif not hasattr(args, "manifest_format"):
        fmt = _manifest_format_default()
        if fmt == "csv":
            print(
                "YTM_MANIFEST_FORMAT=csv is deprecated; it now writes JSON + CSV sidecar",
                file=sys.stderr,
            )
            fmt = "json+csv"
        try:
            args.manifest_format = valid_manifest_format(fmt)
        except ArgumentTypeError as exc:
            print(f"error: YTM_MANIFEST_FORMAT: {exc}", file=sys.stderr)
            raise SystemExit(2) from None


def resolve_verbose(args: argparse.Namespace) -> int:
    """Return 0/1/2 from the -v count action."""
    return getattr(args, "verbose", 0) or 0


# ---------------------------------------------------------------------------
# Full parser (Phase 1 skeleton)
# ---------------------------------------------------------------------------

def _noop(*_args: Any, **_kwargs: Any) -> int:
    """Placeholder command handler until Phase 2 migration."""
    raise NotImplementedError("command not migrated yet")


def build_parser() -> argparse.ArgumentParser:
    """Return the new argparse parser with shared parents wired in.

    The top-level parser shows Authentication and Display help; Run control and
    Selection are accepted silently before the subcommand so they work in either
    position without cluttering the top-level help.
    """
    verbose_silent = build_verbose_parent(suppress_defaults=True, suppress_help=True)
    parser = argparse.ArgumentParser(
        prog="ytm-offline",
        description="Download music from your YouTube Music account for offline use.",
        formatter_class=_HelpFormatter,
        parents=[
            build_auth_parent(),
            build_display_parent(),
            build_verbose_parent(),
        ],
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")

    # Accept run-control / selection flags before the subcommand, silently.
    silent_run = build_run_control_parent(suppress_help=True)
    silent_sel = build_selection_parent(suppress_help=True)
    for parent in (silent_run, silent_sel):
        for action in parent._actions:
            # Avoid re-adding the title-only group stub; copy real actions only.
            if action.option_strings:
                kwargs = {k: getattr(action, k) for k in action.__dict__ if k != "container"}
                parser._add_action(action)

    sub = parser.add_subparsers(dest="command", required=True)

    # Auth-only helpers
    p_auth = sub.add_parser(
        "auth",
        help="validate ytmusicapi auth",
        parents=[
            build_auth_parent(suppress_defaults=True),
            build_display_parent(suppress_defaults=True),
            verbose_silent,
        ],
    )
    p_auth.set_defaults(func=_noop)

    p_library = sub.add_parser(
        "library",
        help="list library playlists",
        parents=[
            build_auth_parent(suppress_defaults=True),
            build_display_parent(suppress_defaults=True),
            verbose_silent,
        ],
    )
    p_library.add_argument(
        "--top",
        dest="top",
        type=positive_int,
        default=25,
        help="max playlists to show (default: 25)",
    )
    # Deprecated library alias.
    p_library.add_argument(
        "--limit",
        dest="top",
        type=positive_int,
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    p_library.set_defaults(func=_noop)

    # Download commands share auth + run-control + display + verbose.
    dl_parents = [
        build_auth_parent(suppress_defaults=True),
        build_run_control_parent(suppress_defaults=True),
        build_display_parent(suppress_defaults=True),
        verbose_silent,
    ]

    p_download_one = sub.add_parser(
        "download-one",
        help="download and verify one track by video ID",
        parents=dl_parents,
    )
    p_download_one.add_argument("video_id", help="YouTube Music video ID")
    p_download_one.set_defaults(func=_noop)

    p_sync = sub.add_parser(
        "sync",
        help="sync liked songs or a playlist",
        parents=dl_parents,
    )
    p_sync.add_argument(
        "--source",
        required=True,
        help="liked or playlist:<id/url>",
    )
    p_sync.set_defaults(func=_noop)

    manifest_parents = [
        build_auth_parent(suppress_defaults=True),
        build_run_control_parent(
            suppress_defaults=True,
            output_default=None,
            output_help="output root directory (default: parent of playlists/)",
        ),
        build_display_parent(suppress_defaults=True),
        verbose_silent,
    ]
    p_manifest = sub.add_parser(
        "download-manifest",
        help="resume/failed downloads from an existing manifest",
        parents=manifest_parents,
    )
    p_manifest.add_argument("--manifest", required=True, help="path to a manifest JSON file")
    p_manifest.add_argument(
        "--failed-only",
        dest="failed_only",
        action="store_true",
        default=False,
        help="retry only failed/pending entries",
    )
    p_manifest.set_defaults(func=_noop)

    p_artist = sub.add_parser(
        "artist",
        help="download one or more artists' full catalogs",
        parents=dl_parents
        + [
            build_selection_parent(suppress_defaults=True),
            build_artist_selection_parent(suppress_defaults=True),
        ],
    )
    p_artist.add_argument(
        "target",
        nargs="*",
        default=[],
        help="artist name, channelId, or channel URL",
    )
    p_artist.add_argument(
        "--from-file",
        dest="from_file",
        help="text file with one artist spec per line",
    )
    p_artist.add_argument(
        "--also",
        dest="also",
        action="append",
        default=[],
        help="additional artist spec (repeatable)",
    )
    p_artist.set_defaults(func=_noop)

    # Discover command group
    p_discover = sub.add_parser(
        "discover",
        help="discover tracks (sections, taste, queues, albums, radio)",
    )
    discover_sub = p_discover.add_subparsers(dest="discover_command", required=True)

    p_sections = discover_sub.add_parser(
        "sections",
        help="Home/Charts/Moods/New Releases",
        parents=dl_parents + [build_duration_parent(suppress_defaults=True)],
    )
    p_sections.add_argument(
        "--source",
        dest="source",
        default="home,charts,moods,new_releases",
        help="comma-separated discovery sources (default: home,charts,moods,new_releases)",
    )
    p_sections.add_argument(
        "--country",
        dest="country",
        default="FR",
        help="country for charts (default: FR)",
    )
    p_sections.add_argument(
        "--quota",
        dest="quota",
        type=positive_int,
        default=10,
        help="max tracks per source (default: 10)",
    )
    p_sections.add_argument(
        "--per-container",
        dest="per_container",
        type=positive_int,
        default=5,
        help="max tracks per container (default: 5)",
    )
    p_sections.add_argument(
        "--per-artist",
        dest="per_artist",
        type=positive_int,
        default=3,
        help="max tracks per artist per source (default: 3)",
    )

    p_taste = discover_sub.add_parser(
        "taste",
        help="taste-adjacent tracks beyond YT Music sections",
        parents=dl_parents + [build_duration_parent(suppress_defaults=True)],
    )
    p_taste.add_argument(
        "--deep",
        "--quota-deep",
        dest="deep",
        type=positive_int,
        default=12,
        help="max deep-cut tracks (default: 12)",
    )
    p_taste.add_argument(
        "--adjacent",
        "--quota-adjacent",
        dest="adjacent",
        type=positive_int,
        default=10,
        help="max adjacent-artist tracks (default: 10)",
    )
    p_taste.add_argument(
        "--radio",
        "--quota-radio",
        dest="radio",
        type=positive_int,
        default=10,
        help="max radio tracks (default: 10)",
    )
    p_taste.add_argument(
        "--external",
        "--quota-external",
        dest="external",
        type=positive_int,
        default=5,
        help="max external-graph tracks (default: 5)",
    )
    p_taste.add_argument(
        "--tag",
        "--quota-tag",
        dest="tag",
        type=positive_int,
        default=5,
        help="max tag-search tracks (default: 5)",
    )
    p_taste.add_argument(
        "--per-container",
        dest="per_container",
        type=positive_int,
        default=2,
        help="max tracks per album/container (default: 2)",
    )
    p_taste.add_argument(
        "--per-artist",
        dest="per_artist",
        type=positive_int,
        default=3,
        help="max tracks per artist (default: 3)",
    )
    p_taste.add_argument(
        "--no-phase3",
        dest="no_phase3",
        action="store_true",
        default=False,
        help="do not dedupe against the phase 3 discover manifest",
    )
    # Deprecated alias with inverted semantics.
    p_taste.add_argument(
        "--include-phase3",
        dest="no_phase3",
        action="store_false",
        default=False,
        help=argparse.SUPPRESS,
    )

    p_queues = discover_sub.add_parser(
        "queues",
        help="playback-queue playlists from liked-track seeds",
        parents=dl_parents + [build_duration_parent(suppress_defaults=True)],
    )
    p_queues.add_argument(
        "--seed-count",
        dest="seed_count",
        type=positive_int,
        default=12,
        help="number of liked-track seeds (default: 12)",
    )
    p_queues.add_argument(
        "--hop-depth",
        dest="hop_depth",
        type=positive_int,
        default=1,
        help="queue hop depth (default: 1)",
    )
    p_queues.add_argument(
        "--hop-width",
        dest="hop_width",
        type=positive_int,
        default=3,
        help="tracks to seed second-hop queues (default: 3)",
    )
    p_queues.add_argument(
        "--per-artist-per-queue",
        dest="per_artist_per_queue",
        type=positive_int,
        default=3,
        help="max tracks per artist within one queue (default: 3)",
    )
    p_queues.add_argument(
        "--per-queue",
        "--per-queue-limit",
        dest="per_queue_limit",
        type=positive_int,
        default=25,
        help="max tracks per queue playlist (default: 25)",
    )
    p_queues.add_argument(
        "--total-cap",
        dest="limit",
        type=positive_int,
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    p_queues.add_argument(
        "--random-seed",
        dest="random_seed",
        type=int,
        default=None,
        help="seed for reproducible seed selection",
    )
    p_queues.add_argument(
        "--download",
        dest="download",
        action="store_true",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )

    p_albums = discover_sub.add_parser(
        "albums",
        help="whole albums from top liked artists",
        parents=dl_parents + [build_duration_parent(suppress_defaults=True)],
    )
    p_albums.add_argument(
        "--artist-top-k",
        dest="artist_top_k",
        type=positive_int,
        default=15,
        help="number of top liked artists to consider (default: 15)",
    )
    p_albums.add_argument(
        "--albums-per-artist",
        dest="albums_per_artist",
        type=positive_int,
        default=2,
        help="max albums per artist (default: 2)",
    )
    p_albums.add_argument(
        "--include-singles",
        dest="include_singles",
        action="store_true",
        default=False,
        help="include singles/EPs",
    )
    p_albums.add_argument(
        "--download",
        dest="download",
        action="store_true",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )

    p_radio = discover_sub.add_parser(
        "radio",
        help="auto radio built from one track URL/id",
        parents=[
            build_auth_parent(suppress_defaults=True),
            build_run_control_parent(
                suppress_defaults=True,
                limit_default=None,
                limit_help="max tracks in the radio (default: 50)",
            ),
            build_display_parent(suppress_defaults=True),
            verbose_silent,
            build_duration_parent(suppress_defaults=True),
        ],
    )
    p_radio.add_argument("seed", help="video id or URL")
    p_radio.add_argument(
        "--per-artist",
        dest="per_artist",
        type=positive_int,
        default=3,
        help="max tracks per artist in the radio (default: 3)",
    )
    p_radio.add_argument(
        "--download",
        dest="download",
        action="store_true",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )

    # UI helpers
    p_ui = sub.add_parser(
        "ui",
        help="TUI helpers (demo, glyphs)",
    )
    ui_sub = p_ui.add_subparsers(dest="ui_command", required=True)
    p_demo = ui_sub.add_parser(
        "demo",
        help="watch the TUI with a simulated download batch",
        parents=[build_display_parent(suppress_defaults=True), verbose_silent],
    )
    p_demo.add_argument(
        "--seconds",
        type=non_negative_float,
        default=60.0,
        help="target duration of the simulated batch (default: 60.0)",
    )
    p_glyphs = ui_sub.add_parser(
        "glyphs",
        help="print the TUI glyph table and theme gradient",
        parents=[build_display_parent(suppress_defaults=True), verbose_silent],
    )

    # Maintenance commands (hidden from top-level help)
    p_migrate = sub.add_parser(
        "migrate-layout",
        help=None,
        parents=[
            build_run_control_parent(suppress_defaults=True),
            build_display_parent(suppress_defaults=True),
            verbose_silent,
        ],
    )
    p_migrate.add_argument(
        "--undo",
        action="store_true",
        default=False,
        help=argparse.SUPPRESS,
    )
    p_migrate._hidden = True
    p_migrate.set_defaults(func=_noop)

    return parser


if __name__ == "__main__":  # pragma: no cover
    build_parser().print_help()
