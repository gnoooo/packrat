"""Pure tests for TUI glyphs, bars, and display-width helpers."""

from __future__ import annotations

import io

from packrat import tui_glyphs as tg

FANCY = tg.GLYPHS["fancy"]
ASCII = tg.GLYPHS["ascii"]
WIDE = "遅 album"  # contains a double-width char


def test_every_fancy_glyph_is_single_cell():
    for key, value in FANCY.items():
        if key in ("spinner",):
            for frame in str(value):
                assert tg.display_width(frame) == 1, (key, frame)
        else:
            text = str(value)
            for ch in text:
                assert tg.display_width(ch) == 1, (key, ch)


def test_display_width_wide_and_combining():
    assert tg.display_width("遅") == 2
    assert tg.display_width(WIDE) == 8  # 2 + 1 space + 5
    assert tg.display_width("á") == 1  # combining accent
    assert tg.char_width("a") == 1


def test_truncate_width_never_splits_wide_char():
    assert tg.truncate_width("abcdef", 4) == "abcd"
    # 5 wide chars = 10 cols; cut keeps whole chars.
    out = tg.truncate_width("遅遅遅遅遅", 5)
    assert tg.display_width(out) <= 5
    assert tg.truncate_width("abc", 10) == "abc"
    assert tg.truncate_width("", 5) == ""


def test_charset_pick():
    import types

    assert tg.pick_set(force_ascii=True) is ASCII
    assert tg.pick_set(stream=types.SimpleNamespace(encoding="utf-8")) is FANCY
    assert tg.pick_set(stream=types.SimpleNamespace(encoding="latin-1")) is ASCII
    # encoding=None falls back to the locale, so no invariant there.


def _check_shape(s: str, width: int, fill: str, track: str, edge_set: set[str]):
    assert tg.display_width(s) == width
    assert set(s) <= set(fill) | set(track) | edge_set


def test_bar_exact_widths():
    for width in (10, 40, 80):
        for pct in (0, 1, 50, 99, 100):
            _check_shape(
                tg.bar(FANCY, width, pct), width, "█", "░", set("▏▎▍▌▋▊▉")
            )
            _check_shape(tg.bar(ASCII, width, pct), width, "#", "-", set())


def test_bar_monotonic_fill():
    widths = [tg.bar(FANCY, 40, p * 10).count("█") for p in range(11)]
    assert widths == sorted(widths)
    assert widths[0] == 0
    assert widths[-1] == 40


def test_bar_subcell_edge_appears():
    # 50% of 20 = 10.0 -> no edge; 25% of 20 = exactly 5 -> no edge.
    assert tg.bar(FANCY, 20, 50).count("σ") == 0
    # 1% of 80 = 0.8 -> less than one full cell -> edge char present.
    s = tg.bar(FANCY, 80, 1)
    assert set(s) & set("▏▎▍▌▋▊▉"), s
    assert tg.display_width(s) == 80


def test_thin_bar():
    for width in (10, 40):
        for pct in (0, 50, 99, 100):
            _check_shape(
                tg.thin_bar(FANCY, width, pct), width, "━", "─", {"╸"}
            )
        # Half-cell: 25% of 40 -> exactly 10 full cells, no edge.
        assert tg.thin_bar(FANCY, 40, 25).count("╸") == 0
        # 26% of 40 = 20.8 -> edge present.
        assert "╸" in tg.thin_bar(FANCY, 40, 26)
        _check_shape(tg.thin_bar(ASCII, 40, 37), 40, "=", "-", set())








def test_panels_exact_width_and_inline_title():
    top = tg.panel_top(FANCY, 40, "Now playing")
    assert top.startswith("╭─")
    assert top.endswith("╮")
    assert "Now playing" in top
    assert tg.display_width(top) == 40
    bottom = tg.panel_bottom(FANCY, 40)
    assert bottom.startswith("╰") and bottom.endswith("╯")
    assert tg.display_width(bottom) == 40
    # ASCII variant
    atop = tg.panel_top(ASCII, 30, "log")
    assert atop.startswith("+-") and atop.endswith("+")
    assert "log" in atop


def test_panel_long_title_clipped():
    top = tg.panel_top(FANCY, 12, "a" * 40)
    assert tg.display_width(top) == 12
