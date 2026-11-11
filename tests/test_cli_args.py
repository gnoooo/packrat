"""Tests for the shared CLI argument definitions in cli_args.py."""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

import pytest

from ytm_offline import cli_args


@pytest.fixture
def parser():
    return cli_args.build_parser()


def _parse(argv):
    return cli_args.build_parser().parse_args(argv)


# ---------------------------------------------------------------------------
# Defaults and env precedence
# ---------------------------------------------------------------------------

def test_headers_default_prefers_env(monkeypatch):
    monkeypatch.setenv("YTM_HEADERS", "/from/env.json")
    args = _parse(["auth"])
    assert args.headers == "/from/env.json"


def test_cookies_default_prefers_env(monkeypatch):
    monkeypatch.setenv("YTM_COOKIES", "/from/env/cookies.txt")
    args = _parse(["sync", "--source", "liked"])
    assert args.cookies == "/from/env/cookies.txt"


def test_cookies_from_browser_default_prefers_env(monkeypatch):
    monkeypatch.setenv("YTM_COOKIES_FROM_BROWSER", "firefox:profile")
    args = _parse(["sync", "--source", "liked"])
    assert args.cookies_from_browser == "firefox:profile"


def test_output_default_prefers_env(monkeypatch):
    monkeypatch.setenv("YTM_OUTPUT", "/env/output")
    args = _parse(["sync", "--source", "liked"])
    assert args.output == "/env/output"


def test_archive_default_prefers_env(monkeypatch):
    monkeypatch.setenv("YTM_ARCHIVE", "/env/archive")
    args = _parse(["sync", "--source", "liked"])
    assert args.archive == "/env/archive"


def test_singles_dir_default_prefers_env(monkeypatch):
    monkeypatch.setenv("YTM_SINGLES_DIR", "EPs")
    args = _parse(["sync", "--source", "liked"])
    cli_args.resolve_run_control_aliases(args)
    assert args.singles_dir == "EPs"


def test_manifest_format_default_prefers_env(monkeypatch):
    monkeypatch.setenv("YTM_MANIFEST_FORMAT", "csv")
    args = _parse(["sync", "--source", "liked"])
    cli_args.resolve_run_control_aliases(args)
    assert args.manifest_format == "json+csv"


def test_cli_overrides_env(monkeypatch):
    monkeypatch.setenv("YTM_OUTPUT", "/env/output")
    monkeypatch.setenv("YTM_ARCHIVE", "/env/archive")
    args = _parse(["sync", "--source", "liked", "--output", "/cli/output", "--archive", "/cli/archive"])
    assert args.output == "/cli/output"
    assert args.archive == "/cli/archive"


def test_cli_overrides_env_of_other_auth_kind(monkeypatch, capsys):
    """--cookies on the CLI overrides YTM_COOKIES_FROM_BROWSER without error."""
    monkeypatch.setenv("YTM_COOKIES_FROM_BROWSER", "firefox")
    argv = ["sync", "--source", "liked", "--cookies", "cookies.txt"]
    args = _parse(argv)
    cli_args.resolve_auth(args, argv=argv)
    assert args.cookies == "cookies.txt"
    assert args.cookies_from_browser is None  # CLI kind overrides the env kind


def test_env_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("YTM_OUTPUT", raising=False)
    args = _parse(["sync", "--source", "liked"])
    assert args.output == cli_args.DEFAULT_OUTPUT


