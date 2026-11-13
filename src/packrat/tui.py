"""Curses TUI for packrat downloads — themed, animated renderer.

Render layer only: the downloader/pipeline, events/State schema and Control
semantics are untouched. Colors come from tui_theme, glyphs from tui_glyphs.
"""

from __future__ import annotations

import curses
import logging
import os
import re
import sys
import threading
import time
import traceback
from collections import deque
from typing import Any, Callable

from packrat import tui_glyphs as tg
from packrat import tui_theme as tt
from packrat.control import Control
from packrat.events import Event, State
from packrat.logging_config import (LOG_DIR, RING_CAPACITY,
                                        redact, setup_logging)
from packrat.sizes import format_size

logger = logging.getLogger(__name__)

def format_duration(seconds: float) -> str:
    """HH:MM:SS from seconds."""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_bytes(n: int | None) -> str:
    if n is None or n < 0:
        return "? B"
    return format_size(n)


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CTRL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_RUN_SPACES_RE = re.compile(r" {2,}")


def _clean(text: str) -> str:
    """Control chars → space, collapsed runs, stripped ends (curses-safe)."""
    text = _ANSI_RE.sub("", str(text))
    text = _CTRL_CHARS_RE.sub(" ", text)
    return _RUN_SPACES_RE.sub(" ", text).strip()


def truncate(text: str, width: int) -> str:
    """Sanitize, redact and cut to a display width, ellipsis when needed."""
    text = redact(_clean(str(text)))
    if width <= 3:
        return text[: width]
    if tg.display_width(text) <= width:
        return text
    return tg.clip_width(text, width - 1) + "…"


def _fit(text: str, width: int) -> str:
    """Like truncate, but keeps intentional spaces between UI segments."""
    text = redact(_CTRL_CHARS_RE.sub(" ", str(text)))
    if width <= 0:
        return ""
    if tg.display_width(text) <= width:
        return text
    if width <= 3:
        return tg.clip_width(text, width)
    return tg.clip_width(text, width - 1) + "…"


# --- visual options (CLI-wide flag set once in main()) ----------------------

_visual_state = {"theme": "ocean", "no_color": False}
MAX_CONTENT_WIDTH = 200
_palette_dirty = False
_custom_slots: set[int] = set()


_no_anim_default = False


def set_no_anim_default(value: bool) -> None:
    global _no_anim_default
    _no_anim_default = bool(value)


def set_visual_options(theme: str | None = None, no_color: bool | None = None,
                       palette: str | None = None) -> None:
    """Single place the CLI passes its visual flags; run_tui call sites unchanged."""
    if theme is not None and theme in tt.THEMES:
        _visual_state["theme"] = theme
    if no_color is not None:
        _visual_state["no_color"] = bool(no_color)
    if palette is not None:
        _visual_state["palette"] = palette


def anim_mode(status: str) -> str:
    if status.startswith("WAITING"):
        return "waiting"
    if status in ("PAUSED", "STOPPING", "DONE"):
        return status.lower()
    if status in ("STOPPED", "ABORTED"):
        return "aborted"
    return "running"


# --- drawing primitives -----------------------------------------------------


def _safe_addstr(win, y: int, x: int, text: str, attr: int = 0) -> None:
    try:
        h, w = win.getmaxyx()
        if y < 0 or y >= h or x >= w:
            return
        win.addstr(y, x, tg.clip_width(text, w - x), attr)
    except curses.error:
        pass


class Visual:
    """One theme+tier snapshot; built at start and on every theme switch."""

    def __init__(self, glyphs, pal, colors, attr, no_anim: bool, tier: str,
                 bar_bg: dict | None = None, bar_edge: dict | None = None,
                 track_attr: int | None = None, chrome: int = 0,
                 pill: int = 0, status_pills: dict | None = None,
                 tip_fill: int = 0, tip_edge: int = 0) -> None:
        self.g = glyphs
        self.pal = pal
        self.colors = colors
        self.attr = attr
        self.no_anim = no_anim
        self.tier = tier
        # Space-bar pair tables (pos -> attr); empty for basic/none tiers
        # or when the pair budget ran out (bars then use the fill chars).
        self.bar_bg = bar_bg or {}
        self.bar_edge = bar_edge or {}
        self.track_attr = track_attr
        self.tip_fill = tip_fill
        self.tip_edge = tip_edge
        # Full-width chrome bands and inverted key/status pills. 0 means
        # "fall back to reverse video" on colorless terminals.
        self.chrome = chrome
        self.pill = pill
        self.status_pills = status_pills or {}

    def wave_attr(self, x: float, phase: float) -> int:
        idx, bold = self.pal.attr_for_intensity(tt.wave_intensity(x, phase))
        return self.attr(idx, bold) if idx >= 0 else 0

    def breathe_attr(self, breath: float) -> int:
        idx, bold = self.pal.attr_for_intensity(breath)
        return self.attr(idx, bold) if idx >= 0 else 0


def _make_bar_pairs(cur, grad_values: dict[int, int], track_value: int,
                    first_pair: int, tip_value: int) -> tuple[
                        dict[int, int], dict[int, int], int | None, int, int]:
    """Gradient fill (space background) + foreground ``▂`` track."""
    try:
        budget = min(cur.COLOR_PAIRS, 256)
    except Exception:
        return {}, {}, None, 0, 0
    steps = len(grad_values)
    if steps == 0 or first_pair + 2 * steps + 3 > budget:
        return {}, {}, None, 0, 0
    bg: dict[int, int] = {}
    edge: dict[int, int] = {}
    n = first_pair
    try:
        for pos, grad in grad_values.items():
            cur.init_pair(n, grad, -1)
            bg[pos] = cur.color_pair(n)
            n += 1
            cur.init_pair(n, grad, track_value)
            edge[pos] = cur.color_pair(n)
            n += 1
        cur.init_pair(n, track_value, track_value)
        track_attr = cur.color_pair(n)
        n += 1
        cur.init_pair(n, tip_value, track_value)
        tip_edge = cur.color_pair(n)
        n += 1
        cur.init_pair(n, tip_value, -1)
        tip_fill = cur.color_pair(n)
    except cur.error:
        return {}, {}, None, 0, 0
    return bg, edge, track_attr, tip_fill, tip_edge


