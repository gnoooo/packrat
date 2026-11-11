"""Focused download option/path tests."""

from __future__ import annotations

import pathlib

from packrat.download import _YtdlpLogger, _build_ydl_opts, _final_filepath


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
    assert _final_filepath({"filepath": str(expected)}, object()) == expected


def test_final_filepath_falls_back_to_audio_extension(tmp_path: pathlib.Path):
    expected = tmp_path / "song.m4a"
    expected.write_bytes(b"x")
    info = {"_filename": str(tmp_path / "song.webm")}
    assert _final_filepath(info, object()) == expected


def test_age_restricted_error_is_actionable():
    from packrat.download import _friendly_download_error

    msg = ("ERROR: [youtube] n0LOyYtWny8: Sign in to confirm your age. "
           "This video may be inappropriate for some users.")
    out = _friendly_download_error(msg)
    assert "age-restricted" in out
    assert "--cookies-from-browser" in out