# ---------------------------------------------------------------------------
# Global position independence
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "option,value,command",
    [
        (["--headers", "x.json"], "x.json", ["sync", "--source", "liked"]),
        (["--cookies", "c.txt"], "c.txt", ["sync", "--source", "liked"]),
        (["--output", "/tmp/out"], "/tmp/out", ["sync", "--source", "liked"]),
        (["--archive", "arc"], "arc", ["sync", "--source", "liked"]),
        (["--delay", "5"], 5.0, ["sync", "--source", "liked"]),
        (["--limit", "10"], 10, ["sync", "--source", "liked"]),
        (["--yes"], True, ["artist", "X"]),
        (["--dry-run"], True, ["sync", "--source", "liked"]),
        (["--theme", "matrix"], "matrix", ["sync", "--source", "liked"]),
        (["--no-color"], True, ["sync", "--source", "liked"]),
        (["--ascii"], True, ["sync", "--source", "liked"]),
        (["--select", "A[*]"], "A[*]", ["artist", "X"]),
        (["--min-duration", "60"], 60, ["artist", "X"]),
    ],
)
def test_global_option_works_before_and_after_subcommand(parser, option, value, command):
    """A flag given before the subcommand must not be overwritten by defaults."""
    args_after = parser.parse_args(command + option)
    args_before = parser.parse_args(option + command)

    for attr in ("headers", "cookies", "cookies_from_browser", "output", "archive",
                 "delay", "limit", "yes", "dry_run", "theme", "no_color", "ascii",
                 "select", "min_duration"):
        if hasattr(args_after, attr):
            assert getattr(args_after, attr) == getattr(args_before, attr), attr

    # Spot-check the changed value.
    changed_dest = option[0].lstrip("-")
    if changed_dest == "yes":
        changed_dest = "yes"
    elif changed_dest == "dry-run":
        changed_dest = "dry_run"
    elif changed_dest == "no-color":
        changed_dest = "no_color"
    elif changed_dest == "min-duration":
        changed_dest = "min_duration"
    assert hasattr(args_after, changed_dest)
    assert getattr(args_after, changed_dest) == value


# ---------------------------------------------------------------------------
# Shared option identity across commands
# ---------------------------------------------------------------------------

