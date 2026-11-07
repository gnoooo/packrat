"""Pure-function tests for TUI helpers."""

import pytest

from ytm_offline import tui
from ytm_offline.logging_config import redact


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "00:00"),
        (59, "00:59"),
        (60, "01:00"),
        (3661, "1:01:01"),
    ],
)
def test_format_duration(seconds, expected):
    assert tui.format_duration(seconds) == expected


@pytest.mark.parametrize(
    "n,expected",
    [
        (0, "0.0 B"),
        (512, "512.0 B"),
        (2048, "2.0 KB"),
        (2097152, "2.0 MB"),
        (1073741824, "1.0 GB"),
    ],
)
def test_format_bytes(n, expected):
    assert tui.format_bytes(n) == expected


def test_render_bar_unicode():
    bar = tui.render_bar(10, 50)
    assert len(bar) == 10
    assert "\u2588" in bar


def test_render_bar_ascii():
    bar = tui.render_bar(10, 50, ascii_mode=True)
    assert bar == "[#####-----]"


def test_truncate():
    assert tui.truncate("short", 10) == "short"
    assert tui.truncate("a very long string indeed", 12).endswith("\u2026")
    assert len(tui.truncate("a very long string indeed", 12)) == 12


def test_redact():
    msg = "Cookie: ABC123; Authorization: Bearer XYZ; SAPISID=secret"
    out = redact(msg)
    assert "ABC123" not in out
    assert "Bearer XYZ" not in out
    assert "secret" not in out
    assert "Cookie:" in out
    assert "Authorization:" in out
    assert "SAPISID=" in out
