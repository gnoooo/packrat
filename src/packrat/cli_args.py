"""Shared CLI argument definitions for packrat.

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
import json
import os
import pathlib
from typing import Any

from packrat import __version__
from packrat import paths
from packrat.naming import SINGLES_DIR_DEFAULT
from packrat.sizes import parse_size

DEFAULT_OUTPUT = "~/Music/PackratMidden"
DEFAULT_ARCHIVE = "~/.config/packrat/archive"
DEFAULT_DELAY = 7.0
DEFAULT_THEME = "ocean"
DEFAULT_PALETTE = "auto"
DEFAULT_MANIFEST_FORMAT = "json"

HEADERS_CONFIG_PATH = "~/.config/packrat/browser.json"
LEGACY_HEADERS_CONFIG_PATH = "~/.config/ytmusic-composer/browser.json"
LEGACY_ARCHIVE_NAMES = (".packrat_archive", ".ytm_offline_archive")


# ---------------------------------------------------------------------------
# Env-aware defaults
# ---------------------------------------------------------------------------

def _env(name: str, legacy: str) -> str | None:
    """New PACKRAT_* name, with the old YTM_* name as a fallback."""
    return os.environ.get(name) or os.environ.get(legacy)


def _headers_default() -> str | None:
    env = _env("PACKRAT_HEADERS", "YTM_HEADERS")
    if env:
        return env
    for candidate in (HEADERS_CONFIG_PATH, LEGACY_HEADERS_CONFIG_PATH):
        p = pathlib.Path(candidate).expanduser()
        if p.exists():
            return str(p)
    return None


def _cookies_default() -> str | None:
    return _env("PACKRAT_COOKIES", "YTM_COOKIES")


def _cookies_from_browser_default() -> str | None:
    return _env("PACKRAT_COOKIES_FROM_BROWSER", "YTM_COOKIES_FROM_BROWSER")


def _output_default() -> str:
    return os.path.expanduser(
        _env("PACKRAT_OUTPUT", "YTM_OUTPUT") or DEFAULT_OUTPUT
    )


def _archive_default() -> str:
    env = _env("PACKRAT_ARCHIVE", "YTM_ARCHIVE")
    if env:
        return env
    new = paths.ARCHIVE_PATH
    candidates = [
        candidate
        for name in LEGACY_ARCHIVE_NAMES
        for candidate in (pathlib.Path(name), pathlib.Path.home() / name)
    ]
    if not new.exists():
        for old in candidates:
            if old.exists():
                return str(old.resolve())
    return str(new)


def _singles_dir_default() -> str:
    return _env("PACKRAT_SINGLES_DIR", "YTM_SINGLES_DIR") or SINGLES_DIR_DEFAULT


def _manifest_format_default() -> str:
    return _env("PACKRAT_MANIFEST_FORMAT", "YTM_MANIFEST_FORMAT") or DEFAULT_MANIFEST_FORMAT


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


def build_auth_parent(
    suppress_defaults: bool = False,
    suppress_help: bool = False,
    *,
    headers: bool = True,
    cookies: bool = True,
) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    g = p.add_argument_group("Authentication")

    if headers:
        default = argparse.SUPPRESS if suppress_defaults else _headers_default()
        g.add_argument(
            "--headers",
            default=default,
            help=_help_text(
                "your YouTube Music login file; create it with `packrat login` "
                "(env: PACKRAT_HEADERS; default: ~/.config/packrat/browser.json)",
                suppress_help,
            ),
        )

    if cookies:
        default = argparse.SUPPRESS if suppress_defaults else _cookies_default()
        g.add_argument(
            "--cookies",
            default=default,
            help=_help_text(
                "advanced: a cookies.txt file exported from your browser (env: PACKRAT_COOKIES)",
                suppress_help,
            ),
        )

        default = (
            argparse.SUPPRESS if suppress_defaults else _cookies_from_browser_default()
        )
        g.add_argument(
            "--cookies-from-browser",
            dest="cookies_from_browser",
            default=default,
            help=_help_text(
                "recommended: read cookies from a browser: firefox, chrome, edge, ...; "
                "add :profile for a specific profile "
                "(env: PACKRAT_COOKIES_FROM_BROWSER)",
                suppress_help,
            ),
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
    *,
    output: bool = True,
    archive: bool = True,
    delay: bool = True,
    max_size: bool = True,
    limit: bool = True,
    dry_run: bool = True,
    yes: bool = True,
    tui: bool = True,
    force_new_archive: bool = True,
) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    g = p.add_argument_group("Run control")

    if output:
        if output_default is _UNSET:
            default = argparse.SUPPRESS if suppress_defaults else _output_default()
        else:
            default = output_default
        help_text = output_help or f"folder where songs are saved (env: PACKRAT_OUTPUT; default: {DEFAULT_OUTPUT})"
        g.add_argument(
            "--output",
            "-o",
            dest="output",
            default=default,
            help=_help_text(help_text, suppress_help),
        )

    if archive:
        default = argparse.SUPPRESS if suppress_defaults else _archive_default()
        g.add_argument(
            "--archive",
            dest="archive",
            default=default,
            help=_help_text(
                f"download history file, so finished songs are not downloaded again "
                f"(env: PACKRAT_ARCHIVE; default: {DEFAULT_ARCHIVE})",
                suppress_help,
            ),
        )

    if delay:
        default = argparse.SUPPRESS if suppress_defaults else DEFAULT_DELAY
        g.add_argument(
            "--delay",
            dest="delay",
            type=non_negative_float,
            default=default,
            help=_help_text(
                f"seconds to wait between songs (default: {DEFAULT_DELAY})",
                suppress_help,
            ),
        )

    if max_size:
        default = argparse.SUPPRESS if suppress_defaults else None
        g.add_argument(
            "--max-size",
            dest="max_size",
            type=valid_size,
            default=default,
            help=_help_text(
                "skip songs bigger than SIZE (example: 3GB)",
                suppress_help,
            ),
        )

    if limit:
        limit_action_default = (
            argparse.SUPPRESS
            if (suppress_defaults and limit_default is None)
            else limit_default
        )
        limit_help_text = (
            limit_help
            if limit_help is not None
            else "download at most N new songs this run"
        )
        g.add_argument(
            "--limit",
            dest="limit",
            type=positive_int,
            default=limit_action_default,
            help=_help_text(limit_help_text, suppress_help),
        )

    if dry_run:
        dry_default = argparse.SUPPRESS if suppress_defaults else False
        g.add_argument(
            "--dry-run",
            "-n",
            dest="dry_run",
            action="store_true",
            default=dry_default,
            help=_help_text(
                "show what would be downloaded, without downloading anything",
                suppress_help,
            ),
        )

    if yes:
        yes_default = argparse.SUPPRESS if suppress_defaults else False
        g.add_argument(
            "--yes",
            "-y",
            dest="yes",
            action="store_true",
            default=yes_default,
            help=_help_text(
                "don't ask before downloading",
                suppress_help,
            ),
        )

    if tui:
        tui_default = argparse.SUPPRESS if suppress_defaults else False
        g.add_argument(
            "--tui",
            dest="tui",
            action="store_true",
            default=tui_default,
            help=_help_text("show the full-screen progress view", suppress_help),
        )

    if force_new_archive:
        g.add_argument(
            "--force-new-archive",
            dest="force_new_archive",
            action="store_true",
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
            "ignore songs shorter than this many seconds (default: 90)",
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
            "ignore songs longer than this many seconds (default: 540)",
            suppress_help,
        ),
    )
    return p


def build_selection_parent(
    suppress_defaults: bool = False, suppress_help: bool = False
) -> argparse.ArgumentParser:
    p = build_duration_parent(suppress_defaults, suppress_help)
    g = p._action_groups[1]

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
            "which search result to use when several artists match",
            suppress_help,
        ),
    )

    g.add_argument(
        "--exclude-singles",
        dest="no_singles",
        action="store_true",
        default=argparse.SUPPRESS,
        help=_help_text("exclude singles but keep EPs", suppress_help),
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
        choices=("ocean", "sunset", "matrix", "mono"),
        default=argparse.SUPPRESS if suppress_defaults else DEFAULT_THEME,
        help=_help_text(
            f"colors for the full-screen view (default: {DEFAULT_THEME})",
            suppress_help,
        ),
    )

    g.add_argument(
        "--no-anim",
        dest="no_anim",
        action="store_true",
        default=argparse.SUPPRESS if suppress_defaults else False,
        help=_help_text(
            "turn off animation in the full-screen view (env: PACKRAT_TUI_ANIM=0)",
            suppress_help,
        ),
    )

    g.add_argument(
        "--no-color",
        dest="no_color",
        action="store_true",
        default=argparse.SUPPRESS if suppress_defaults else False,
        help=_help_text(
            "no colors (env: NO_COLOR)",
            suppress_help,
        ),
    )

    g.add_argument(
        "--ascii",
        dest="ascii",
        action="store_true",
        default=argparse.SUPPRESS if suppress_defaults else False,
        help=_help_text("plain characters instead of symbols", suppress_help),
    )

    g.add_argument(
        "--palette",
        dest="palette",
        choices=["auto", "fixed", "basic"],
        default=argparse.SUPPRESS if suppress_defaults else DEFAULT_PALETTE,
        help=_help_text(
            f"how colors are chosen: auto, fixed, basic (default: {DEFAULT_PALETTE})",
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
            "show more detail while running (repeat for even more)",
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

    def _fill_text(self, text: str, width: int, indent: str) -> str:
        # Preserve explicit line breaks (used by the top-level examples).
        lines = []
        for line in text.splitlines():
            if line.startswith("  "):
                lines.append(indent + line)
            elif line:
                lines.append(
                    argparse.HelpFormatter._fill_text(self, line, width, indent)
                )
            else:
                lines.append("")
        return "\n".join(lines)

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


def browser_from_headers(headers_path: str | None) -> str | None:
    """Infer the browser that produced a login headers file from its UA."""
    if not headers_path:
        return None
    try:
        data = json.loads(pathlib.Path(headers_path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ua = str(data.get("user-agent", "")).lower()
    for needle, browser in (
        ("firefox/", "firefox"),
        ("edg/", "edge"),
        ("opr/", "opera"),
        ("brave", "brave"),
        ("vivaldi", "vivaldi"),
        ("chromium/", "chromium"),
        ("chrome/", "chrome"),
        ("safari/", "safari"),
    ):
        if needle in ua:
            return browser
    return None


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
    """Fill layout/format values from the environment."""
    import sys

    if not hasattr(args, "singles_dir"):
        try:
            args.singles_dir = valid_singles_dir(_singles_dir_default())
        except ArgumentTypeError as exc:
            print(f"error: PACKRAT_SINGLES_DIR: {exc}", file=sys.stderr)
            raise SystemExit(2) from None

    if not hasattr(args, "manifest_format"):
        fmt = _manifest_format_default()
        if fmt == "csv":
            print(
                "PACKRAT_MANIFEST_FORMAT=csv is deprecated; it now writes JSON + CSV sidecar",
                file=sys.stderr,
            )
            fmt = "json+csv"
        try:
            args.manifest_format = valid_manifest_format(fmt)
        except ArgumentTypeError as exc:
            print(f"error: PACKRAT_MANIFEST_FORMAT: {exc}", file=sys.stderr)
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
        prog="packrat",
        description=(
            "Download music from YouTube Music so you can listen offline. "
            "First time? Run 'packrat login', then 'auth' to check it. "
            "Then 'sync' downloads your Liked Songs."
        ),
        epilog=(
            "Common tasks:\n"
            "  packrat login\n"
            "  packrat auth\n"
            "  packrat sync --limit 20\n"
            "  packrat download-one \"https://music.youtube.com/watch?v=...\"\n"
            "  packrat artist \"Artist name\"\n"
            "  packrat discover albums\n"
            "\n"
            "Options like --output, --limit and --dry-run can go before or after "
            "the command. Run 'packrat <command> --help' for details."
        ),
        formatter_class=_HelpFormatter,
        parents=[
            build_auth_parent(),
            build_display_parent(suppress_help=True),
            build_verbose_parent(),
        ],
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    # Accept run-control / selection flags before the subcommand, silently.
    silent_run = build_run_control_parent(suppress_help=True)
    silent_sel = build_selection_parent(suppress_help=True)
    for parent in (silent_run, silent_sel):
        for action in parent._actions:
            # Avoid re-adding the title-only group stub; copy real actions only.
            if action.option_strings:
                kwargs = {k: getattr(action, k) for k in action.__dict__ if k != "container"}
                parser._add_action(action)

    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    headers_only = build_auth_parent(
        suppress_defaults=True, cookies=False
    )
    cookies_only = build_auth_parent(
        suppress_defaults=True, headers=False
    )

    # Auth-only helpers
    p_auth = sub.add_parser(
        "auth",
        help="check that your YouTube Music login works",
        description=(
            "Check that your YouTube Music login works. If you have no login "
            "file yet, run 'packrat login' first."
        ),
        parents=[headers_only, verbose_silent],
    )
    p_auth.set_defaults(func=_noop)

    p_login = sub.add_parser(
        "login",
        help="create your YouTube Music login file",
        description=(
            "Create the login file this app uses. You copy request headers from "
            "a logged-in music.youtube.com browser tab; the command explains the "
            "steps. Run 'packrat auth' afterwards to check it."
        ),
        parents=[verbose_silent],
    )
    p_login.add_argument(
        "--file",
        metavar="FILE",
        default=None,
        help=f"where to save the login file (default: {HEADERS_CONFIG_PATH})",
    )
    p_login.add_argument(
        "--headers",
        dest="file",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    p_login.set_defaults(func=_noop)

    p_logout = sub.add_parser(
        "logout",
        help="delete your saved YouTube Music login file",
        description=(
            "Delete the login file created by 'packrat login'. This removes "
            "the saved browser session headers from disk."
        ),
        parents=[verbose_silent],
    )
    p_logout.add_argument(
        "--file",
        metavar="FILE",
        default=None,
        help=f"login file to delete (default: {HEADERS_CONFIG_PATH})",
    )
    p_logout.set_defaults(func=_noop)

    p_doctor = sub.add_parser(
        "doctor",
        help="check the local setup and optionally the YouTube Music canary",
        description=(
            "Check Python/dependencies, the login file, ffmpeg/Deno, and "
            "optionally run an online YouTube Music/yt-dlp canary."
        ),
        parents=[headers_only, verbose_silent],
    )
    p_doctor.add_argument(
        "--online",
        action="store_true",
        help="also verify the login and run a small yt-dlp extraction canary",
    )
    p_doctor.set_defaults(func=_noop)

    p_upgrade = sub.add_parser(
        "upgrade",
        help="check for or install a newer release",
        description=(
            "Check GitHub Releases for a newer Packrat version and, for the "
            "standard installer layout, install the verified release wheel."
        ),
        parents=[verbose_silent],
    )
    p_upgrade.add_argument(
        "--check",
        action="store_true",
        help="only check for a newer release; do not install",
    )
    p_upgrade.add_argument(
        "--pre",
        action="store_true",
        help="include prerelease versions",
    )
    p_upgrade.add_argument(
        "--yes",
        action="store_true",
        help="upgrade without asking for confirmation",
    )
    p_upgrade.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would be upgraded without changing the install",
    )
    p_upgrade.set_defaults(func=_noop)

    p_export = sub.add_parser(
        "export-metadata",
        help="export track metadata from a saved plan",
        description=(
            "Export a normalized JSON or CSV metadata document from a saved "
            "manifest, for interoperability with other music tools."
        ),
        parents=[verbose_silent],
    )
    p_export.add_argument(
        "--manifest",
        metavar="FILE",
        default=None,
        help="saved plan (default: newest plan in the output folder)",
    )
    p_export.add_argument(
        "--file", metavar="FILE", default=None,
        help="write here instead of metadata.json/csv next to the plan",
    )
    p_export.add_argument(
        "--format", choices=("json", "csv"), default="json",
        help="metadata format (default: json)",
    )
    p_export.set_defaults(func=_noop)

    p_library = sub.add_parser(
        "library",
        help="list your playlists",
        description=(
            "List your playlists. To download one, use its ID with "
            "'sync --source playlist:<ID>'."
        ),
        parents=[headers_only, verbose_silent],
    )
    p_library.add_argument(
        "--top",
        dest="top",
        type=positive_int,
        default=25,
        help="how many playlists to list (default: 25)",
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
        help="download one song from a link or video ID",
        description=(
            "Download one song. Deno (https://deno.com) is recommended; "
            "cookies are optional for public songs and needed for private songs."
        ),
        parents=[
            cookies_only,
            headers_only,
            build_run_control_parent(
                suppress_defaults=True,
                archive=False,
                delay=False,
                max_size=False,
                limit=False,
                dry_run=False,
                yes=False,
                tui=False,
                force_new_archive=False,
            ),
            verbose_silent,
        ],
    )
    p_download_one.add_argument(
        "video_id", metavar="LINK_OR_ID",
        help="a music.youtube.com link, a youtu.be link, or an 11-character video ID",
    )
    p_download_one.set_defaults(func=_noop)

    p_sync = sub.add_parser(
        "sync",
        help="download new songs from Liked Songs or a playlist",
        description=(
            "Download songs you have not downloaded yet. Without --source it "
            "uses your Liked Songs."
        ),
        parents=[
            build_auth_parent(suppress_defaults=True),
            build_run_control_parent(suppress_defaults=True),
            build_display_parent(suppress_defaults=True),
            verbose_silent,
        ],
    )
    p_sync.add_argument(
        "--source",
        default="liked",
        help="what to download: liked (default), a playlist link, or playlist:<link or ID>",
    )
    p_sync.set_defaults(func=_noop)

    manifest_parents = [
        build_auth_parent(suppress_defaults=True, headers=False),
        build_run_control_parent(
            suppress_defaults=True,
            output_default=None,
            output_help="folder where songs are saved (default: inferred from the plan)",
        ),
        build_display_parent(suppress_defaults=True),
        verbose_silent,
    ]
    p_manifest = sub.add_parser(
        "download-manifest",
        aliases=["resume"],
        help="finish or retry downloads from a saved plan",
        description=(
            "Finish or retry downloads from a saved plan. With no --manifest, "
            "the newest plan in the output folder is used. Also available as "
            "'resume'."
        ),
        parents=manifest_parents,
    )
    p_manifest.add_argument(
        "--manifest",
        metavar="FILE",
        default=None,
        help="saved plan file (default: newest plan in the output folder)",
    )
    p_manifest.add_argument(
        "--failed-only",
        dest="failed_only",
        action="store_true",
        default=False,
        help="only retry songs that failed",
    )
    p_manifest.set_defaults(func=_noop)

    p_artist = sub.add_parser(
        "artist",
        help="download an artist's albums and singles",
        parents=dl_parents
        + [
            build_duration_parent(suppress_defaults=True),
            build_artist_selection_parent(suppress_defaults=True),
        ],
    )
    selection_group = next(
        g for g in p_artist._action_groups if g.title == "Selection"
    )
    selection_group.add_argument(
        "--select",
        dest="select",
        default=argparse.SUPPRESS,
        help="choose releases without the menu, e.g. A[1-3],S[*]",
    )
    p_artist.add_argument(
        "target",
        nargs="?",
        default=None,
        help="artist name, or a link to the artist page",
    )
    p_artist.add_argument(
        "--from-file",
        dest="from_file",
        help="text file with one artist name per line",
    )
    p_artist.add_argument(
        "--also",
        dest="also",
        action="append",
        default=[],
        help="another artist to download (repeatable)",
    )
    p_artist.set_defaults(func=_noop)

    # Discover command group
    p_discover = sub.add_parser(
        "discover",
        help="find new music to download",
        description="Find new music to download. Pick one of the choices below.",
    )
    discover_sub = p_discover.add_subparsers(
        dest="discover_command", required=True,
        metavar="<sections|taste|queues|albums|radio>",
    )

    p_sections = discover_sub.add_parser(
        "sections",
        help="popular music from Home, Charts, Moods and New Releases",
        parents=[
            build_auth_parent(suppress_defaults=True),
            build_run_control_parent(suppress_defaults=True, tui=False),
            verbose_silent,
            build_duration_parent(suppress_defaults=True),
        ],
    )
    p_sections.add_argument(
        "--sources",
        dest="source",
        default="home,charts,moods,new_releases",
        help="where to look: home, charts, moods, new_releases (comma-separated; default: all four)",
    )
    p_sections.add_argument(
        "--country",
        dest="country",
        default="FR",
        help="country for charts, e.g. FR (default: FR)",
    )
    p_sections.add_argument(
        "--quota",
        dest="quota",
        type=positive_int,
        default=10,
        help="how many songs per source (default: 10)",
    )
    p_sections.add_argument(
        "--per-container",
        dest="per_container",
        type=positive_int,
        default=5,
        help="how many songs per playlist or album (default: 5)",
    )
    p_sections.add_argument(
        "--per-artist",
        dest="per_artist",
        type=positive_int,
        default=3,
        help="how many songs per artist (default: 3)",
    )

    p_taste = discover_sub.add_parser(
        "taste",
        help="find music similar to what you already like (plan only)",
        parents=[
            build_auth_parent(suppress_defaults=True, cookies=False),
            build_run_control_parent(
                suppress_defaults=True,
                delay=False,
                limit=False,
                dry_run=False,
                yes=False,
                tui=False,
                force_new_archive=False,
            ),
            verbose_silent,
            build_duration_parent(suppress_defaults=True),
        ],
    )
    p_taste.add_argument(
        "--quota",
        dest="quota",
        type=positive_int,
        default=None,
        help="how many songs per category (overrides the defaults below)",
    )
    for flag, alias, dest, default, help_text in (
        ("--deep", "--quota-deep", "deep", 12,
         "songs from deep in an artist's catalog (default: 12)"),
        ("--adjacent", "--quota-adjacent", "adjacent", 10,
         "songs by similar artists (default: 10)"),
        ("--radio", "--quota-radio", "radio", 10,
         "songs from radio stations (default: 10)"),
        ("--external", "--quota-external", "external", 5,
         "songs from Last.fm recommendations, needs LASTFM_API_KEY (default: 5)"),
        ("--tag", "--quota-tag", "tag", 5,
         "songs found by genre tag (default: 5)"),
    ):
        p_taste.add_argument(
            flag, dest=dest, type=positive_int, default=default,
            help=help_text,
        )
    p_taste.add_argument(
        "--per-album",
        dest="per_container",
        type=positive_int,
        default=2,
        metavar="N",
        help="how many songs per album (default: 2)",
    )
    p_taste.add_argument(
        "--per-artist",
        dest="per_artist",
        type=positive_int,
        default=3,
        help="how many songs per artist (default: 3)",
    )
    p_taste.add_argument(
        "--family-cap",
        dest="family_cap",
        type=non_negative_int,
        default=0,
        metavar="N",
        help="limit songs per artist family, 0 = no limit (default: 0)",
    )
    p_taste.add_argument(
        "--skip-previous",
        dest="no_phase3",
        action="store_true",
        default=False,
        help="skip songs already in the last discover plan",
    )
    p_queues = discover_sub.add_parser(
        "queues",
        help="playlists based on YouTube Music radio queues",
        parents=dl_parents + [build_duration_parent(suppress_defaults=True)],
    )
    p_queues.add_argument(
        "--seed-count",
        dest="seed_count",
        type=positive_int,
        default=12,
        help="how many liked songs to start from (default: 12)",
    )
    p_queues.add_argument(
        "--hop-depth",
        dest="hop_depth",
        type=positive_int,
        default=1,
        help="how many rounds of related queues to follow, 0 = none (default: 1)",
    )
    p_queues.add_argument(
        "--hop-width",
        dest="hop_width",
        type=positive_int,
        default=3,
        help="how many related queues per round (default: 3)",
    )
    p_queues.add_argument(
        "--per-artist-per-queue",
        dest="per_artist_per_queue",
        type=positive_int,
        default=3,
        help="how many songs per artist in one queue (default: 3)",
    )
    p_queues.add_argument(
        "--per-queue",
        dest="per_queue_limit",
        type=positive_int,
        default=25,
        help="how many songs per queue playlist (default: 25)",
    )
    p_queues.add_argument(
        "--random-seed",
        dest="random_seed",
        type=int,
        default=None,
        help="make random choices repeatable (any number)",
    )

    p_albums = discover_sub.add_parser(
        "albums",
        help="whole albums from artists you like",
        parents=dl_parents + [build_duration_parent(suppress_defaults=True)],
    )
    p_albums.add_argument(
        "--top-artists",
        dest="artist_top_k",
        type=positive_int,
        default=15,
        help="how many of your top artists to consider (default: 15)",
    )
    p_albums.add_argument(
        "--albums-per-artist",
        dest="albums_per_artist",
        type=positive_int,
        default=2,
        help="how many albums per artist (default: 2)",
    )
    p_albums.add_argument(
        "--include-singles",
        dest="include_singles",
        action="store_true",
        default=False,
        help="include singles and EPs",
    )
    p_albums.add_argument(
        "--exclude-singles",
        dest="include_singles",
        action="store_false",
        default=argparse.SUPPRESS,
        help="only albums, not singles or EPs (default)",
    )

    p_radio = discover_sub.add_parser(
        "radio",
        help="a radio station based on one song",
        parents=[
            build_auth_parent(suppress_defaults=True),
            build_run_control_parent(suppress_defaults=True),
            build_display_parent(suppress_defaults=True),
            verbose_silent,
            build_duration_parent(suppress_defaults=True),
        ],
    )
    p_radio.add_argument("seed", help="a song link or 11-character video ID")
    p_radio.add_argument(
        "--per-artist",
        dest="per_artist",
        type=positive_int,
        default=3,
        help="how many songs per artist (default: 3)",
    )

    _apply_metavars(parser)
    _organize_help(parser)
    return parser


_METAVARS = {
    "--headers": "FILE",
    "--cookies": "FILE",
    "--cookies-from-browser": "BROWSER[:PROFILE]",
    "--output": "DIR",
    "--archive": "FILE",
    "--delay": "SECONDS",
    "--max-size": "SIZE",
    "--limit": "N",
    "--manifest": "FILE",
    "--top": "N",
    "--min-duration": "SECONDS",
    "--max-duration": "SECONDS",
    "--candidate": "N",
    "--from-file": "FILE",
    "--also": "NAME",
    "--select": "EXPR",
    "--sources": "LIST",
    "--country": "CODE",
    "--quota": "N",
    "--per-container": "N",
    "--per-artist": "N",
    "--deep": "N",
    "--adjacent": "N",
    "--radio": "N",
    "--external": "N",
    "--tag": "N",
    "--family-cap": "N",
    "--seed-count": "N",
    "--hop-depth": "N",
    "--hop-width": "N",
    "--per-artist-per-queue": "N",
    "--per-queue": "N",
    "--random-seed": "N",
    "--top-artists": "N",
    "--albums-per-artist": "N",
}


def _apply_metavars(parser: argparse.ArgumentParser) -> None:
    """Use short, readable placeholders in usage lines and help."""
    stack = [parser]
    while stack:
        node = stack.pop()
        for action in node._actions:
            for option in action.option_strings:
                if option in _METAVARS:
                    action.metavar = _METAVARS[option]
            if isinstance(action, argparse._SubParsersAction):
                stack.extend(action.choices.values())


if __name__ == "__main__":  # pragma: no cover
    build_parser().print_help()


# Option -> help section. Kept in one place so every command tells the same
# story: everyday options first, first-time setup next, advanced last.
_OPTION_GROUP = {
    "--headers": "First-time setup",
    "--cookies": "First-time setup",
    "--cookies-from-browser": "First-time setup",
    "--output": "Common options",
    "--limit": "Common options",
    "--dry-run": "Common options",
    "--yes": "Common options",
    "--source": "Common options",
    "--top": "Common options",
    "--manifest": "Common options",
    "--file": "Common options",
    "--also": "Common options",
    "--from-file": "Common options",
    "--sources": "Common options",
    "--country": "Common options",
    "--quota": "Common options",
    "--top-artists": "Common options",
    "--skip-previous": "Common options",
    "--archive": "Advanced",
    "--delay": "Advanced",
    "--max-size": "Advanced",
    "--force-new-archive": "Advanced",
    "--failed-only": "Advanced",
    "--min-duration": "Advanced",
    "--max-duration": "Advanced",
    "--candidate": "Advanced",
    "--select": "Advanced",
    "--exclude-singles": "Advanced",
    "--include-singles": "Advanced",
    "--per-container": "Advanced",
    "--per-album": "Advanced",
    "--per-artist": "Advanced",
    "--deep": "Advanced",
    "--adjacent": "Advanced",
    "--radio": "Advanced",
    "--external": "Advanced",
    "--tag": "Advanced",
    "--family-cap": "Advanced",
    "--seed-count": "Advanced",
    "--hop-depth": "Advanced",
    "--hop-width": "Advanced",
    "--per-artist-per-queue": "Advanced",
    "--per-queue": "Advanced",
    "--random-seed": "Advanced",
    "--albums-per-artist": "Advanced",
    "--verbose": "Advanced",
    "--tui": "Advanced display",
    "--theme": "Advanced display",
    "--no-anim": "Advanced display",
    "--no-color": "Advanced display",
    "--ascii": "Advanced display",
    "--palette": "Advanced display",
}

_GROUP_ORDER = (
    "positional arguments",
    "Common options",
    "First-time setup",
    "Advanced",
    "Advanced display",
    "options",
)

_GROUP_RENAMES = {
    "Authentication": "First-time setup",
    "Run control": "Common options",
    "Display": "Advanced display",
    "Selection": "Advanced",
}


def _organize_help(parser: argparse.ArgumentParser) -> None:
    """Put everyday, setup and advanced options in separate help sections."""
    stack = [parser]
    while stack:
        node = stack.pop()
        for group in node._action_groups:
            group.title = _GROUP_RENAMES.get(group.title, group.title)

        moved: dict[str, list[argparse.Action]] = {}
        for action in node._actions:
            title = next(
                (_OPTION_GROUP[opt] for opt in action.option_strings
                 if opt in _OPTION_GROUP),
                None,
            )
            if title:
                moved.setdefault(title, []).append(action)

        groups = {group.title: group for group in node._action_groups}
        for title in moved:
            if title not in groups:
                groups[title] = node.add_argument_group(title)
        for title, actions in moved.items():
            for action in actions:
                for group in node._action_groups:
                    if action in group._group_actions:
                        group._group_actions.remove(action)
                groups[title]._group_actions.append(action)

        order = {title: index for index, title in enumerate(_GROUP_ORDER)}
        node._action_groups.sort(
            key=lambda group: order.get(group.title, len(order))
        )

        for action in node._actions:
            if isinstance(action, argparse._SubParsersAction):
                stack.extend(action.choices.values())
