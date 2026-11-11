"""Focused download option/path tests."""

from __future__ import annotations

import pathlib

from ytm_offline.download import _YtdlpLogger, _build_ydl_opts, _final_filepath


def test_cookie_options(tmp_path: pathlib.Path):
    opts = _build_ydl_opts(
        tmp_path / "x.%(ext)s",
        "cookies.txt",
        "firefox:default-release",
        None,
        _YtdlpLogger(),
        None,
    )
    assert opts["cookiefile"] == "cookies.txt"
    assert opts["cookiesfrombrowser"][0] == "firefox"
    assert opts["cookiesfrombrowser"][1] == "default-release"


def test_final_filepath_uses_postprocessor_path(tmp_path: pathlib.Path):
    expected = tmp_path / "song.m4a"
    expected.write_bytes(b"x")
    assert _final_filepath({"filepath": str(expected)}, object(), tmp_path) == expected


def test_final_filepath_falls_back_to_audio_extension(tmp_path: pathlib.Path):
    expected = tmp_path / "song.m4a"
    expected.write_bytes(b"x")
    info = {"_filename": str(tmp_path / "song.webm")}
    assert _final_filepath(info, object(), tmp_path) == expected
