"""The login command: write the login file, permissions and failure paths."""

from __future__ import annotations

import pathlib

from packrat import cli


def _parse(argv):
    return cli.build_parser().parse_args(argv)


class _FakeYT:
    def get_library_playlists(self, limit=1):
        return []


def test_login_writes_file_with_private_permissions(monkeypatch, tmp_path, capsys):
    target = tmp_path / "browser.json"
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def _setup(filepath=None):
        pathlib.Path(filepath).write_text("{}", encoding="utf-8")

    monkeypatch.setattr("ytmusicapi.auth.browser.setup_browser", _setup)
    monkeypatch.setattr(cli, "load_ytmusic", lambda _path: _FakeYT())

    assert cli.cmd_login(_parse(["login", "--file", str(target)])) == 0
    assert target.exists()
    assert (target.stat().st_mode & 0o777) == 0o600
    assert "Login works." in capsys.readouterr().out


def test_login_needs_an_interactive_terminal(monkeypatch, tmp_path, capsys):
    target = tmp_path / "browser.json"
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli.cmd_login(_parse(["login", "--file", str(target)])) == 2
    assert not target.exists()
    assert "interactive terminal" in capsys.readouterr().err


def test_login_reports_bad_headers(monkeypatch, tmp_path, capsys):
    target = tmp_path / "browser.json"
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def _setup(filepath=None):
        raise ValueError("missing cookie header")

    monkeypatch.setattr("ytmusicapi.auth.browser.setup_browser", _setup)
    assert cli.cmd_login(_parse(["login", "--file", str(target)])) == 1
    assert "Login failed" in capsys.readouterr().err


def test_login_file_saved_but_check_failed(monkeypatch, tmp_path, capsys):
    target = tmp_path / "browser.json"
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        "ytmusicapi.auth.browser.setup_browser",
        lambda filepath=None: pathlib.Path(filepath).write_text("{}"),
    )

    def _bad_load(_path):
        raise RuntimeError("network down")

    monkeypatch.setattr(cli, "load_ytmusic", _bad_load)
    assert cli.cmd_login(_parse(["login", "--file", str(target)])) == 1
    assert "file was saved" in capsys.readouterr().err


def test_login_parses_real_browser_headers(monkeypatch, tmp_path, capsys):
    import json

    target = tmp_path / "browser.json"
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    lines = [
        "cookie: VISITOR_INFO1_LIVE=abc; SAPISID=def",
        "x-goog-authuser: 0",
        "authorization: SAPISIDHASH xyz",
    ]

    def _input():
        if not lines:
            raise EOFError
        return lines.pop(0)

    monkeypatch.setattr("builtins.input", _input)
    monkeypatch.setattr(cli, "load_ytmusic", lambda _path: _FakeYT())

    assert cli.cmd_login(_parse(["login", "--file", str(target)])) == 0
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["cookie"].startswith("VISITOR_INFO1_LIVE=abc")
    assert saved["x-goog-authuser"] == "0"
    assert (target.stat().st_mode & 0o777) == 0o600


def test_login_accepts_headers_alias():
    args = cli.build_parser().parse_args(["login", "--headers", "x.json"])
    assert args.file == "x.json"
