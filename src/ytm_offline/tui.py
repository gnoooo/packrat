"""Curses TUI for ytm_offline downloads — themed, animated renderer.

Render layer only: the downloader/pipeline, events/State schema and Control
semantics are untouched. Colors come from tui_theme, glyphs from tui_glyphs.
"""

from __future__ import annotations

import curses
import os
import random
import re
import sys
import threading
import time
import traceback
from collections import deque
from typing import Any, Callable

from ytm_offline import tui_glyphs as tg
from ytm_offline import tui_theme as tt
from ytm_offline.control import Control
from ytm_offline.events import Event, State
from ytm_offline.logging_config import (LOG_DIR, RING_CAPACITY,  # noqa: F401
                                        RingBufferHandler, redact,  # noqa: F401
                                        setup_logging)

FANCY = tg.GLYPHS["fancy"]


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
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def render_bar(width: int, percent: float, ascii_mode: bool = False) -> str:
    """Unicode bar now uses the 1/8-cell glyph engine; ascii keeps brackets."""
    if ascii_mode:
        return _render_bar_ascii(width, percent)
    return tg.bar(FANCY, width, percent)


def _render_bar_ascii(width: int, percent: float) -> str:
    if width <= 0:
        return ""
    percent = max(0.0, min(100.0, percent))
    filled = int(width * percent / 100)
    return "[" + "#" * filled + "-" * (width - filled) + "]"


_CTRL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_RUN_SPACES_RE = re.compile(r" {2,}")


def _clean(text: str) -> str:
    """Control chars → space, collapsed runs, stripped ends (curses-safe)."""
    text = _CTRL_CHARS_RE.sub(" ", str(text))
    return _RUN_SPACES_RE.sub(" ", text).strip()


def truncate(text: str, width: int) -> str:
    """Sanitize, redact and cut to a display width, ellipsis when needed."""
    text = redact(_clean(str(text)))
    if width <= 3:
        return text[: width]
    if tg.display_width(text) <= width:
        return text
    return tg.clip_width(text, width - 1) + "…"


# --- visual options (CLI-wide flag set once in main()) ----------------------

_visual_state = {"theme": "ocean", "no_color": False}


def set_visual_options(theme: str = "ocean", no_color: bool = False,
                       palette: str = "auto") -> None:
    """Single place the CLI passes its visual flags; run_tui call sites unchanged."""
    if theme in tt.THEMES:
        _visual_state["theme"] = theme
    _visual_state["no_color"] = bool(no_color)
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
                 track_attr: int | None = None) -> None:
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

    def wave_attr(self, x: float, phase: float) -> int:
        idx, bold = self.pal.attr_for_intensity(tt.wave_intensity(x, phase))
        return self.attr(idx, bold) if idx >= 0 else 0

    def breathe_attr(self, breath: float) -> int:
        idx, bold = self.pal.attr_for_intensity(breath)
        return self.attr(idx, bold) if idx >= 0 else 0


def _make_bar_pairs(cur, grad_values: dict[int, int], track_value: int,
                    first_pair: int) -> tuple[dict[int, int], dict[int, int], int | None]:
    """Space-bar pair tables, built ONCE at startup.

    Returns ({pos: bg_attr}, {pos: edge_attr}, track_attr) where bg pairs
    (fg=grad, bg=grad) render the gradient as spaces, edge pairs
    (fg=grad, bg=track) color the 1/8 partial cell, and the track pair
    (fg=track, bg=track) fills the empty part. Returns ({}, {}, None)
    when the pair budget (COLOR_PAIRS) is exhausted or init fails — the
    renderer then falls back to the fill-char bar.
    """
    try:
        budget = cur.COLOR_PAIRS
    except Exception:
        return {}, {}, None
    steps = len(grad_values)
    if steps == 0 or first_pair + 2 * steps > budget:
        return {}, {}, None
    bg: dict[int, int] = {}
    edge: dict[int, int] = {}
    n = first_pair
    try:
        for pos, grad in grad_values.items():
            cur.init_pair(n, grad, grad)
            bg[pos] = cur.color_pair(n)
            n += 1
            cur.init_pair(n, grad, track_value)
            edge[pos] = cur.color_pair(n)
            n += 1
        cur.init_pair(n, track_value, track_value)
        track_attr = cur.color_pair(n)
    except cur.error:
        return {}, {}, None
    return bg, edge, track_attr


