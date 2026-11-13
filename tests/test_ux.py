"""Beginner-facing CLI behaviour: help, defaults and plain error messages."""

from __future__ import annotations

import contextlib
import json

from packrat import cli, cli_args


def _parse(argv):
    return cli.build_parser().parse_args(argv)


def test_no_arguments_prints_help(capsys):
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "Common tasks" in out
    assert "packrat auth" in out


def test_missing_login_explains_setup(monkeypatch, capsys):
    monkeypatch.delenv("PACKRAT_HEADERS", raising=False)
    monkeypatch.setattr(cli_args, "HEADERS_CONFIG_PATH", "/definitely/missing.json")
    monkeypatch.setattr(cli_args, "LEGACY_HEADERS_CONFIG_PATH", "/definitely/missing.json")
    rc = cli.main(["sync"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "packrat login" in err
    assert "PACKRAT_HEADERS" in err


def test_discover_without_subcommand_prints_help(capsys):
    assert cli.main(["discover"]) == 0
    out = capsys.readouterr().out
    assert "sections" in out and "taste" in out and "radio" in out
    assert "discover_command" not in out


def test_sync_defaults_to_liked_songs():
    assert _parse(["sync"]).source == "liked"


def test_sync_asks_before_downloading(monkeypatch, tmp_path, capsys):
    args = _parse([
        "sync", "--headers", "/tmp/x",
        "--output", str(tmp_path), "--archive", str(tmp_path / "arc"),
    ])
    called = []
    prompts = []
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input",
                        lambda prompt: prompts.append(prompt) or "n")
    monkeypatch.setattr("packrat.cli.sync_module.run_sync",
                        lambda _args: called.append(True) or 0)
    assert cli.cmd_sync(args) == 2
    assert called == []
    assert "Liked Songs" in prompts[0]


def test_sync_yes_skips_the_prompt(monkeypatch, tmp_path):
    args = _parse([
        "sync", "--headers", "/tmp/x", "--yes",
        "--output", str(tmp_path), "--archive", str(tmp_path / "arc"),
    ])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input",
                        lambda _prompt: (_ for _ in ()).throw(AssertionError("asked")))
    monkeypatch.setattr("packrat.cli.sync_module.run_sync", lambda _args: 0)
    assert cli.cmd_sync(args) == 0


def test_download_one_accepts_a_link_without_cookies(monkeypatch, tmp_path):
    downloaded = tmp_path / "song.m4a"
    downloaded.write_bytes(b"x")
    captured = {}

    class FakeYDL:
        def __init__(self, opts):
            captured.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def extract_info(self, url, download=True):
            captured["url"] = url
            return {"filepath": str(downloaded)}

    monkeypatch.setattr(cli, "_check_deno", lambda: (True, "2.9.5"))
    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYDL)
    monkeypatch.setattr(cli, "_final_filepath", lambda *_a: downloaded)
    monkeypatch.setattr(cli, "_verify_with_ffprobe", lambda _p: {})
    monkeypatch.setattr(cli, "_verify_with_mutagen", lambda _p: {})
    monkeypatch.setattr(
        "packrat.cli.verify_audio",
        lambda *_a, **_k: type("C", (), {"ok": True, "reason": "", "duration": 200, "size": 1})(),
    )

    args = _parse([
        "download-one", "https://music.youtube.com/watch?v=abcdefghijk",
        "--output", str(tmp_path),
    ])
    assert cli.cmd_download_one(args) == 0
    assert captured["url"] == "https://music.youtube.com/watch?v=abcdefghijk"
    assert "cookiefile" not in captured
    assert "cookiesfrombrowser" not in captured


def test_sync_source_accepts_liked_case_and_links():
    from packrat.sync import normalize_source

    assert normalize_source("Liked") == "liked"
    assert normalize_source("playlist:PL123") == "playlist:PL123"
    assert normalize_source(
        "https://music.youtube.com/playlist?list=PL123"
    ) == "playlist:https://music.youtube.com/playlist?list=PL123"