def _get_subparsers(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("no subparsers found")


def test_shared_options_have_same_definition_across_commands(parser):
    """Every command that accepts a shared option uses the same dest/type/help."""
    subparsers = _get_subparsers(parser)
    shared = {
        "--headers", "--cookies", "--cookies-from-browser",
        "--output", "--archive", "--delay", "--max-size", "--limit",
        "--dry-run", "--yes", "--tui", "--ascii",
        "--theme", "--no-anim", "--no-color", "--palette",
    }
    # Build the reference from the vocal parent parsers, not the silent top-level copies.
    reference: dict[str, argparse.Action] = {}
    for parent in (
        cli_args.build_auth_parent(),
        cli_args.build_run_control_parent(),
        cli_args.build_display_parent(),
    ):
        for action in parent._actions:
            for opt in action.option_strings:
                if opt in shared:
                    reference[opt] = action

    for name, sub in subparsers.choices.items():
        for action in sub._actions:
            # Skip hidden aliases that reuse a shared flag name (e.g. --limit on library).
            if action.help is argparse.SUPPRESS:
                continue
            for opt in action.option_strings:
                if opt not in shared:
                    continue
                ref = reference[opt]
                assert action.dest == ref.dest, f"{name} {opt} dest mismatch"
                assert action.type == ref.type, f"{name} {opt} type mismatch"
                # A command may legitimately override help/default for a shared option.
                if name == "download-manifest" and opt == "--output":
                    continue
                if name == "migrate-layout" and opt in ("--dry-run", "--yes"):
                    continue
                assert action.help == ref.help, f"{name} {opt} help mismatch"


# ---------------------------------------------------------------------------
# Authentication rules
# ---------------------------------------------------------------------------

def test_both_cookie_kinds_on_cli_exit_2(capsys):
    argv = ["sync", "--source", "liked", "--cookies", "c.txt", "--cookies-from-browser", "firefox"]
    args = _parse(argv)
    with pytest.raises(SystemExit):
        cli_args.resolve_auth(args, argv=argv)
    err = capsys.readouterr().err
    assert "mutually exclusive" in err


def test_browser_profile_alias_combines_and_warns(capsys):
    argv = ["sync", "--source", "liked", "--cookies-from-browser", "firefox", "--browser-profile", "dev"]
    args = _parse(argv)
    cli_args.resolve_auth(args, argv=argv)
    assert args.cookies_from_browser == "firefox:dev"
    err = capsys.readouterr().err
    assert "deprecated" in err


@pytest.mark.parametrize("spec,expected", [
    ("firefox", ("firefox", None, None, None)),
    ("firefox:default-release", ("firefox", "default-release", None, None)),
    ("firefox:/abs/path/to/profile", ("firefox", "/abs/path/to/profile", None, None)),
    ("firefox+gnomekeyring:p", ("firefox", "p", "GNOMEKEYRING", None)),
    ("firefox:p::container", ("firefox", "p", None, "container")),
])
def test_parse_cookies_from_browser(spec, expected):
    assert cli_args.parse_cookies_from_browser(spec) == expected


@pytest.mark.parametrize("spec", ["", "netscape", "firefox+bogus", "firefox::"])
def test_parse_cookies_from_browser_invalid_exits_2(spec, capsys):
    argv = ["sync", "--source", "liked", "--cookies-from-browser", spec]
    args = _parse(argv)
    with pytest.raises(SystemExit) as exc:
        cli_args.resolve_auth(args, argv=argv)
    assert exc.value.code == 2
    assert "cookies-from-browser" in capsys.readouterr().err


def test_cli_cookies_from_browser_overrides_env_cookies(monkeypatch):
    monkeypatch.setenv("YTM_COOKIES", "env-cookies.txt")
    argv = ["sync", "--source", "liked", "--cookies-from-browser", "firefox"]
    args = _parse(argv)
    cli_args.resolve_auth(args, argv=argv)
    assert args.cookies_from_browser == "firefox"
    assert args.cookies is None


def test_both_env_kinds_allowed(monkeypatch):
    monkeypatch.setenv("YTM_COOKIES", "env-cookies.txt")
    monkeypatch.setenv("YTM_COOKIES_FROM_BROWSER", "firefox")
    argv = ["sync", "--source", "liked"]
    args = _parse(argv)
    cli_args.resolve_auth(args, argv=argv)
    assert args.cookies == "env-cookies.txt"
    assert args.cookies_from_browser == "firefox"


def test_both_cli_kinds_exit_2(capsys):
    argv = ["sync", "--source", "liked", "--cookies", "c.txt",
            "--cookies-from-browser", "firefox"]
    args = _parse(argv)
    with pytest.raises(SystemExit) as exc:
        cli_args.resolve_auth(args, argv=argv)
    assert exc.value.code == 2
    assert "mutually exclusive" in capsys.readouterr().err


def test_browser_profile_without_cookies_from_browser_exits():
    argv = ["sync", "--source", "liked", "--browser-profile", "dev"]
    args = _parse(argv)
    with pytest.raises(SystemExit):
        cli_args.resolve_auth(args, argv=argv)


# ---------------------------------------------------------------------------
# Run-control aliases
# ---------------------------------------------------------------------------

def test_singles_dir_flag_alias_warns(capsys):
    args = _parse(["sync", "--source", "liked", "--singles-dir", "Singles2"])
    cli_args.resolve_run_control_aliases(args)
    assert args.singles_dir == "Singles2"
    assert "deprecated" in capsys.readouterr().err


def test_singles_dir_validation(parser):
    with pytest.raises(SystemExit):
        parser.parse_args(["sync", "--source", "liked", "--singles-dir", "a/b"])


def test_manifest_format_flag_alias_warns(capsys):
    args = _parse(["sync", "--source", "liked", "--manifest-format", "csv"])
    cli_args.resolve_run_control_aliases(args)
    assert args.manifest_format == "json+csv"
    assert "deprecated" in capsys.readouterr().err


def test_manifest_format_validation(parser):
    with pytest.raises(SystemExit):
        parser.parse_args(["sync", "--source", "liked", "--manifest-format", "xml"])


# ---------------------------------------------------------------------------
# Validators / invalid values
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "argv,expected_in_stderr",
    [
        (["sync", "--source", "liked", "--limit", "0"], "positive"),
        (["sync", "--source", "liked", "--delay", "-1"], "non-negative"),
        (["sync", "--source", "liked", "--max-size", "huge"], "size"),
        (["artist", "X", "--min-duration", "abc"], "integer"),
    ],
)
def test_invalid_values_exit_2_with_message(parser, capsys, argv, expected_in_stderr):
    with pytest.raises(SystemExit):
        parser.parse_args(argv)
    err = capsys.readouterr().err
    assert expected_in_stderr in err.lower()