def _build_visual(stdscr, theme_name: str, ascii_mode: bool, no_anim: bool) -> Visual:
    import curses as _cur

    glyphs = tg.pick_set(force_ascii=ascii_mode)
    has_colors = _cur.has_colors()
    no_color = tt.no_color_requested() or _visual_state.get("no_color")
    can_change = False
    try:
        can_change = bool(_cur.can_change_color() and _cur.COLORS >= 256)
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
    pal = tt.Palette(theme, tier)
    colors: dict[str, int] = {k: 0 for k in tt.COLOR_KEYS}

    def plain_attr(idx: int, bold: bool = False) -> int:
        return _cur.A_BOLD if bold else 0

    attr: Callable[[int, bool], int] = plain_attr
    bar_bg: dict[int, int] = {}
    bar_edge: dict[int, int] = {}
    track_attr: int | None = None
    if tier != tt.TIER_NONE:
        try:
            _cur.start_color()
            if tier == tt.TIER_CUSTOM:
                gradient_pairs, static_pairs = tt.install_custom_rgb(
                    pal.gradient_rgb_list,
                    {k: tt.parse_hex(getattr(theme, k)) for k in tt.COLOR_KEYS},
                    curses_mod=_cur,
                )
                pal.custom_pairs = gradient_pairs

                def themed_attr(idx: int, bold: bool = False,
                                _pairs: dict = gradient_pairs) -> int:
                    a = _cur.color_pair(_pairs.get(idx, 0))
                    return a | _cur.A_BOLD if bold else a

                # static colors already come back as pair numbers
                colors = {
                    k: (_cur.color_pair(p) if p >= 0 else 0)
                    for k, p in static_pairs.items()
                }
                # Gradient slots start at tt's first_slot (32, install order
                # = rgb list); the track color got the last slot.
                grad_slots = {
                    pos: 32 + pos  # tt install order: gradient rgb slots first
                    for pos in range(len(pal.gradient_rgb_list))
                }
                track = static_pairs.get(tt.TRACK_KEY)
                first_free = (
                    1 + len(pal.gradient_rgb_list) + len(static_pairs) - 1
                )
                if track is not None:
                    bar_bg, bar_edge, track_attr = _make_bar_pairs(
                        _cur, grad_slots, track, first_free,
                    )
            else:
                pairs = tt.activate(pal)

                def themed_attr(idx: int, bold: bool = False, _pairs: dict = pairs) -> int:
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
                if track is not None and track >= 0:
                    bar_bg, bar_edge, track_attr = _make_bar_pairs(
                        _cur, grad_idx, track, first_free,
                    )
            attr = themed_attr
        except (_cur.error, ValueError):
            attr = plain_attr
            colors = {k: 0 for k in tt.COLOR_KEYS}
            bar_bg, bar_edge, track_attr = {}, {}, None
    return Visual(glyphs, pal, colors, attr, no_anim, tier,
                  bar_bg=bar_bg, bar_edge=bar_edge, track_attr=track_attr)


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


def _bar_position(vis: Visual, i: int, width: int, mode: str, now: float) -> int:
    """Gradient position (0..steps-1) for bar cell i; running mode adds the
    moving crest highlight."""
    steps = len(vis.bar_bg)
    if width <= 1:
        t = 0.0
    else:
        t = i / (width - 1)
    if mode == "running":
        rgb = tt.blend(
            vis.pal.theme.walk(t), tt.parse_hex(vis.pal.theme.highlight),
            tt.CREST_BLEND * tt.crest(i, now, width),
        )
        return tt.nearest_position(rgb, vis.pal.theme, steps)
    return round(t * (steps - 1))