def test_discover_rejects_unknown_sources(tmp_path, capsys):
    rc = cli.main([
        "discover", "sections", "--headers", "/tmp/x",
        "--output", str(tmp_path), "--sources", "home,bogus",
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "bogus" in err
    assert "home" in err


def test_download_one_warns_but_continues_without_deno(monkeypatch, tmp_path, capsys):
    downloaded = tmp_path / "song.m4a"
    downloaded.write_bytes(b"x")

    class FakeYDL:
        def __init__(self, _opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def extract_info(self, _url, download=True):
            return {"filepath": str(downloaded)}

    monkeypatch.setattr(cli, "_check_deno", lambda: (False, None))
    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYDL)
    monkeypatch.setattr(cli, "_final_filepath", lambda *_a: downloaded)
    monkeypatch.setattr(cli, "_verify_with_ffprobe", lambda _p: {})
    monkeypatch.setattr(cli, "_verify_with_mutagen", lambda _p: {})
    monkeypatch.setattr(
        "packrat.cli.verify_audio",
        lambda *_a, **_k: type("C", (), {"ok": True, "reason": "", "duration": 200, "size": 1})(),
    )

    args = _parse(["download-one", "abcdefghijk", "--output", str(tmp_path)])
    assert cli.cmd_download_one(args) == 0
    assert "Warning: Deno is not installed" in capsys.readouterr().err


def _help(path=()):
    import argparse

    node = cli.build_parser()
    for name in path:
        for action in node._actions:
            if isinstance(action, argparse._SubParsersAction):
                node = action.choices[name]
                break
    return node.format_help()


def test_help_groups_common_setup_and_advanced():
    text = _help(("sync",))
    common = text.index("Common options:")
    setup = text.index("First-time setup:")
    advanced = text.index("Advanced:")
    display = text.index("Advanced display:")
    assert common < setup < advanced < display

    common_text = text[common:setup]
    assert "--output" in common_text and "--limit" in common_text
    assert "--source" in common_text
    setup_text = text[setup:advanced]
    assert "--headers" in setup_text and "--cookies-from-browser" in setup_text
    advanced_text = text[advanced:display]
    assert "--archive" in advanced_text and "--delay" in advanced_text


def test_advanced_commands_keep_selection_options_in_advanced():
    artist = _help(("artist",))
    common = artist[artist.index("Common options:"):artist.index("First-time setup:")]
    advanced = artist[artist.index("Advanced:"):artist.index("Advanced display:")]
    assert "--also" in common and "--from-file" in common
    assert "--select" in advanced and "--candidate" in advanced

    taste = _help(("discover", "taste"))
    common = taste[taste.index("Common options:"):taste.index("First-time setup:")]
    advanced = taste[taste.index("Advanced:"):]
    assert "--skip-previous" in common
    assert "--deep" in advanced and "--family-cap" in advanced


def test_login_has_common_file_option():
    text = _help(("login",))
    assert "Common options:" in text
    assert "--file" in text


def test_resume_is_an_alias_for_download_manifest():
    args = cli.build_parser().parse_args(["resume", "--manifest", "m.json"])
    assert args.command == "resume"
    assert args.func is cli.cmd_download_manifest


def test_browser_from_headers_detects_firefox(tmp_path):
    headers = tmp_path / "headers.json"
    headers.write_text(json.dumps({
        "user-agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
    }))
    assert cli_args.browser_from_headers(str(headers)) == "firefox"


def test_browser_from_headers_detects_chrome_before_safari(tmp_path):
    headers = tmp_path / "headers.json"
    headers.write_text(json.dumps({
        "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    }))
    assert cli_args.browser_from_headers(str(headers)) == "chrome"


def test_main_auto_uses_browser_cookies(monkeypatch, tmp_path):
    headers = tmp_path / "headers.json"
    headers.write_text(json.dumps({"user-agent": "Firefox/128.0"}))
    cookie_path = str(tmp_path / "cookies.txt")
    seen = {}

    @contextlib.contextmanager
    def fake_cookies(browser):
        seen["browser"] = browser
        yield cookie_path

    def fake_sync(args):
        seen["cookies"] = args.cookies
        return 0

    monkeypatch.setattr(cli, "browser_cookie_file", fake_cookies)
    monkeypatch.setattr(cli, "cmd_sync", fake_sync)
    rc = cli.main([
        "sync", "--source", "liked", "--headers", str(headers), "--dry-run",
    ])
    assert rc == 0
    assert seen == {"browser": "firefox", "cookies": cookie_path}


def test_main_explicit_cookies_skip_browser_lookup(monkeypatch, tmp_path):
    headers = tmp_path / "headers.json"
    headers.write_text(json.dumps({"user-agent": "Firefox/128.0"}))
    seen = {"browser": False}

    @contextlib.contextmanager
    def fake_cookies(browser):
        seen["browser"] = True
        yield "unused"

    def fake_sync(args):
        seen["cookies"] = args.cookies
        return 0

    monkeypatch.setattr(cli, "browser_cookie_file", fake_cookies)
    monkeypatch.setattr(cli, "cmd_sync", fake_sync)
    rc = cli.main([
        "sync", "--source", "liked", "--headers", str(headers),
        "--cookies", "explicit.txt", "--dry-run",
    ])
    assert rc == 0
    assert seen["browser"] is False
    assert seen["cookies"] == "explicit.txt"