def _set_custom_color(cur, slot: int, rgb: tuple[int, int, int]) -> None:
    """Set one palette slot; tmux always uses OSC 4 for pane palettes."""
    global _custom_slots
    _custom_slots.add(slot)
    if os.environ.get("TMUX"):
        sys.stdout.write(
            f"\x1b]4;{slot};rgb:{rgb[0]:02x}/{rgb[1]:02x}/{rgb[2]:02x}\x07"
        )
        sys.stdout.flush()
        return
    try:
        cur.init_color(slot, rgb[0] * 1000 // 255,
                       rgb[1] * 1000 // 255, rgb[2] * 1000 // 255)
    except cur.error:
        pass


def _can_custom_palette(cur) -> bool:
    try:
        if cur.can_change_color():
            return True
    except cur.error:
        pass
    return bool(os.environ.get("TMUX"))


def _contrast_fg(rgb: tuple[int, int, int]) -> int:
    r, g, b = rgb
    luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
    return 16 if luminance > 0.55 else 231


def _build_custom_visual(_cur, theme, pal, glyphs, no_anim: bool) -> Visual | None:
    """Exact-RGB palette for a genuinely smooth progress bar.

    Reserved palette slots are redefined with init_color: 128 gradient steps,
    then base/highlight/status colors and a dark track.  Pairs are allocated
    once.  Returns None if the terminal refuses the palette or the pair budget.
    """
    global _palette_dirty
    try:
        _cur.start_color()
        _cur.use_default_colors()
    except _cur.error:
        pass
    grad = pal.gradient_rgb_list
    steps = len(grad)
    static_rgb = {
        "base": tt.parse_hex(theme.base),
        "highlight": tt.parse_hex(theme.highlight),
        **{k: tt.parse_hex(getattr(theme, k)) for k in tt.COLOR_KEYS},
    }
    track_rgb = tt.blend(tt.parse_hex(theme.base), (0, 0, 0), 0.55)
    first_slot = 32
    static_slots: dict[str, int] = {}
    slot = first_slot + steps
    for key in static_rgb:
        static_slots[key] = slot
        slot += 1
    track_slot = slot
    if track_slot > 255:
        return None
    try:
        if min(_cur.COLOR_PAIRS, 256) < 1 + 2 * steps + len(static_rgb) + 1 + 8:
            return None
        _palette_dirty = True
        for i, rgb in enumerate(grad):
            _set_custom_color(_cur, first_slot + i, rgb)
        for key, rgb in static_rgb.items():
            _set_custom_color(_cur, static_slots[key], rgb)
        _set_custom_color(_cur, track_slot, track_rgb)
        n = 1

        def pair(fg: int, bg: int = -1) -> int:
            nonlocal n
            _cur.init_pair(n, fg, bg)
            out = _cur.color_pair(n)
            n += 1
            return out

        bar_bg = {pos: pair(first_slot + pos)
                  for pos in range(steps)}
        bar_edge = {pos: pair(first_slot + pos, track_slot)
                    for pos in range(steps)}
        track_attr = pair(track_slot, track_slot)
        tip_fill = pair(static_slots["highlight"])
        tip_edge = pair(static_slots["highlight"], track_slot)
        colors = {k: pair(static_slots[k]) for k in tt.COLOR_KEYS}
        chrome = pair(static_slots["highlight"], static_slots["base"])
        pill = pair(static_slots["base"], static_slots["highlight"])
        status_pills = {
            k: pair(_contrast_fg(static_rgb[k]), static_slots[k])
            for k in tt.COLOR_KEYS
        }
    except _cur.error:
        return None
    return Visual(glyphs, pal, colors, lambda idx, bold=False: 0,
                  no_anim, tt.TIER_CUSTOM, bar_bg=bar_bg, bar_edge=bar_edge,
                  track_attr=track_attr, chrome=chrome, pill=pill,
                  status_pills=status_pills, tip_fill=tip_fill,
                  tip_edge=tip_edge)


def _build_visual(theme_name: str, ascii_mode: bool, no_anim: bool) -> Visual:
    import curses as _cur

    glyphs = tg.pick_set(force_ascii=ascii_mode)
    has_colors = _cur.has_colors()
    no_color = tt.no_color_requested() or _visual_state.get("no_color")
    try:
        can_change = bool(_cur.COLORS >= 256 and _can_custom_palette(_cur))
    except _cur.error:
        can_change = False
    tier = tt.select_tier(
        has_colors,
        _cur.COLORS if has_colors else 0,
        no_color,
        palette_mode=_visual_state.get("palette", "auto"),
        can_change_color=can_change,
    )
    theme = tt.THEMES.get(theme_name, tt.THEMES["ocean"])
    method = ("osc4" if os.environ.get("TMUX")
              else "init_color" if can_change else "fixed")
    logger.info(
        "TUI palette tier=%s method=%s colors=%s pairs=%s can_change=%s tmux=%s",
        tier,
        method,
        _cur.COLORS if has_colors else 0,
        getattr(_cur, "COLOR_PAIRS", 0),
        can_change,
        bool(os.environ.get("TMUX")),
    )
    pal = tt.Palette(theme, tier)
    if tier == tt.TIER_CUSTOM:
        custom = _build_custom_visual(_cur, theme, pal, glyphs, no_anim)
        if custom is not None:
            return custom
        logger.warning("TUI custom palette unavailable; falling back to fixed")
        tier = tt.TIER_FIXED
        pal = tt.Palette(theme, tier)
    colors: dict[str, int] = {k: 0 for k in tt.COLOR_KEYS}

    def plain_attr(idx: int, bold: bool = False) -> int:
        return _cur.A_BOLD if bold else 0

    attr: Callable[[int, bool], int] = plain_attr
    bar_bg: dict[int, int] = {}
    bar_edge: dict[int, int] = {}
    track_attr: int | None = None
    tip_fill = tip_edge = 0
    chrome = pill = 0
    status_pills: dict[str, int] = {}
    if tier != tt.TIER_NONE:
        try:
            _cur.start_color()
            pairs = tt.activate(pal)

            def themed_attr(idx: int, bold: bool = False,
                            _pairs: dict = pairs) -> int:
                a = _cur.color_pair(_pairs.get(idx, 0))
                return a | _cur.A_BOLD if bold else a

            colors = {
                k: (_cur.color_pair(pairs[idx]) if idx >= 0 else 0)
                for k, idx in pal.static.items()
            }
            grad_idx = {
                pos: pal.gradient_exact[pos]
                for pos in range(len(pal.gradient_exact))
            }
            track = pal.track_idx
            first_free = 1 + len(pairs)
            chrome = pill = 0
            status_pills: dict[str, int] = {}
            try:
                base_idx = tt.nearest_index(tt.parse_hex(theme.base))
                high_idx = tt.nearest_index(tt.parse_hex(theme.highlight))
                _cur.init_pair(first_free, high_idx, base_idx)
                chrome = _cur.color_pair(first_free)
                first_free += 1
                _cur.init_pair(first_free, base_idx, high_idx)
                pill = _cur.color_pair(first_free)
                first_free += 1
                for key in tt.COLOR_KEYS:
                    idx = pal.static.get(key, -1)
                    if idx >= 0:
                        fg_idx = _contrast_fg(tt.parse_hex(getattr(theme, key)))
                        _cur.init_pair(first_free, fg_idx, idx)
                        status_pills[key] = _cur.color_pair(first_free)
                        first_free += 1
            except _cur.error:
                chrome = pill = 0
                status_pills = {}
            tip_value = tt.nearest_index(tt.parse_hex(theme.highlight))
            if track is not None and track >= 0:
                bar_bg, bar_edge, track_attr, tip_fill, tip_edge = _make_bar_pairs(
                    _cur, grad_idx, track, first_free, tip_value,
                )
            attr = themed_attr
        except (_cur.error, ValueError):
            attr = plain_attr
            colors = {k: 0 for k in tt.COLOR_KEYS}
            bar_bg, bar_edge, track_attr = {}, {}, None
            tip_fill = tip_edge = 0
    return Visual(glyphs, pal, colors, attr, no_anim, tier,
                  bar_bg=bar_bg, bar_edge=bar_edge, track_attr=track_attr,
                  chrome=chrome, pill=pill, status_pills=status_pills,
                  tip_fill=tip_fill, tip_edge=tip_edge)


def _cell_attr(vis: Visual, i: int, mode: str, phase: float, breath: float,
               sweep: float | None, filled: bool, width: int) -> int:
    if not filled:
        return vis.colors.get("dim", 0)
    if mode == "running":
        return vis.wave_attr(i, phase)
    if mode == "waiting":
        return vis.breathe_attr(breath)
    if mode == "paused":
        return vis.colors.get("dim", 0)
    if mode == "stopping":
        return vis.colors.get("warning", 0)
    if mode == "done":
        if sweep is not None and sweep < 1.0 and abs(i - sweep * width) < 1:
            return vis.attr(231, True)
        return vis.colors.get("success", 0)
    return vis.colors.get("error", 0)


_BAYER_16 = (0, 8, 2, 10, 12, 4, 14, 6,
             3, 11, 1, 9, 15, 7, 13, 5)


def _bar_position(vis: Visual, i: int, width: int, mode: str,
                  now: float) -> int:
    """Gradient position for tile i, using the FULL bar width.

    Both fill and track share this mapping, so the colour at a given column
    does not jump when the percentage grows.  Fixed tiers dither between
    adjacent xterm colours; custom tiers use the exact HSL slots.
    """
    steps = len(vis.bar_bg)
    if steps <= 1:
        return 0
    t = 0.0 if width <= 1 else min(1.0, max(0.0, i / (width - 1)))
    pos = t * (steps - 1)
    if vis.tier == tt.TIER_CUSTOM:
        return round(pos)
    base = int(pos)
    frac = pos - base
    threshold = (_BAYER_16[i % 16] + 0.5) / 16.0
    if base < steps - 1 and frac > threshold:
        base += 1
    return base


def _gradient_attr(vis: Visual, t: float, bold: bool = False) -> int:
    """A single smooth xterm-256 color from the theme gradient."""
    steps = vis.pal.gradient_exact
    if not steps:
        return 0
    idx = steps[min(len(steps) - 1, max(0, round(t * (len(steps) - 1))))]
    return vis.attr(idx, bold)


def _draw_space_bar(win, y: int, prefix: str, body: str, suffix: str,
                    vis: Visual, mode: str, now: float,
                    base_attr: int = 0) -> None:
    """One bar renderer for both OVERALL and NOW PLAYING.

    Filled tiles are full blocks whose foreground and background are the
    same gradient colour.  Track tiles are foreground ``▂`` on the dark
    track background.
    """
    g = vis.g
    track_c = str(g["bar_track"])
    fill_glyph = str(g.get("bar_fill", "█"))
    track_glyph = str(g.get("bar_track_line", "▂"))
    filled = sum(1 for ch in body if ch != track_c)
    x = tg.display_width(prefix)
    _safe_addstr(win, y, 0, prefix, vis.colors.get("dim", 0))
    runs: list[tuple[int, str]] = []
    for i, ch in enumerate(body):
        if ch == track_c:
            pos = _bar_position(vis, i, len(body), mode, now)
            attr, text = vis.bar_edge.get(pos, vis.track_attr or 0), track_glyph
        else:
            pos = _bar_position(vis, i, len(body), mode, now)
            attr, text = vis.bar_bg.get(pos, 0), fill_glyph
        if runs and runs[-1][0] == attr:
            runs[-1] = (attr, runs[-1][1] + text)
        else:
            runs.append((attr, text))
    for attr, text in runs:
        _safe_addstr(win, y, x, text, attr)
        x += tg.display_width(text)
    _safe_addstr(win, y, x, suffix, base_attr)


def _draw_themed_line(win, y: int, prefix: str, body: str, suffix: str, vis: Visual,
                      mode: str, phase: float, breath: float, sweep: float | None,
                      base_attr: int = 0, thin: bool = False) -> None:
    """prefix + per-cell colored body + suffix; bar cells get the gradient."""
    if vis.tier == tt.TIER_NONE or not body:
        _safe_addstr(win, y, 0, prefix + body + suffix, base_attr)
        return
    if (not thin and vis.bar_bg and mode in ("running", "waiting")
            and vis.track_attr is not None and vis.g.get("bar_edge")):
        # Bar animation is driven by the injected frame phase, never the
        # wall clock, so renders (and tests) are deterministic per frame.
        _draw_space_bar(win, y, prefix, body, suffix, vis, mode,
                        phase, base_attr)
        return
    _safe_addstr(win, y, 0, prefix, vis.colors.get("dim", 0))
    fill = str(vis.g["thin_fill"] if thin else vis.g["bar_fill"])
    edge = {str(vis.g["thin_edge"])} if thin and vis.g["thin_edge"] else set(str(vis.g["bar_edge"]))
    fill_set = {fill} | edge
    filled = sum(1 for ch in body if ch in fill_set)
    x = tg.display_width(prefix)
    for i, ch in enumerate(body):
        if ch in fill_set:
            if thin or (mode in ("running", "waiting")
                        and vis.pal.gradient_exact):
                t = 0.0 if filled <= 1 else i / (filled - 1)
                attr = _gradient_attr(vis, min(1.0, t),
                                      bold=(ch in edge))
            else:
                attr = _cell_attr(vis, i, mode, phase, breath, sweep,
                                  True, len(body))
        else:
            attr = vis.colors.get("dim", 0)
        _safe_addstr(win, y, x, ch, attr)
        x += tg.char_width(ch)
    _safe_addstr(win, y, x, suffix, base_attr)


# --- panels -----------------------------------------------------------------


def _pretty_source(source: str) -> str:
    """Turn internal source ids into the same @-separated display form."""
    text = _clean(str(source or ""))
    if ":" in text:
        key, value = text.split(":", 1)
        return f"{key}@{value}"
    return text


def _ascii_visual(vis: Visual | None) -> bool:
    return bool(vis) and str(vis.g.get("bar_fill", "")) == "#"


def _sep(vis: Visual) -> str:
    return "  |  " if _ascii_visual(vis) else "  ·  "


def _ellipsis(vis: Visual) -> str:
    return "..." if _ascii_visual(vis) else "…"


def _bar_prefix(vis: Visual) -> str:
    return "| " if _ascii_visual(vis) else "│ "


def _arrow(vis: Visual) -> str:
    return ">" if _ascii_visual(vis) else "›"


def _segments_width(segments: list[tuple[str, int]]) -> int:
    return sum(tg.display_width(text) for text, _attr in segments)


def _draw_panel_top(win, title: str, vis: Visual, accent: str = "info") -> None:
    """Rounded top edge: dim border, accent uppercase title."""
    h, w = win.getmaxyx()
    if h < 1 or w < 4:
        return
    label = f" {title.upper()} "
    raw = tg.panel_top(vis.g, w, title.upper())
    _safe_addstr(win, 0, 0, raw, vis.colors.get("dim", 0))
    start = raw.find(label)
    if start >= 0:
        _safe_addstr(win, 0, start, label,
                     vis.colors.get(accent, 0) | curses.A_BOLD)


def _draw_panel_bottom(win, vis: Visual) -> None:
    h, w = win.getmaxyx()
    if h < 1 or w < 2:
        return
    _safe_addstr(win, h - 1, 0, tg.panel_bottom(vis.g, w),
                 vis.colors.get("dim", 0))


def _panel_segments(win, y: int, segments: list[tuple[str, int]],
                    vis: Visual) -> None:
    """'│' + colored runs + padding + '│', clipped to the inner width."""
    h, w = win.getmaxyx()
    if h < 3 or w < 4 or y < 1 or y > h - 2:
        return
    inner = w - 4
    _safe_addstr(win, y, 0, vis.g.get("v", "|"), vis.colors.get("dim", 0))
    _safe_addstr(win, y, 1, " ", 0)
    x = 2
    used = 0
    for text, attr in segments:
        if used >= inner:
            break
        text = _fit(text, inner - used)
        if not text:
            continue
        _safe_addstr(win, y, x, text, attr)
        width = tg.display_width(text)
        x += width
        used += width
    _safe_addstr(win, y, x, " " * max(0, inner - used), 0)
    _safe_addstr(win, y, w - 1, vis.g.get("v", "|"),
                 vis.colors.get("dim", 0))


def _panel_row(win, y: int, text: str, vis: Visual, attr: int = 0,
               bold: bool = False) -> None:
    """'│' + padded content + '│' padded to the exact inner width."""
    h, w = win.getmaxyx()
    if h < 3 or w < 4 or y < 1 or y > h - 2:
        return
    if bold:
        attr = attr | curses.A_BOLD
    body = truncate(text, max(0, w - 4))
    pad = " " * max(0, w - 2 - tg.display_width(body) - 1)
    _safe_addstr(win, y, 0, vis.g.get("v", "|"), vis.colors.get("dim", 0))
    _safe_addstr(win, y, 1, " " + body + pad, attr)
    _safe_addstr(win, y, w - 1, vis.g.get("v", "|"), vis.colors.get("dim", 0))


def _right_border(win, y: int, vis: Visual) -> None:
    """Right '│' for rows that themed lines draw themselves."""
    h, w = win.getmaxyx()
    if h < 1 or w < 2 or y < 0 or y >= h:
        return
    _safe_addstr(win, y, w - 1, vis.g.get("v", "|"), vis.colors.get("dim", 0))


def _throughput_graph(frame: dict, avail: int) -> str:
    """Braille deltas of the last minute, or '' when there is no room."""
    if avail < 8:
        return ""
    graph_w = min(40, avail)
    hist = frame.get("history") or ()
    if not hist or max((v for _, v in hist), default=0) <= 0:
        return ""
    now = time.monotonic()
    # ~2 samples/cell over the last minute, per-second granularity
    per_bucket = max(1, int(60.0 / max(1, graph_w * 2)))
    buckets: list[float] = []
    for i in range(graph_w * 2):
        start = now - (graph_w * 2 - i) * per_bucket
        vals = [v for ts, v in hist if start < ts <= start + per_bucket]
        buckets.append(vals[-1] if vals else 0.0)
    deltas = [max(0.0, buckets[i] - buckets[i - 1]) if i else 0.0
              for i in range(len(buckets))]
    if not any(d > 0 for d in deltas):
        return ""
    return tg.braille_graph(deltas, graph_w)


def _draw_header(win, snap: State, vis: Visual) -> None:
    """Foreground-only header: no chrome/background band."""
    h, w = win.getmaxyx()
    g = vis.g
    colors = vis.colors
    status = snap.status
    if status == "DONE":
        key = "success" if snap.failed == 0 else "error"
    elif status in ("STOPPED", "ABORTED"):
        key = "error"
    elif status == "STOPPING":
        key = "stopping"
    elif status.startswith("WAITING"):
        key = "warning"
    else:
        key = "info"

    note = f"{g.get('note', '~')} "
    _safe_addstr(win, 0, 0, note, colors.get("info", 0) | curses.A_BOLD)
    x = tg.display_width(note)
    left = f"packrat  {_arrow(vis)}  {_pretty_source(snap.mode)} "
    right = (f"{g.get('dot', '*')} {status}  "
             f"{g.get('clock', '~')} {format_duration(snap.elapsed_seconds)} ")
    right_w = tg.display_width(right)
    left = _fit(left, max(0, w - right_w - x - 1))
    _safe_addstr(win, 0, x, left, colors.get("info", 0) | curses.A_BOLD)
    rx = max(x + tg.display_width(left), w - right_w)
    if rx > x + tg.display_width(left):
        _safe_addstr(win, 0, x + tg.display_width(left),
                     " " * (rx - x - tg.display_width(left)), 0)
    _safe_addstr(win, 0, rx, right, colors.get(key, 0) | curses.A_BOLD)


def _draw_overall(win, snap: State, vis: Visual, frame: dict) -> None:
    h, w = win.getmaxyx()
    _draw_panel_top(win, "overall", vis)
    if snap.total <= 0:
        _panel_segments(win, 1, [
            (f"resolving tracks{_ellipsis(vis)}", vis.colors.get("info", 0)),
        ], vis)
        _draw_panel_bottom(win, vis)
        return
    pct = min(100.0, max(0.0, snap.done / snap.total * 100))
    eta = snap.eta_seconds()
    eta_s = "--:--" if eta is None else format_duration(eta)
    suffix = f" {pct:5.1f}% "
    prefix = _bar_prefix(vis)
    bar_w = max(10, w - 2 - tg.display_width(prefix) - tg.display_width(suffix) - 1)
    suffix = tg.clip_width(suffix, max(0, w - 1 - 2 - tg.display_width(prefix) - bar_w))
    body = tg.bar(vis.g, bar_w, pct)
    _draw_themed_line(win, 1, prefix, body, suffix, vis, frame["mode"],
                      frame["phase"], frame["breath"], frame.get("sweep"), 0)
    _right_border(win, 1, vis)

    dim = vis.colors.get("dim", 0)
    info = vis.colors.get("info", 0)
    segments: list[tuple[str, int]] = [
        (f"{snap.done}/{snap.total} tracks", info | curses.A_BOLD),
        (_sep(vis), dim),
        (f"{format_bytes(snap.total_bytes_downloaded)} / "
         f"{format_bytes(snap.total_est_bytes)}", dim),
        (_sep(vis), dim),
        (f"ETA {eta_s}", info),
    ]
    if snap.max_size_bytes:
        segments += [(_sep(vis), dim),
                     (f"max {format_bytes(snap.max_size_bytes)}", dim)]
    rate = tg.rates_60s(frame.get("history") or (), time.monotonic())
    if rate:
        segments += [(_sep(vis), dim), (f"{format_bytes(rate)}/s", dim)]
    used = _segments_width(segments)
    avail = w - 4 - used - 2
    graph = "" if _ascii_visual(vis) else _throughput_graph(frame, avail)
    if graph:
        segments += [("  ", dim), (graph, dim)]
    _panel_segments(win, 2, segments, vis)
    _draw_panel_bottom(win, vis)


def _empty_border_rows(win, h: int, written: set[int], vis: Visual) -> None:
    """Panels that may be content-empty still get complete borders."""
    for y in range(1, h - 1):
        if y not in written:
            _panel_row(win, y, "", vis)


def _draw_current(win, snap: State, vis: Visual, frame: dict) -> None:
    h, w = win.getmaxyx()
    cur = snap.current
    g = vis.g
    colors = vis.colors
    _draw_panel_top(win, "now playing", vis)
    written: set[int] = set()
    if cur.video_id:
        title = _clean(cur.title)
        artist = _clean(cur.artist) or "?"
        album = _clean(cur.album)
        _panel_segments(win, 1, [
            (f"{g.get('play', '>')} {title}", colors.get("info", 0) | curses.A_BOLD),
        ], vis)
        meta: list[tuple[str, int]] = [
            (f"artist@{artist}", colors.get("info", 0) | curses.A_BOLD),
        ]
        if album:
            meta.append((f"#{album}", colors.get("dim", 0)))
        _panel_segments(win, 2, meta, vis)
        stage = _clean(cur.stage or "working")
        source = _pretty_source(cur.source)
        line3: list[tuple[str, int]] = [
            (f"{g.get('dot', '*')} {stage}", colors.get("success", 0)),
        ]
        # Do not print artist@X twice: the metadata line already says it.
        if source and source != f"artist@{artist}":
            line3 += [("   source ", colors.get("dim", 0)),
                      (source, colors.get("dim", 0))]
        _panel_segments(win, 3, line3, vis)

        pct = max(0.0, min(100.0, cur.percent))
        prefix = _bar_prefix(vis)
        suffix = f" {pct:5.1f}% "
        bar_w = max(8, w - 2 - tg.display_width(prefix)
                    - tg.display_width(suffix) - 1)
        suffix = _fit(suffix, max(0, w - 1 - 2
                                  - tg.display_width(prefix) - bar_w))
        _draw_themed_line(win, 4, prefix, tg.bar(g, bar_w, pct), suffix,
                          vis, frame["mode"], frame["phase"],
                          frame["breath"], frame.get("sweep"), 0)
        _right_border(win, 4, vis)

        eta = "--:--" if cur.eta_seconds is None else format_duration(cur.eta_seconds)
        stats: list[tuple[str, int]] = [
            (f"{format_bytes(cur.downloaded_bytes)} / "
             f"{format_bytes(cur.total_bytes)}", colors.get("info", 0)),
        ]
        if cur.speed:
            stats += [(_sep(vis), colors.get("dim", 0)),
                      (cur.speed, colors.get("dim", 0))]
        stats += [(_sep(vis), colors.get("dim", 0)),
                  (f"ETA {eta}", colors.get("info", 0))]
        _panel_segments(win, 5, stats, vis)
        written = {1, 2, 3, 4, 5}
    elif snap.status.startswith("WAITING"):
        _panel_segments(win, 1, [
            (f"{g.get('done', '+')} finished: {snap.last_finished}",
             colors.get("success", 0)),
        ], vis)
        if snap.next_title:
            _panel_segments(win, 2, [
                (f"{g.get('play', '>')} next: {snap.next_title}",
                 colors.get("info", 0) | curses.A_BOLD),
            ], vis)
            written = {1, 2}
        else:
            written = {1}
    elif snap.status == "DONE":
        if snap.failed:
            _panel_segments(win, 1, [
                (f"{g.get('fail', 'x')} finished with {snap.failed} failed",
                 colors.get("error", 0) | curses.A_BOLD),
                (_sep(vis), colors.get("dim", 0)),
                (f"{snap.downloaded} downloaded", colors.get("info", 0)),
            ], vis)
        else:
            _panel_segments(win, 1, [
                (f"{g.get('done', '+')} all done{_sep(vis)}{snap.downloaded} downloaded",
                 colors.get("success", 0) | curses.A_BOLD),
            ], vis)
        written = {1}
    elif snap.status in ("STOPPED", "ABORTED"):
        reason = snap._stop_reason or "stopped"
        if reason == "consecutive_errors":
            reason = "3 consecutive retryable errors"
        elif reason == "user":
            reason = "stopped by user"
        _panel_segments(win, 1, [
            (f"{g.get('fail', 'x')} {reason}",
             colors.get("error", 0) | curses.A_BOLD),
        ], vis)
        _panel_segments(win, 2, [
            ("resume with ", colors.get("dim", 0)),
            ("packrat download-manifest --failed-only", colors.get("info", 0)),
        ], vis)
        written = {1, 2}
    else:
        text = (f"waiting for the next track{_ellipsis(vis)}" if snap.done
                else f"waiting for the first track{_ellipsis(vis)}")
        _panel_segments(win, 1, [(text, colors.get("dim", 0))], vis)
        written = {1}
    _empty_border_rows(win, h, written, vis)
    _draw_panel_bottom(win, vis)


def _draw_counters(win, snap: State, vis: Visual, frame: dict) -> None:
    h, w = win.getmaxyx()
    colors = vis.colors
    flash = bool(frame.get("flash"))
    _draw_panel_top(win, "status", vis, "error" if flash else "info")
    chips = _status_chips(snap, vis)
    gauge = _retry_gauge(snap, vis)
    dim = colors.get("dim", 0)
    segments: list[tuple[str, int]] = []
    for i, (text, key) in enumerate(chips):
        if i:
            segments.append(("   ", dim))
        attr = colors.get(key, 0) | curses.A_BOLD
        if flash and key == "error":
            attr = colors.get("error", 0) | curses.A_BOLD
        segments.append((text, attr))
    gauge_w = tg.display_width(gauge)
    gauge_key = "error" if snap.consecutive_retryable >= 3 else "warning"
    if gauge:
        if _segments_width(segments) + 3 + gauge_w <= w - 4:
            segments += [("   ", dim), (gauge, colors.get(gauge_key, 0))]
        else:
            _panel_segments(win, 1, segments, vis)
            _panel_segments(win, 2, [(gauge, colors.get(gauge_key, 0))], vis)
            _draw_panel_bottom(win, vis)
            return
    _panel_segments(win, 1, segments, vis)
    _draw_panel_bottom(win, vis)


def _draw_failures(win, snap: State, vis: Visual, frame: dict) -> None:
    h, w = win.getmaxyx()
    colors = vis.colors
    _draw_panel_top(win, "recent failures", vis, "error")
    y = 1
    for fail in reversed(list(snap.recent_failures)[-(max(0, h - 2)):]):
        title = _clean(str(fail.get("title", "")))
        reason = _clean(str(fail.get("reason", "")))
        _panel_segments(win, y, [
            (f"{vis.g.get('fail', 'x')} {title}", colors.get("error", 0) | curses.A_BOLD),
            ("  ", colors.get("dim", 0)),
            (reason, colors.get("dim", 0)),
        ], vis)
        y += 1
        if y >= h - 1:
            break
    _empty_border_rows(win, h, set(range(1, y)), vis)
    _draw_panel_bottom(win, vis)


def _draw_log(win, snap: State, vis: Visual, ring_handler, log_offset: int) -> None:
    h, w = win.getmaxyx()
    _draw_panel_top(win, "log", vis)
    lines: list[str] = []
    if ring_handler is not None:
        try:
            lines = list(ring_handler.lines(RING_CAPACITY))
        except Exception:
            lines = []
    if not lines:
        lines = list(snap.log_lines)
    if log_offset:
        log_offset = min(log_offset, max(0, len(lines) - 1))
        lines = lines[: len(lines) - log_offset]
    visible = max(0, h - 2)
    shown = lines[-visible:]
    colors = vis.colors
    for i, line in enumerate(shown):
        line = _clean(line)
        upper = line.upper()
        if "ERROR" in upper or "FAILED" in upper:
            attr = colors.get("error", 0)
        elif "WARN" in upper:
            attr = colors.get("warning", 0)
        elif "INFO" in upper or "DONE" in upper or "SUCCESS" in upper:
            attr = colors.get("info", 0)
        else:
            attr = colors.get("dim", 0)
        _panel_segments(win, i + 1, [(line, attr)], vis)
    _empty_border_rows(win, h, set(range(1, 1 + len(shown))), vis)
    _draw_panel_bottom(win, vis)


def _draw_footer(win, snap, y: int = 0, show_log: bool = False,
                 vis: Visual | None = None, log_visible: bool | None = None) -> None:
    h, w = win.getmaxyx()
    if y < 0 or y >= h:
        return
    colors = vis.colors if vis is not None else {}
    key_attr = colors.get("info", 0) | curses.A_BOLD
    desc_attr = colors.get("dim", 0)
    if snap.status == "STOPPING":
        text = (f" finishing the current track{_ellipsis(vis)} press q to quit "
                "after it finishes ")
        _safe_addstr(win, y, 0, text[:w],
                     colors.get("warning", 0) | curses.A_BOLD)
        return
    if log_visible is None:
        log_visible = show_log
    log_label = "hide log" if log_visible else "log"
    keys = [
        ("p", "pause"), ("s", "skip"), ("q", "quit"),
        ("l", log_label), ("a", "anim"), ("t", "theme"), ("?", "help"),
    ]
    x = 0
    for key, label in keys:
        key_text = f"{key} "
        desc = f"{label}  "
        if x + len(key_text) + len(desc) > w:
            break
        _safe_addstr(win, y, x, key_text, key_attr)
        x += len(key_text)
        _safe_addstr(win, y, x, desc, desc_attr)
        x += len(desc)


def _draw_help(stdscr, vis: Visual) -> None:
    h, w = stdscr.getmaxyx()
    bw = min(58, max(0, w - 4))
    bh = 9
    if bw <= 2 or h < bh:
        return
    y0 = max(0, (h - bh) // 2)
    x0 = max(0, (w - bw) // 2)
    win = stdscr.derwin(bh, bw, y0, x0)
    if win is None:
        return
    _draw_panel_top(win, "help", vis)
    rows = [
        ("p / space", "pause or resume"),
        ("s", "skip current track"),
        ("l", "toggle log view (arrows scroll)"),
        ("a", "animation on/off"),
        ("t", "next theme"),
        ("?", "toggle this help"),
        ("q", "quit (finishes current track)"),
    ]
    for i, (key, label) in enumerate(rows, start=1):
        _panel_segments(win, i, [
            (f" {key} ", vis.pill or curses.A_REVERSE),
            ("  ", 0),
            (label, vis.colors.get("dim", 0)),
        ], vis)
    _draw_panel_bottom(win, vis)
    win.noutrefresh()


# --- layout + frame ---------------------------------------------------------


def _status_chips(snap: State, vis: Visual) -> list[tuple[str, str]]:
    """Only non-zero counters, so the status line stays calm and readable."""
    g = vis.g
    chips: list[tuple[str, str]] = []
    if snap.downloaded:
        chips.append((f"{g.get('done', '+')} {snap.downloaded} done", "success"))
    if snap.archived:
        chips.append((f"{g.get('dot_off', 'o')} {snap.archived} archived", "dim"))
    if snap.failed:
        chips.append((f"{g.get('fail', 'x')} {snap.failed} failed", "error"))
    if snap.retries:
        chips.append((f"{g.get('retry', 'r')} {snap.retries} retries", "warning"))
    if snap.skipped_size:
        chips.append((f"{g.get('ellipsis', '..')} {snap.skipped_size} skipped-size", "warning"))
    if snap.skipped:
        chips.append((f"{g.get('ellipsis', '..')} {snap.skipped} skipped", "warning"))
    if not chips:
        chips.append((f"{g.get('dot', '*')} ready", "info"))
    return chips


def _retry_gauge(snap: State, vis: Visual) -> str:
    c = snap.consecutive_retryable
    if c <= 0:
        return ""
    g = vis.g
    dots = (g.get("dot", "*") * min(3, c)
            + g.get("dot_off", "o") * max(0, 3 - c))
    return f"retry streak {dots} {c}/3"


def _counters_height(snap: State, w: int, vis: Visual) -> int:
    chips = _status_chips(snap, vis)
    gauge = _retry_gauge(snap, vis)
    text_w = sum(tg.display_width(t) for t, _ in chips) + 3 * (len(chips) - 1)
    if not gauge or text_w + 3 + tg.display_width(gauge) <= w - 4:
        return 3
    return 4


def _panel_heights(snap: State, w: int, vis: Visual) -> list[tuple[str, int]]:
    heights: list[tuple[str, int]] = [
        ("overall", 3 if snap.total <= 0 else 4),
    ]
    cur = snap.current
    if cur.video_id:
        cur_rows = 5
    elif snap.status.startswith("WAITING"):
        cur_rows = 1 + (1 if snap.next_title else 0)
    else:
        cur_rows = 1
    heights.append(("current", cur_rows + 2))
    heights.append(("counters", _counters_height(snap, w, vis)))
    if snap.failed > 0:
        heights.append(
            ("failures", min(5, len(snap.recent_failures)) + 2))
    return heights


def _log_line_count(ring_handler, snap: State) -> int:
    if ring_handler is not None:
        try:
            return len(ring_handler.lines(RING_CAPACITY))
        except Exception:
            pass
    return len(snap.log_lines)


def _draw_outer_frame(win, vis: Visual) -> None:
    """Rounded outer frame; header and footer live inside it."""
    h, w = win.getmaxyx()
    if h < 2 or w < 2:
        return
    g = vis.g
    attr = vis.colors.get("dim", 0)
    tl, tr, bl, br = (str(g.get("tl", "╭")), str(g.get("tr", "╮")),
                      str(g.get("bl", "╰")), str(g.get("br", "╯")))
    hz, vt = str(g.get("h", "─")), str(g.get("v", "│"))
    _safe_addstr(win, 0, 0, tl + hz * (w - 2) + tr, attr)
    for y in range(1, h - 1):
        _safe_addstr(win, y, 0, vt, attr)
        _safe_addstr(win, y, w - 1, vt, attr)
    _safe_addstr(win, h - 1, 0, bl + hz * (w - 2) + br, attr)


def _draw_all(stdscr, snap: State, vis: Visual, ring_handler, show_log: bool,
              log_offset: int, help_mode: bool, frame: dict) -> None:
    h, w = stdscr.getmaxyx()
    stdscr.erase()
    if h < 12 or w < 40:
        _safe_addstr(stdscr, 0, 0, "terminal too small", curses.A_BOLD)
        stdscr.noutrefresh()
        curses.doupdate()
        return
    # Rounded outer frame; header/footer bands live inside it, body centered.
    _draw_outer_frame(stdscr, vis)
    inner_w = w - 2
    inner_h = h - 2
    cw = min(inner_w, MAX_CONTENT_WIDTH)
    x0 = 1 + max(0, (inner_w - cw) // 2)
    body_h = inner_h - 2
    body = stdscr.derwin(body_h, cw, 2, x0)
    header = stdscr.derwin(1, inner_w, 1, 1)
    _draw_header(header, snap, vis)
    header.noutrefresh()
    footer = stdscr.derwin(1, inner_w, h - 2, 1)
    _draw_footer(footer, snap, 0, show_log, vis, log_visible=show_log and h >= 30)
    footer.noutrefresh()
    heights = _panel_heights(snap, cw, vis)
    gaps = 0 if h < 30 else (2 if h >= 36 else 1)
    base_row = sum(v for _, v in heights) + gaps * max(0, len(heights) - 1)
    log_shown = show_log and h >= 30
    log_h = 0
    if log_shown:
        want = max(3, _log_line_count(ring_handler, snap) + 2)
        log_h = min(body_h - base_row - gaps, want)
        if log_h < 3:
            log_shown = False
            log_h = 0
    total_h = base_row + (gaps + log_h if log_shown else 0)
    free = max(0, body_h - total_h)
    top_margin = free // 2
    y = top_margin
    drawers = {
        "overall": _draw_overall,
        "current": _draw_current,
        "counters": _draw_counters,
        "failures": _draw_failures,
    }
    shown: list = []
    for name, ph in heights:
        if y + ph > body_h:
            break  # never drop content for spacing alone; clip the tail
        win = body.derwin(ph, cw, y, 0)
        drawers[name](win, snap, vis, frame)
        shown.append(win)
        y += ph + gaps
    if log_shown:
        remaining = body_h - y
        log_h = min(log_h, remaining)
        if log_h >= 3:
            win = body.derwin(log_h, cw, y, 0)
            _draw_log(win, snap, vis, ring_handler, log_offset)
            shown.append(win)
    for win in shown:
        win.noutrefresh()
    body.noutrefresh()
    if help_mode:
        _draw_help(stdscr, vis)
    stdscr.noutrefresh()
    curses.doupdate()


# --- keys -------------------------------------------------------------------


def _handle_keys(stdscr, control: Control, state: State,
                 show_log: bool, log_offset: int, help_mode: bool):
    """Existing keys keep their behavior; a adds anim toggle, t cycles theme."""
    action = ""
    try:
        key = stdscr.getch()
    except curses.error:
        return True, show_log, log_offset, help_mode, action
    if key == -1:
        return True, show_log, log_offset, help_mode, action
    active = state.status in ("RUNNING", "WAITING", "PAUSED")
    if key in (ord("q"), ord("Q")):
        if state.status in ("DONE", "STOPPED", "ABORTED", "STOPPING"):
            action = "quit"
        else:
            control.request_stop()
            state.apply(Event("status", {"status": "STOPPING", "reason": "user quit"}))
            action = "stopping"
    elif key in (ord("p"), ord("P"), 32) and active:
        control.toggle_pause()
        state.apply(Event("status", {"status": "PAUSED" if control.paused else "RUNNING"}))
    elif key in (ord("s"), ord("S")) and active and state.current.video_id:
        control.request_skip()
    elif key in (ord("l"), ord("L")):
        show_log = not show_log
        log_offset = 0
    elif key in (ord("a"), ord("A")):
        action = "toggle_anim"
    elif key in (ord("t"), ord("T")):
        action = "next_theme"
    elif key == 63:  # ?
        help_mode = not help_mode
    elif key == curses.KEY_UP and show_log:
        log_offset += 1
    elif key == curses.KEY_DOWN and show_log:
        log_offset = max(0, log_offset - 1)
    elif key == curses.KEY_PPAGE and show_log:
        log_offset += 10
    elif key == curses.KEY_NPAGE and show_log:
        log_offset = max(0, log_offset - 10)
    elif key == curses.KEY_RESIZE:
        action = "resize"
    return True, show_log, log_offset, help_mode, action


# --- main loop --------------------------------------------------------------


def _ui(stdscr, state: State, control: Control, ring_handler, ascii_mode: bool,
        no_anim: bool | None = None) -> None:
    stdscr.nodelay(True)
    stdscr.timeout(100)
    curses.curs_set(0)
    try:
        curses.start_color()
        curses.use_default_colors()
    except curses.error:
        pass
    if no_anim is None:
        no_anim = os.environ.get("PACKRAT_TUI_ANIM", os.environ.get("YTM_TUI_ANIM")) == "0"
    vis = _build_visual(_visual_state["theme"], ascii_mode, no_anim)
    show_log = True
    help_mode = False
    log_offset = 0
    history: deque[tuple[float, int]] = deque(maxlen=240)
    last_failed = 0
    last_fail_ts = 0.0
    done_ts: float | None = None
    last_hist = 0.0
    prev_sig: tuple | None = None
    order = list(tt.THEMES)

    while True:
        snap = state.snapshot()  # the ONE per-frame state copy
        now = time.monotonic()
        mode = anim_mode(snap.status)
        animating = not vis.no_anim and mode in ("running", "waiting")
        if snap.failed > last_failed:
            last_failed = snap.failed
            last_fail_ts = now
        if snap.status == "DONE" and done_ts is None:
            done_ts = now
        sweep = None if done_ts is None else (now - done_ts) / 1.5
        if now - last_hist >= 0.25:
            history.append((now, snap.total_bytes_downloaded))
            last_hist = now
        sig = (
            snap.status, snap.done, snap.failed, snap.total_bytes_downloaded,
            snap.current.percent, show_log, log_offset, help_mode,
            len(snap.recent_failures), vis.no_anim, _visual_state["theme"],
            None if sweep is None else int(sweep * 20),
        )
        if animating or sig != prev_sig:
            frame = {
                "mode": mode,
                "phase": tt.wave_phase(now, 0.8),
                "thin_phase": tt.wave_phase(now, 1.6) + 0.3,
                "breath": tt.breathe(now, 0.4),
                "flash": bool(last_fail_ts and (now - last_fail_ts) < 0.4),
                "sweep": sweep,
                "history": tuple(history),
            }
            _draw_all(stdscr, snap, vis, ring_handler, show_log, log_offset, help_mode, frame)
            prev_sig = sig
        stdscr.timeout(50 if animating else 250)
        cont, show_log, log_offset, help_mode, action = _handle_keys(
            stdscr, control, state, show_log, log_offset, help_mode)
        if action == "quit":
            break
        if action == "toggle_anim":
            vis.no_anim = not vis.no_anim
            prev_sig = None
        elif action == "next_theme":
            nxt = order[(order.index(_visual_state["theme"]) + 1) % len(order)]
            _visual_state["theme"] = nxt
            vis = _build_visual(nxt, ascii_mode, vis.no_anim)
            prev_sig = None
        elif action == "resize":
            prev_sig = None
        if not cont:
            break


def _worker_target(worker_func: Callable, worker_kwargs: dict, state: State,
                   control: Control, result_container: dict) -> None:
    def event_callback(event: dict) -> None:
        state.apply(Event(event["type"], event.get("payload") or {}))

    kwargs = dict(worker_kwargs)
    kwargs["event_callback"] = event_callback
    kwargs["control"] = control
    try:
        result_container["return_value"] = worker_func(**kwargs)
    except Exception as exc:
        logging.getLogger(__name__).exception("worker crashed")
        state.apply(Event("stopped", {"reason": f"worker crashed: {exc}"}))
        result_container["exception"] = exc
        result_container["traceback"] = traceback.format_exc()
        return
    if isinstance(result_container.get("return_value"), dict) and result_container["return_value"].get("error"):
        state.apply(Event("stopped", {"reason": str(result_container["return_value"]["error"])}))
    elif state.status not in ("STOPPED", "ABORTED", "STOPPING"):
        state.apply(Event("done", {"reason": "finished"}))


def _join_worker(thread: threading.Thread, log_note: str = "") -> None:
    """Wait for the worker; a second Ctrl-C force-exits (partial file may remain)."""
    if thread.is_alive():
        print("Finishing the current track before exit…", file=sys.stderr)
    try:
        thread.join()
    except KeyboardInterrupt:
        global _palette_dirty
        if _palette_dirty:
            tt.restore_palette(slots=_custom_slots)
            _palette_dirty = False
            _custom_slots.clear()
        print("second Ctrl-C: download thread still running — a partial file may "
              f"remain{(f'; full log: {log_note}' if log_note else '')}",
              file=sys.stderr)
        os._exit(130)


def run_tui(worker_func: Callable, worker_kwargs: dict, mode: str = "",
            ascii_mode: bool = False, no_anim: bool | None = None,
            no_color: bool | None = None, palette: str | None = None) -> int:
    """Run worker under the TUI; plain logging fallback when stdout/stdin is no TTY."""
    if not supports_tui():
        print("This terminal can't show the full-screen view; "
              "continuing with normal output.", file=sys.stderr)
        return worker_func(**worker_kwargs)
    if no_color is not None or palette is not None:
        # None leaves the global (set by main()) intact.
        set_visual_options(no_color=no_color, palette=palette)
    if no_anim is None:
        no_anim = _no_anim_default
    ring_handler = setup_logging(tui_active=True)  # creates LOG_DIR itself
    state = State(mode=mode)
    control = Control()
    result_container: dict[str, Any] = {"return_value": None, "exception": None}
    uses_browser_cookies = bool(worker_kwargs.get("cookies_from_browser"))
    # Not a daemon: on quit the worker finishes its current track, writes
    # archive/manifest/m3u8 and unwinds (deleting any temp cookie file)
    # before run_tui returns.
    thread = threading.Thread(
        target=_worker_target,
        args=(worker_func, worker_kwargs, state, control, result_container),
    )
    thread.start()
    try:
        curses.wrapper(_ui, state, control, ring_handler, ascii_mode, True if no_anim else None)
    except KeyboardInterrupt:
        control.request_stop()
        state.apply(Event("status", {"status": "ABORTED", "reason": "Ctrl-C"}))
        print("finishing the current track… Ctrl-C again to force quit",
              file=sys.stderr)
    except Exception as exc:  # UI bug: stop the worker cleanly, no traceback
        # The worker finishes its current track, writes archive/manifest/
        # m3u8 and unwinds (deleting any temp cookie file) before exiting.
        control.request_stop()
        path = _latest_log_path()
        print(f"TUI crashed: {exc.__class__.__name__}: {exc}",
              file=sys.stderr)
        print(f"download stopped cleanly{' (cookie temporary files deleted)'
              if uses_browser_cookies else ''};"
              f" full log: {path}", file=sys.stderr)
        return 1
    finally:
        global _palette_dirty
        if _palette_dirty:
            tt.restore_palette(slots=_custom_slots)
            _palette_dirty = False
            _custom_slots.clear()
        control.request_stop()
        _join_worker(thread, _latest_log_path())
    if result_container["exception"]:
        raise result_container["exception"]
    return result_container["return_value"] if result_container["return_value"] is not None else 0


def _latest_log_path() -> str:
    import glob

    paths = (
        glob.glob(str(LOG_DIR / "packrat-*.log"))
        + glob.glob(str(LOG_DIR / "ytm-offline-*.log"))
    )
    paths.sort(key=lambda path: os.path.getmtime(path))
    return paths[-1] if paths else f"{LOG_DIR}/"


def supports_tui() -> bool:
    return sys.stdout.isatty() and sys.stdin.isatty()


# --- demo worker (no network, no files, no yt-dlp) ---------------------------