def _draw_space_bar(win, y: int, prefix: str, body: str, suffix: str,
                    vis: Visual, mode: str, now: float,
                    base_attr: int = 0) -> None:
    """Gradient bar rendered as colored SPACES (no █ seams), grouped into
    one addstr per same-attr run."""
    g = vis.g
    edge_set = set(str(g["bar_edge"]))
    track_c = str(g["bar_track"])
    fill_c = str(g["bar_fill"])
    x = tg.display_width(prefix)
    _safe_addstr(win, y, 0, prefix, base_attr)
    runs: list[tuple[int, str]] = []
    for i, ch in enumerate(body):
        pos = _bar_position(vis, i, len(body), mode, now)
        if ch != fill_c and (ch != track_c and ch not in edge_set):
            # unexpected char: keep it as-is, uncolored
            attr, text = 0, ch
        elif ch == track_c:
            attr = vis.track_attr if vis.track_attr is not None else 0
            text = " "
        elif ch in edge_set:
            attr = vis.bar_edge.get(pos, vis.track_attr or 0)
            text = ch
        else:
            attr = vis.bar_bg.get(pos, 0)
            text = " "
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
    """prefix + per-cell colored body + suffix; bar cells get the wave."""
    if vis.tier == tt.TIER_NONE or not body:
        _safe_addstr(win, y, 0, prefix + body + suffix, base_attr)
        return
    if (not thin and vis.bar_bg and mode in ("running", "waiting")
            and vis.track_attr is not None):
        # Bar animation is driven by the injected frame phase, never the
        # wall clock, so renders (and tests) are deterministic per frame.
        _draw_space_bar(win, y, prefix, body, suffix, vis, mode,
                        phase, base_attr)
        return
    _safe_addstr(win, y, 0, prefix, base_attr)
    fill = str(vis.g["thin_fill"] if thin else vis.g["bar_fill"])
    edge = {str(vis.g["thin_edge"])} if thin and vis.g["thin_edge"] else set(str(vis.g["bar_edge"]))
    fill_set = {fill} | edge
    x = tg.display_width(prefix)
    for i, ch in enumerate(body):
        if ch in fill_set:
            _safe_addstr(win, y, x, ch, _cell_attr(vis, i, mode, phase, breath, sweep, True, len(body)))
        else:
            _safe_addstr(win, y, x, ch, base_attr)
        x += tg.char_width(ch)
    _safe_addstr(win, y, x, suffix, base_attr)


def _spark_rates(history: tuple[tuple[float, int], ...]) -> list[float]:
    """Per-second byte-rate deltas from (ts, total_bytes) samples."""
    buckets: dict[int, int] = {}
    for ts, total in history:
        buckets[int(ts)] = total
    stamps = sorted(buckets)
    return [max(0, buckets[b] - buckets[a]) for a, b in zip(stamps, stamps[1:])]


# --- panels -----------------------------------------------------------------


def _row_borders(win, y: int, vis: Visual) -> None:
    """Left and right '│' of one content row (no padding decisions)."""
    _, w = win.getmaxyx()
    if w < 2:
        return
    _safe_addstr(win, y, 0, vis.g.get("v", "|"), 0)
    _safe_addstr(win, y, w - 1, vis.g.get("v", "|"), 0)


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
    _safe_addstr(win, y, 0, vis.g.get("v", "|"), 0)
    _safe_addstr(win, y, 1, " " + body + pad, attr)
    _safe_addstr(win, y, w - 1, vis.g.get("v", "|"), 0)


def _right_border(win, y: int, vis: Visual) -> None:
    """Right '│' for rows that themed lines draw themselves."""
    h, w = win.getmaxyx()
    if h < 1 or w < 2 or y < 0 or y >= h:
        return
    _safe_addstr(win, y, w - 1, vis.g.get("v", "|"), 0)


def _throughput_graph(frame: dict, avail: int) -> str:
    """Braille deltas of the last minute, or '' when there is no room."""
    if avail < 8:
        return ""
    graph_w = min(40, avail)
    hist = frame.get("history") or ()
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
    return tg.braille_graph(deltas, graph_w)


