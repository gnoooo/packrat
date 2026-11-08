"""Glyph/text helpers for the TUI: charsets, bars, panels, sparkline.

Display-width-aware: any layout/truncation that takes user text goes through
char_width/display_width so wide (CJK) characters never break alignment.
"""

from __future__ import annotations

import locale
import sys
import time
import unicodedata

# --- display width ----------------------------------------------------------


def char_width(ch: str) -> int:
    """0 for combining chars, 2 for wide/fullwidth, else 1."""
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def display_width(text: str) -> int:
    return sum(char_width(c) for c in text)


def truncate_width(text: str, width: int) -> str:
    """Cut to exactly `width` display columns, never splitting a wide char."""
    if width <= 0:
        return ""
    if display_width(text) <= width:
        return text
    cut = ""
    used = 0
    for ch in text:
        w = char_width(ch)
        if used + w > width:
            break
        cut += ch
        used += w
    fill = width - used
    if fill <= 0:
        return cut
    # Pad with the widest-safe filler: plain dots, never a wide char.
    return cut + "." * min(fill, 3)


def clip_width(text: str, width: int) -> str:
    """Cut to at most `width` display columns (no padding, no wide-char split)."""
    out = ""
    used = 0
    for ch in text:
        w = char_width(ch)
        if used + w > width:
            break
        out += ch
        used += w
    return out


# --- charsets ---------------------------------------------------------------

GLYPHS: dict[str, dict[str, object]] = {
    "fancy": {
        "bar_fill": "█",
        "bar_edge": "▏▎▍▌▋▊▉",  # 1/8..7/8 sub-cell steps
        "bar_track": "░",
        "thin_fill": "━",
        "thin_edge": "╸",  # half-cell step
        "thin_track": "─",
        "spinner": "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏",
        "tl": "╭", "tr": "╮", "bl": "╰", "br": "╯", "h": "─", "v": "│",
        "spark": "▁▂▃▄▅▆▇█",
        "play": "▸", "done": "✓", "fail": "✗", "note": "♪",
        "dot": "●", "dot_off": "○", "pause": "‖", "ellipsis": "...", "diamond": "◆",
    },
    "ascii": {
        "bar_fill": "#",
        "bar_edge": "",
        "bar_track": "-",
        "thin_fill": "=",
        "thin_edge": "",
        "thin_track": "-",
        "spinner": "|/-\\",
        "tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|",
        "spark": "._-~=*#",
        "play": ">", "done": "+", "fail": "x", "note": "~",
        "dot": "*", "dot_off": "o", "pause": "||", "ellipsis": "...", "diamond": "+",
    },
}

_FANCY_PROBE = "█░╭▸⠋▁✓"


def supports_fancy(stream: object | None = None) -> bool:
    """True when the output stream encoding can render the fancy glyph set."""
    if stream is None:
        stream = sys.stdout
    enc = getattr(stream, "encoding", None) or locale.getpreferredencoding(False) or ""
    if not enc:
        return False
    try:
        _FANCY_PROBE.encode(enc)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def pick_set(force_ascii: bool = False, stream: object | None = None) -> dict[str, object]:
    """Fancy glyphs for UTF-8 streams, ASCII otherwise; --ascii always wins."""
    if force_ascii or not supports_fancy(stream):
        return GLYPHS["ascii"]
    return GLYPHS["fancy"]


# --- bars -------------------------------------------------------------------


def bar(g: dict, width: int, percent: float) -> str:
    """Overall bar: fill cells + 1/8-cell leading edge + dim track == width cols."""
    if width <= 0:
        return ""
    percent = max(0.0, min(100.0, percent))
    edge_chars = str(g["bar_edge"])
    edge = ""
    if edge_chars:
        units = round(width * percent / 100 * 8)
        full, frac = divmod(units, 8)
        if frac and full < width:
            edge = edge_chars[frac - 1]
    else:
        full = round(width * percent / 100)
    full = min(full, width)
    rest = max(0, width - full - len(edge))
    return str(g["bar_fill"]) * full + edge + str(g["bar_track"]) * rest


def thin_bar(g: dict, width: int, percent: float) -> str:
    """Per-track bar: half-cell resolution leading edge."""
    if width <= 0:
        return ""
    percent = max(0.0, min(100.0, percent))
    edge_chars = str(g["thin_edge"])
    edge = ""
    if edge_chars:
        units = round(width * percent / 100 * 2)
        full, half = divmod(units, 2)
        if half and full < width:
            edge = edge_chars
    else:
        full = round(width * percent / 100)
    full = min(full, width)
    rest = max(0, width - full - len(edge))
    return str(g["thin_fill"]) * full + edge + str(g["thin_track"]) * rest


# --- spinner / sparkline ----------------------------------------------------


def spinner_frame(g: dict, now: float | None = None, fps: float = 10.0) -> str:
    frames = str(g["spinner"])
    t = time.monotonic() if now is None else now
    return frames[int(t * fps) % len(frames)]


def sparkline(g: dict, samples: list[float], width: int) -> str:
    """Rescale samples into `width` spark cells; empty/all-zero = lowest level."""
    levels = str(g["spark"])
    if width <= 0:
        return ""
    cells = samples[-width:] if samples else []
    if len(cells) < width:
        cells = [0.0] * (width - len(cells)) + cells
    peak = max(cells) if cells else 0.0
    if peak <= 0:
        return levels[0] * width
    return "".join(
        levels[min(len(levels) - 1, int(v / peak * (len(levels) - 1)))] for v in cells
    )


# --- panels -----------------------------------------------------------------


def panel_top(g: dict, width: int, title: str = "") -> str:
    """Rounded top edge with inline title: ╭─ Title ────╮"""
    if width <= 2:
        return ""
    inner = width - 2
    if title:
        # One 'h' is the leading separator right after the corner.
        t = truncate_width(f" {title} ", inner - 1)
        head = str(g["h"]) + t
    else:
        head = ""
    pad = inner - display_width(head)
    return str(g["tl"]) + head + str(g["h"]) * max(0, pad) + str(g["tr"])


def panel_bottom(g: dict, width: int) -> str:
    if width <= 1:
        return ""
    return str(g["bl"]) + str(g["h"]) * (width - 2) + str(g["br"])