# ---------------------------------------------------------------------------
# Verbosity count
# ---------------------------------------------------------------------------

def test_verbose_count(parser):
    assert parser.parse_args(["auth"]).verbose == 0
    assert parser.parse_args(["-v", "auth"]).verbose == 1
    assert parser.parse_args(["-vv", "auth"]).verbose == 2
    assert parser.parse_args(["auth", "-vvv"]).verbose == 3


# ---------------------------------------------------------------------------
# Library option rename
# ---------------------------------------------------------------------------

def test_library_top_option(parser):
    args = parser.parse_args(["library", "--top", "5"])
    assert args.top == 5


def test_library_limit_hidden_alias(parser):
    args = parser.parse_args(["library", "--limit", "5"])
    assert args.top == 5


# ---------------------------------------------------------------------------
# Discover subcommand structure
# ---------------------------------------------------------------------------

def test_discover_requires_subcommand(parser):
    with pytest.raises(SystemExit):
        parser.parse_args(["discover"])


@pytest.mark.parametrize("sub", ["sections", "taste", "queues", "albums", "radio"])
def test_discover_subcommands_parse(parser, sub):
    extra = ["seed"] if sub == "radio" else []
    args = parser.parse_args(["discover", sub] + extra)
    assert args.discover_command == sub


# ---------------------------------------------------------------------------
# Hidden legacy aliases parse
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "alias,new",
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
def test_legacy_aliases_are_rewritten(alias, new):
    from ytm_offline import cli
    assert cli._rewrite_legacy_argv([alias]) == new
    assert cli._rewrite_legacy_argv([alias, "--extra"]) == new + ["--extra"]


def test_hidden_aliases_not_in_help(parser, capsys):
    parser.print_help()
    out = capsys.readouterr().out
    for alias in ("retry", "discover-phase4", "tui-demo"):
        assert alias not in out


# ---------------------------------------------------------------------------
# resolve helpers: env-only auth is not an error
# ---------------------------------------------------------------------------

def test_env_both_cookie_kinds_no_cli_is_allowed(monkeypatch):
    monkeypatch.setenv("YTM_COOKIES", "cookies.txt")
    monkeypatch.setenv("YTM_COOKIES_FROM_BROWSER", "firefox")
    argv = ["sync", "--source", "liked"]
    args = _parse(argv)
    cli_args.resolve_auth(args, argv=argv)
    assert args.cookies == "cookies.txt"
    assert args.cookies_from_browser == "firefox"


# ---------------------------------------------------------------------------
# Snapshot: top-level help
# ---------------------------------------------------------------------------

def test_top_level_help_matches_snapshot(monkeypatch, tmp_path):
    """Top-level help is stable; update tests/snapshots/help_top_level.txt when it changes."""
    monkeypatch.setattr(cli_args, "HEADERS_CONFIG_PATH", str(tmp_path / "no.json"))
    parser = cli_args.build_parser()
    output = parser.format_help()
    snapshot = pathlib.Path(__file__).parent / "snapshots" / "help_top_level.txt"
    if os.environ.get("UPDATE_SNAPSHOTS"):
        snapshot.write_text(output, encoding="utf-8")
    assert output == snapshot.read_text(encoding="utf-8")