def _draw_header(win, snap: State, vis: Visual) -> None:
    """Full-width header band: '♪ ytm-offline › <mode>' left, status badge
    (own color) + elapsed on the right, every cell covered."""
    h, w = win.getmaxyx()
    g = vis.g
    status = snap.status
    colors = vis.colors
    if status == "DONE":
        sattr = colors.get("success", 0)
    elif status in ("STOPPED", "ABORTED"):
        sattr = colors.get("error", 0)
    elif status in ("STOPPING",) or status.startswith("WAITING"):
        sattr = colors.get("warning", 0) if status.startswith("WAITING") else colors.get("stopping", 0)
    else:
        sattr = colors.get("info", 0)
    right = f" {g.get('dot', '*')} {status}  {g.get('clock', '~')} {format_duration(snap.elapsed_seconds)} "
    right_w = tg.display_width(right)
    left = truncate(f" {g.get('note', '~')} ytm-offline › {snap.mode}", max(4, w - right_w - 1))
    pad = max(0, w - tg.display_width(left) - right_w)
    _safe_addstr(win, 0, 0, left + " " * pad, curses.A_BOLD)
    x = max(0, w - right_w)
    if x > tg.display_width(left):
        _safe_addstr(win, 0, x, right, sattr | curses.A_BOLD)


def _draw_overall(win, snap: State, vis: Visual, frame: dict) -> None:
    h, w = win.getmaxyx()
    _safe_addstr(win, 0, 0, tg.panel_top(vis.g, w, "overall"), curses.A_BOLD)
    if snap.total <= 0:
        _panel_row(win, 1, "resolving tracks...", vis, bold=True)
        _safe_addstr(win, h - 1, 0, tg.panel_bottom(vis.g, w), curses.A_BOLD)
        return
    pct = snap.done / snap.total * 100
    eta = snap.eta_seconds()
    eta_s = "…" if eta is None else format_duration(eta)
    # Line 1: wave bar + percent only (bug 3: no track count here).
    bar_w = max(10, min(100, w - 26))
    body = tg.bar(vis.g, bar_w, pct)
    suffix = f" {pct:5.1f}% "
    # Suffix stays left of the right border at column w-1 (never overwritten);
    # plain clip (no sanitize) — the suffix is generated, not user text.
    suffix = tg.clip_width(suffix, max(0, w - 1 - 2 - bar_w))
    _draw_themed_line(win, 1, "│ ", body, suffix, vis, frame["mode"], frame["phase"],
                      frame["breath"], frame.get("sweep"), 0)
    _right_border(win, 1, vis)
    # Line 2: tracks · bytes · ETA · speed + braille graph.
    base = (f"{snap.done}/{snap.total} tracks · "
            f"{format_bytes(snap.total_bytes_downloaded)} / "
            f"{format_bytes(snap.total_est_bytes)} · ETA {eta_s}")
    if snap.max_size_bytes:
        base += f" · max {format_bytes(snap.max_size_bytes)}"
    rate = tg.rates_60s(frame.get("history") or (), time.monotonic())
    if rate:
        base += f" · {format_bytes(rate)}/s"
    avail = w - 4 - tg.display_width(base) - 2
    graph = _throughput_graph(frame, avail)
    if graph:
        base += "  " + graph
    _panel_row(win, 2, base, vis, attr=vis.colors.get("info", 0))
    _safe_addstr(win, h - 1, 0, tg.panel_bottom(vis.g, w), curses.A_BOLD)


def _empty_border_rows(win, h: int, written: set[int], vis: Visual) -> None:
    """Panels that may be content-empty still get complete borders."""
    for y in range(1, h - 1):
        if y not in written:
            _panel_row(win, y, "", vis)


def _draw_current(win, snap: State, vis: Visual, frame: dict) -> None:
    h, w = win.getmaxyx()
    cur = snap.current
    _safe_addstr(win, 0, 0, tg.panel_top(vis.g, w, "now playing"),
                 vis.colors.get("info", 0) | curses.A_BOLD)
    written: set[int] = set()
    if cur.video_id:
        _panel_row(win, 1, cur.title, vis, bold=True)
        _panel_row(win, 2, f"{cur.artist} - {cur.album}", vis)
        _panel_row(win, 3, f"source: {cur.source}  |  stage: {cur.stage}", vis)
        bar_w = max(10, w - 40)
        pct = max(0.0, min(100.0, cur.percent))
        eta = "…" if cur.eta_seconds is None else format_duration(cur.eta_seconds)
        prefix = f"│ {pct:5.1f}% "
        # Spec order: '<pct>% <bytes>/<total> <speed>'; ETA last so the
        # long tail clips in the middle, never the percent.
        suffix = f" {format_bytes(cur.downloaded_bytes)}/{format_bytes(cur.total_bytes)}"
        if cur.speed:
            suffix += f" {cur.speed}"
        suffix += f"  ETA {eta}"
        # Suffix stays left of the right border at column w-1 (never overwritten).
        suffix = truncate(suffix, max(0, w - 1 - tg.display_width(prefix) - bar_w))
        _draw_themed_line(win, 4, prefix, tg.thin_bar(vis.g, bar_w, pct), suffix,
                          vis, frame["mode"], frame["thin_phase"], frame["breath"],
                          frame.get("sweep"), curses.A_BOLD, thin=True)
        _right_border(win, 4, vis)
        written = {1, 2, 3, 4}
    elif snap.status.startswith("WAITING"):
        # Between tracks: show what finished and what is up next (bug d).
        _panel_row(win, 1, f"{vis.g.get('done', '+')} finished: {snap.last_finished}", vis)
        if snap.next_title:
            _panel_row(win, 2,
                       f"{vis.g.get('play', '>')} next: {snap.next_title}", vis, bold=True)
            written = {1, 2}
        else:
            written = {1}
    else:
        _panel_row(win, 1, "waiting for the first track...", vis)
        written = {1}
    _empty_border_rows(win, h, written, vis)
    _safe_addstr(win, h - 1, 0, tg.panel_bottom(vis.g, w), curses.A_BOLD)


def _draw_counters(win, snap: State, vis: Visual, frame: dict) -> None:
    h, w = win.getmaxyx()
    g = vis.g
    flash_attr = (vis.colors.get("error", 0) | curses.A_BOLD) if frame.get("flash") else 0
    _safe_addstr(win, 0, 0, tg.panel_top(vis.g, w, "status"),
                 (vis.colors.get("error", 0) if frame.get("flash")
                  else vis.colors.get("info", 0)) | curses.A_BOLD)
    chips_text, gauge = _counters_text(snap, vis)
    c = snap.consecutive_retryable
    if c >= 3:
        gauge_attr = vis.colors.get("error", 0) | curses.A_BOLD
    elif c >= 1:
        gauge_attr = vis.colors.get("warning", 0) | curses.A_BOLD
    else:
        gauge_attr = vis.colors.get("success", 0)
    if (tg.display_width(chips_text) + 3 + tg.display_width(gauge)) <= w - 4:
        _panel_row(win, 1, f"{chips_text}   {gauge}", vis, attr=flash_attr)
    else:
        _panel_row(win, 1, chips_text, vis, attr=flash_attr)
        _panel_row(win, 2, gauge, vis, attr=gauge_attr)
    _safe_addstr(win, h - 1, 0, tg.panel_bottom(vis.g, w), curses.A_BOLD)


def _draw_failures(win, snap: State, vis: Visual, frame: dict) -> None:
    h, w = win.getmaxyx()
    heading = (vis.colors.get("error", 0) | curses.A_BOLD) if frame.get("flash") else curses.A_BOLD
    _safe_addstr(win, 0, 0, tg.panel_top(vis.g, w, "recent failures"), heading)
    y = 1
    half = max(4, (w - 4) // 2)
    for fail in reversed(list(snap.recent_failures)[-(max(0, h - 2)):]):
        title = truncate(str(fail.get("title", "")), half)
        reason = truncate(str(fail.get("reason", "")), max(4, half - 4))
        _panel_row(win, y, f"{title}: {reason}", vis, attr=vis.colors.get("error", 0))
        y += 1
        if y >= h - 1:
            break
    _empty_border_rows(win, h, set(range(1, y)), vis)
    _safe_addstr(win, h - 1, 0, tg.panel_bottom(vis.g, w), curses.A_BOLD)


def _draw_log(win, snap: State, vis: Visual, ring_handler, log_offset: int) -> None:
    h, w = win.getmaxyx()
    _safe_addstr(win, 0, 0, tg.panel_top(vis.g, w, "log"), curses.A_BOLD)
    lines: list[str] = []
    if ring_handler is not None:
        try:
            lines = list(ring_handler.lines(RING_CAPACITY))
        except Exception:
            lines = []
    if not lines:
        lines = list(snap.log_lines)
    if log_offset and log_offset < len(lines):
        lines = lines[: len(lines) - log_offset]
    elif log_offset >= len(lines):
        lines = []
    visible = max(0, h - 2)
    shown = lines[-visible:]
    for i, line in enumerate(shown):
        _panel_row(win, i + 1, line, vis)
    _empty_border_rows(win, h, set(range(1, 1 + len(shown))), vis)
    _safe_addstr(win, h - 1, 0, tg.panel_bottom(vis.g, w), curses.A_BOLD)


def _draw_footer(win, snap, vis: Visual, y: int = 0) -> None:
    h, w = win.getmaxyx()
    if snap.status == "STOPPING":
        text = " finishing the current track… press q again to quit now "
    else:
        text = " p pause/resume  s skip  q quit  l log  a anim  t theme  ? help "
    _safe_addstr(win, y, 0, text.ljust(w), curses.A_REVERSE)


def _draw_help(stdscr, vis: Visual) -> None:
    h, w = stdscr.getmaxyx()
    bw = min(56, max(0, w - 4))
    bh = 9
    if bw <= 2 or h < bh:
        return
    y0 = max(0, (h - bh) // 2)
    x0 = max(0, (w - bw) // 2)
    win = stdscr.derwin(bh, bw, y0, x0)
    if win is None:
        return
    _safe_addstr(win, 0, 0, tg.panel_top(vis.g, bw, "help"), curses.A_BOLD)
    rows = [
        " p / space   pause or resume",
        " s           skip current track",
        " l           toggle log view (arrows scroll)",
        " a           animation on/off",
        " t           next theme",
        " ?           toggle this help",
        " q           quit (finishes current track)",
    ]
    for i, row in enumerate(rows, start=1):
        _safe_addstr(win, i, 0, row, 0)
    _safe_addstr(win, bh - 1, 0, tg.panel_bottom(vis.g, bw), curses.A_BOLD)
    win.noutrefresh()


# --- layout + frame ---------------------------------------------------------


def _counters_text(snap: State, vis: Visual) -> tuple[str, str]:
    """(chips, gauge) line content shared by the drawer and the sizer."""
    g = vis.g
    chips = [
        f"{g.get('done')} {snap.downloaded} downloaded",
        f"{g.get('dot_off')} {snap.archived} archived",
        f"{g.get('fail')} {snap.failed} failed",
        f"{g.get('pause', '|')} {snap.retries} retries",
    ]
    if snap.skipped_size:
        chips.append(f"{g.get('ellipsis', '..')} {snap.skipped_size} skipped-size")
    c = snap.consecutive_retryable
    dots = (
        g.get("dot", "*") * min(3, c) + g.get("dot_off", "o") * max(0, 3 - c)
    )
    return "   ".join(chips), f"errors {dots} {c}/3"


def _counters_height(snap: State, w: int, vis: Visual) -> int:
    chips, gauge = _counters_text(snap, vis)
    if tg.display_width(chips) + 3 + tg.display_width(gauge) <= w - 4:
        return 3
    return 4


def _panel_heights(snap: State, w: int, vis: Visual) -> list[tuple[str, int]]:
    heights: list[tuple[str, int]] = [
        ("overall", 3 if snap.total <= 0 else 4),
    ]
    cur = snap.current
    if cur.video_id:
        cur_rows = 4
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


def _draw_all(stdscr, snap: State, vis: Visual, ring_handler, show_log: bool,
              log_offset: int, help_mode: bool, frame: dict) -> None:
    h, w = stdscr.getmaxyx()
    stdscr.erase()
    if h < 12 or w < 40:
        _safe_addstr(stdscr, 0, 0, "terminal too small", curses.A_BOLD)
        stdscr.noutrefresh()
        curses.doupdate()
        return
    # Header/footer bands run the FULL terminal width; body is centered.
    _draw_header(stdscr, snap, vis)
    _draw_footer(stdscr, snap, vis, h - 1)
    body_h = h - 2
    cw = min(w, 140)
    x0 = max(0, (w - cw) // 2)
    body = stdscr.derwin(body_h, cw, 1, x0)
    heights = _panel_heights(snap, cw, vis)
    compact = h < 30
    gaps = 0 if compact else 1
    base_row = sum(v for _, v in heights) + gaps * max(0, len(heights) - 1)
    log_shown = show_log and h >= 30
    log_h = 0
    if log_shown:
        log_h = body_h - base_row - gaps
        if log_h < 3:
            log_shown = False
            log_h = 0
    if log_shown or compact:
        top_margin = 0
    else:
        # Spec: body_height = sum(panel heights) + gaps;
        # free = (rows - 2) - body_height - 2; 40% of free above the body,
        # the rest below; never negative.
        free = max(0, (h - 2) - base_row - 2)
        top_margin = round(free * 0.4) if free > 0 else 0
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
        log_h = body_h - y
        if log_h >= 3:
            win = body.derwin(log_h, cw, y, 0)
            _draw_log(win, snap, vis, ring_handler, log_offset)
            shown.append(win)
        else:
            log_shown = False
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
    if key in (ord("q"), ord("Q")):
        control.request_stop()
        state.apply(Event("status", {"status": "STOPPING", "reason": "user quit"}))
    elif key in (ord("p"), ord("P"), 32):
        control.toggle_pause()
        state.apply(Event("status", {"status": "PAUSED" if control.paused else "RUNNING"}))
    elif key in (ord("s"), ord("S")):
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
    elif key == curses.KEY_UP:
        log_offset = max(0, log_offset - 1)
    elif key == curses.KEY_DOWN:
        log_offset += 1
    elif key == curses.KEY_PPAGE:
        log_offset = max(0, log_offset - 10)
    elif key == curses.KEY_NPAGE:
        log_offset += 10
    elif key == curses.KEY_RESIZE:
        action = "resize"
    return True, show_log, log_offset, help_mode, action


# --- main loop --------------------------------------------------------------


def _ui(stdscr, state: State, control: Control, ring_handler, ascii_mode: bool,
        no_anim: bool | None = None) -> None:
    stdscr.nodelay(True)
    stdscr.timeout(100)
    curses.curs_set(0)
    if no_anim is None:
        no_anim = os.environ.get("YTM_TUI_ANIM") == "0"
    vis = _build_visual(stdscr, _visual_state["theme"], ascii_mode, no_anim)
    show_log = help_mode = False
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
        if action == "toggle_anim":
            vis.no_anim = not vis.no_anim
            prev_sig = None
        elif action == "next_theme":
            nxt = order[(order.index(_visual_state["theme"]) + 1) % len(order)]
            _visual_state["theme"] = nxt
            vis = _build_visual(stdscr, nxt, ascii_mode, vis.no_anim)
            prev_sig = None
        elif action == "resize":
            prev_sig = None
        if not cont:
            break
        if snap.status in ("DONE", "STOPPED", "ABORTED"):
            sweep_done = sweep is not None and sweep >= 1.0
            stdscr.timeout(100 if not sweep_done else 1000)
            try:
                key = stdscr.getch()
            except curses.error:
                key = -1
            if key in (ord("q"), ord("Q"), 27):
                break
            if key == curses.KEY_RESIZE:
                prev_sig = None


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
        state.apply(Event("stopped", {"reason": f"worker crashed: {exc}"}))
        result_container["exception"] = exc
        result_container["traceback"] = traceback.format_exc()
        return
    if state.status not in ("STOPPED", "ABORTED"):
        state.apply(Event("done", {"reason": "finished"}))


def _join_worker(thread: threading.Thread, log_note: str = "") -> None:
    """Wait for the worker; a second Ctrl-C force-exits (partial file may remain)."""
    try:
        thread.join()
    except KeyboardInterrupt:
        print("second Ctrl-C: download thread still running — a partial file may "
              f"remain{(f'; full log: {log_note}' if log_note else '')}",
              file=sys.stderr)
        os._exit(130)


def run_tui(worker_func: Callable, worker_kwargs: dict, mode: str = "",
            ascii_mode: bool = False, no_anim: bool = False,
            no_color: bool | None = None, palette: str = "auto") -> int:
    """Run worker under the TUI; plain logging fallback when stdout/stdin is no TTY."""
    if not supports_tui():
        print("stdout/stdin is not a TTY; falling back to plain logging",
              file=sys.stderr)
        return worker_func(**worker_kwargs)
    if no_color is not None:
        # Explicit no_color wins; None leaves the global (set by main()) intact.
        set_visual_options(no_color=no_color, palette=palette)
    else:
        set_visual_options(palette=palette)
    ring_handler = setup_logging(tui_active=True)  # creates LOG_DIR itself
    state = State(mode=mode)
    control = Control()
    result_container: dict[str, Any] = {"return_value": None, "exception": None}
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
              if any(k in str(worker_kwargs) for k in ('cookies_from_browser',)) else ''};"
              f" full log: {path}", file=sys.stderr)
        return 1
    finally:
        control.request_stop()
        _join_worker(thread, _latest_log_path())
        # Terminal palette back to defaults AFTER curses has ended, even on
        # a UI crash (curses.wrapper already restored the terminal itself).
        tt.restore_palette()
    if result_container["exception"]:
        raise result_container["exception"]
    return result_container["return_value"] if result_container["return_value"] is not None else 0


def _latest_log_path() -> str:
    import glob

    paths = sorted(glob.glob(str(LOG_DIR / "ytm-offline-*.log")))
    return paths[-1] if paths else "logs/"


def supports_tui() -> bool:
    return sys.stdout.isatty() and sys.stdin.isatty()


# --- demo worker (no network, no files, no yt-dlp) ---------------------------


def _demo_worker(event_callback=None, control=None, seconds: float = 60.0,
                 seed: int = 7) -> None:
    """tui-demo: simulated batch through the same State/Event path."""
    rng = random.Random(seed)
    artists = ("Demo Artist A", "Demo Artist B", "The Demo Bros")
    albums = ("Demo Album I", "Demo Album II")
    total = 40
    titles = tuple(f"Demo Track {i + 1:02d}" for i in range(total))
    sizes = [rng.randint(3, 12) * 1024 * 1024 for _ in range(total)]

    def emit(ev_type: str, payload: dict | None = None) -> None:
        if event_callback:
            event_callback({"type": ev_type, "payload": payload or {}})

    def stopped() -> bool:
        if control and control.should_stop():
            emit("stopped", {"reason": "demo stopped"})
            return True
        return False

    emit("start", {"total": total, "total_est_bytes": sum(sizes),
                   "max_size_bytes": max(sizes)})
    budget = max(0.8, seconds / 44)
    ticks = 8
    for i in range(total):
        if stopped():
            return
        title = f"Demo Track {i + 1:02d}"
        emit("log", {"message": f"demo: resolving {title}"})
        emit("track_start", {"video_id": f"demo{i:03d}", "title": title,
                             "artist": rng.choice(artists), "album": rng.choice(albums),
                             "source": "artist:Demo", "stage": "resolving"})
        if i == 8 and control:
            # One pause/resume window so PAUSED visuals get some screen time.
            control.pause()
            emit("status", {"status": "PAUSED"})
            time.sleep(2.0)
            control.resume()
            emit("status", {"status": "RUNNING"})
        speed_mb = rng.uniform(4, 12)
        size = sizes[i]
        for tick in range(1, ticks + 1):
            if control and control.should_skip():
                emit("log", {"message": "demo: skipped track"})
                break
            time.sleep(budget / ticks)
            emit("track_progress", {
                "stage": "downloading",
                "percent": min(100.0, tick / ticks * 100),
                "downloaded_bytes": int(size * tick / ticks),
                "total_bytes": size,
                "speed": f"{speed_mb:.1f} MiB/s",
                "eta_seconds": int((ticks - tick) * budget / ticks),
            })
        if i in (20, 21):
            emit("track_done", {"status": "failed", "video_id": f"demo{i:03d}",
                                "title": title, "error": "demo: simulated failure (2/3)",
                                "retryable": True, "bytes": 0})
        else:
            emit("track_done", {"status": "downloaded", "video_id": f"demo{i:03d}",
                                "title": title, "bytes": size, "error": None,
                                "retryable": False})
        emit("log", {"message": f"demo: finished {title}"})
        nxt = titles[i + 1] if i + 1 < total else ""
        for rem in (0.5, 0.4, 0.3, 0.2, 0.1):
            if stopped():
                return
            emit("delay", {"remaining": rem, "finished": title, "next": nxt})
            time.sleep(0.05)
    emit("done", {"reason": "demo finished"})
