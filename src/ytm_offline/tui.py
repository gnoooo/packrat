"""Curses TUI for ytm_offline downloads — themed, animated renderer.

Render layer only: the downloader/pipeline, events/State schema and Control
semantics are untouched. Colors come from tui_theme, glyphs from tui_glyphs.
"""

from __future__ import annotations

import curses
import os
import random
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
from ytm_offline.logging_config import RingBufferHandler, redact, setup_logging

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


def truncate(text: str, width: int) -> str:
    """Redact and cut to a display width, ellipsis when it fits."""
    text = redact(str(text))
    if width <= 3:
        return text[: width]
    if tg.display_width(text) <= width:
        return text
    return tg.clip_width(text, width - 1) + "…"


# --- visual options (CLI-wide flag set once in main()) ----------------------

_visual_state = {"theme": "ocean", "no_color": False}


def set_visual_options(theme: str = "ocean", no_color: bool = False) -> None:
    """Single place the CLI passes its visual flags; run_tui call sites unchanged."""
    if theme in tt.THEMES:
        _visual_state["theme"] = theme
    _visual_state["no_color"] = bool(no_color)


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

    def __init__(self, glyphs, pal, colors, attr, no_anim: bool, tier: str) -> None:
        self.g = glyphs
        self.pal = pal
        self.colors = colors
        self.attr = attr
        self.no_anim = no_anim
        self.tier = tier

    def wave_attr(self, x: float, phase: float) -> int:
        idx, bold = self.pal.attr_for_intensity(tt.wave_intensity(x, phase))
        return self.attr(idx, bold) if idx >= 0 else 0

    def breathe_attr(self, breath: float) -> int:
        idx, bold = self.pal.attr_for_intensity(breath)
        return self.attr(idx, bold) if idx >= 0 else 0


def _build_visual(stdscr, theme_name: str, ascii_mode: bool, no_anim: bool) -> Visual:
    import curses as _cur

    glyphs = tg.pick_set(force_ascii=ascii_mode)
    has_colors = _cur.has_colors()
    no_color = tt.no_color_requested() or _visual_state.get("no_color")
    tier = tt.select_tier(has_colors, _cur.COLORS if has_colors else 0, no_color)
    theme = tt.THEMES.get(theme_name, tt.THEMES["ocean"])
    pal = tt.Palette(theme, tier)
    colors: dict[str, int] = {k: 0 for k in tt.COLOR_KEYS}

    def plain_attr(idx: int, bold: bool = False) -> int:
        return _cur.A_BOLD if bold else 0

    attr: Callable[[int, bool], int] = plain_attr
    if tier != tt.TIER_NONE:
        try:
            _cur.start_color()
            pairs = tt.activate(pal)

            def themed_attr(idx: int, bold: bool = False, _pairs: dict = pairs) -> int:
                a = _cur.color_pair(_pairs.get(idx, 0))
                return a | _cur.A_BOLD if bold else a

            attr = themed_attr
            colors = {k: (attr(idx) if idx >= 0 else 0) for k, idx in pal.static.items()}
        except _cur.error:
            attr = plain_attr
            colors = {k: 0 for k in tt.COLOR_KEYS}
    return Visual(glyphs, pal, colors, attr, no_anim, tier)


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


def _draw_themed_line(win, y: int, prefix: str, body: str, suffix: str, vis: Visual,
                      mode: str, phase: float, breath: float, sweep: float | None,
                      base_attr: int = 0, thin: bool = False) -> None:
    """prefix + per-cell colored body + suffix; bar cells get the wave."""
    if vis.tier == tt.TIER_NONE or not body:
        _safe_addstr(win, y, 0, prefix + body + suffix, base_attr)
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


def _draw_header(win, snap: State, vis: Visual) -> None:
    status = snap.status
    colors = vis.colors
    if status == "DONE":
        sattr = colors.get("success", 0)
    elif status in ("STOPPED", "ABORTED"):
        sattr = colors.get("error", 0)
    elif status == "STOPPING":
        sattr = colors.get("stopping", 0)
    elif status.startswith("WAITING"):
        sattr = colors.get("warning", 0)
    else:
        sattr = colors.get("info", 0)
    line = f" ytm-offline  |  {snap.mode[:30]}  |  {status}  |  elapsed {format_duration(snap.elapsed_seconds)}"
    _safe_addstr(win, 0, 0, line, curses.A_BOLD)
    pos = line.find(status)
    if pos >= 0:
        _safe_addstr(win, 0, pos, status, sattr | curses.A_BOLD)


def _draw_overall(win, snap: State, vis: Visual, frame: dict) -> None:
    h, w = win.getmaxyx()
    if snap.total <= 0:
        _safe_addstr(win, 0, 0, " overall: resolving tracks...", curses.A_BOLD)
        return
    bar_w = max(10, w - 40)
    pct = snap.done / snap.total * 100
    eta = snap.eta_seconds()
    eta_s = "…" if eta is None else format_duration(eta)
    prefix = f" {snap.done}/{snap.total} "
    body = tg.bar(vis.g, bar_w, pct)
    suffix = f" {pct:5.1f}%  ETA {eta_s}"
    _draw_themed_line(win, 0, prefix, body, suffix, vis, frame["mode"], frame["phase"],
                      frame["breath"], frame.get("sweep"), curses.A_BOLD)
    base = f" downloaded {format_bytes(snap.total_bytes_downloaded)} / est {format_bytes(snap.total_est_bytes)}"
    if snap.max_size_bytes:
        base += f" / max {format_bytes(snap.max_size_bytes)}"
    line = base
    avail = w - 1 - tg.display_width(base) - 2
    if avail >= 8:
        rates = _spark_rates(frame["history"])
        line += "  " + tg.sparkline(vis.g, rates, min(30, avail))
    _safe_addstr(win, 1, 0, line, vis.colors.get("info", 0))


def _draw_current(win, snap: State, vis: Visual, frame: dict) -> None:
    h, w = win.getmaxyx()
    cur = snap.current
    _safe_addstr(win, 0, 0, tg.panel_top(vis.g, w, "now playing"),
                 vis.colors.get("info", 0) | curses.A_BOLD)
    if not cur.video_id:
        return
    _safe_addstr(win, 1, 0, f" {truncate(cur.title, w - 4)}", curses.A_BOLD)
    _safe_addstr(win, 2, 0, f" {truncate(cur.artist + ' - ' + cur.album, w - 4)}", 0)
    _safe_addstr(win, 3, 0,
                 f" source: {truncate(cur.source, max(8, w // 3))}  |  stage: {cur.stage}", 0)
    bar_w = max(10, w - 40)
    pct = max(0.0, min(100.0, cur.percent))
    eta = "…" if cur.eta_seconds is None else format_duration(cur.eta_seconds)
    suffix = f" {cur.speed}  ETA {eta}" if cur.speed else f" ETA {eta}"
    if cur.total_bytes:
        suffix += f"  {format_bytes(cur.downloaded_bytes)}/{format_bytes(cur.total_bytes)}"
    _draw_themed_line(win, 4, f" {pct:5.1f}% ", tg.thin_bar(vis.g, bar_w, pct), suffix,
                      vis, frame["mode"], frame["thin_phase"], frame["breath"],
                      frame.get("sweep"), curses.A_BOLD, thin=True)


def _draw_counters(win, snap: State, vis: Visual, frame: dict) -> None:
    h, w = win.getmaxyx()
    line = f" downloaded {snap.downloaded}  archived {snap.archived}  skipped-size {snap.skipped_size}  failed {snap.failed}  retries {snap.retries}"
    attr0 = (vis.colors.get("error", 0) | curses.A_BOLD) if frame.get("flash") else 0
    _safe_addstr(win, 0, 0, line, attr0)
    c = snap.consecutive_retryable
    if c >= 3:
        attr1 = vis.colors.get("error", 0) | curses.A_BOLD
    elif c == 2:
        attr1 = vis.colors.get("warning", 0) | curses.A_BOLD
    else:
        attr1 = vis.colors.get("success", 0)
    _safe_addstr(win, 1, 0, f" consecutive retryable errors: {c}/3", attr1)


def _draw_failures(win, snap: State, vis: Visual, frame: dict) -> None:
    h, w = win.getmaxyx()
    heading = (vis.colors.get("error", 0) | curses.A_BOLD) if frame.get("flash") else curses.A_BOLD
    _safe_addstr(win, 0, 0, tg.panel_top(vis.g, w, "recent failures"), heading)
    y = 1
    half = max(4, (w - 2) // 2)
    for fail in reversed(list(snap.recent_failures)[-(max(0, h - 2)):]):
        title = truncate(str(fail.get("title", "")), half)
        reason = truncate(str(fail.get("reason", "")), max(4, half - 4))
        _safe_addstr(win, y, 0, f" {title}: {reason}", vis.colors.get("error", 0))
        y += 1
        if y >= h:
            break


def _draw_log(win, snap: State, vis: Visual, ring_handler, log_offset: int) -> None:
    h, w = win.getmaxyx()
    _safe_addstr(win, 0, 0, tg.panel_top(vis.g, w, "log"), curses.A_BOLD)
    lines: list[str] = []
    if ring_handler is not None:
        try:
            lines = list(ring_handler.lines(500))
        except Exception:
            lines = []
    if not lines:
        lines = list(snap.log_lines)
    if log_offset and log_offset < len(lines):
        lines = lines[: len(lines) - log_offset]
    elif log_offset >= len(lines):
        lines = []
    visible = max(0, h - 2)
    for i, line in enumerate(lines[-visible:]):
        _safe_addstr(win, i + 1, 0, " " + truncate(line, w - 3), 0)


def _draw_footer(win, vis: Visual) -> None:
    h, w = win.getmaxyx()
    help_text = " p pause/resume  s skip  q quit  l log  a anim  t theme  ? help "
    _safe_addstr(win, 0, 0, help_text.ljust(max(0, w - 1)), curses.A_REVERSE)


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


def _layout(stdscr, show_log: bool):
    h, w = stdscr.getmaxyx()
    header_h = 1
    overall_h = 2
    current_h = 5
    counters_h = 2
    footer_h = 1
    remaining = h - (header_h + overall_h + current_h + counters_h + footer_h)
    if remaining < 6:
        # Too small; just draw header + footer and a warning.
        return None, None, None, None, None, None

    if show_log:
        failures_h = max(3, remaining // 4)
        log_h = remaining - failures_h
    else:
        failures_h = remaining
        log_h = 0

    y = 0
    header = stdscr.derwin(header_h, w, y, 0)
    y += header_h
    overall = stdscr.derwin(overall_h, w, y, 0)
    y += overall_h
    current = stdscr.derwin(current_h, w, y, 0)
    y += current_h
    counters = stdscr.derwin(counters_h, w, y, 0)
    y += counters_h
    failures = stdscr.derwin(failures_h, w, y, 0)
    y += failures_h
    log = stdscr.derwin(log_h, w, y, 0)
    y += log_h
    footer = stdscr.derwin(footer_h, w, y, 0)
    return header, overall, current, counters, failures, log, footer


def _draw_all(stdscr, snap: State, vis: Visual, ring_handler, show_log: bool,
              log_offset: int, help_mode: bool, frame: dict) -> None:
    h, w = stdscr.getmaxyx()
    stdscr.erase()
    if h < 12 or w < 40:
        _safe_addstr(stdscr, 0, 0, "terminal too small", curses.A_BOLD)
        stdscr.noutrefresh()
        curses.doupdate()
        return
    header, overall, current, counters, failures, log, footer = _layout(stdscr, show_log)
    if header is None:
        _safe_addstr(stdscr, 0, 0, "terminal too small for layout", curses.A_BOLD)
        stdscr.noutrefresh()
        curses.doupdate()
        return
    _draw_header(header, snap, vis)
    _draw_overall(overall, snap, vis, frame)
    _draw_current(current, snap, vis, frame)
    _draw_counters(counters, snap, vis, frame)
    _draw_failures(failures, snap, vis, frame)
    if show_log and log:
        _draw_log(log, snap, vis, ring_handler, log_offset)
    _draw_footer(footer, vis)
    for win in (header, overall, current, counters, failures, footer):
        win.noutrefresh()
    if show_log and log:
        log.noutrefresh()
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
        stdscr.timeout(100 if animating else 250)
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


def run_tui(worker_func: Callable, worker_kwargs: dict, mode: str = "",
            ascii_mode: bool = False, no_anim: bool = False, no_color: bool = False) -> int:
    """Run worker under the TUI; plain logging fallback when stdout is no TTY."""
    if not supports_tui():
        print("stdout is not a TTY; falling back to plain logging", file=sys.stderr)
        return worker_func(**worker_kwargs)
    os.makedirs("logs", exist_ok=True)
    ring_handler = setup_logging(tui_active=True)
    state = State(mode=mode)
    control = Control()
    result_container: dict[str, Any] = {"return_value": None, "exception": None}
    thread = threading.Thread(
        target=_worker_target,
        args=(worker_func, worker_kwargs, state, control, result_container),
        daemon=True,
    )
    thread.start()
    try:
        curses.wrapper(_ui, state, control, ring_handler, ascii_mode, True if no_anim else None)
    except KeyboardInterrupt:
        control.request_stop()
        state.apply(Event("status", {"status": "ABORTED", "reason": "Ctrl-C"}))
    except Exception as exc:  # UI bug: stop the worker cleanly, no traceback
        # The worker finishes its current track, writes archive/manifest/
        # m3u8 and unwinds (deleting any temp cookie file) before exiting.
        control.request_stop()
        thread.join(5)
        path = _latest_log_path()
        print(f"TUI crashed: {exc.__class__.__name__}: {exc}",
              file=sys.stderr)
        print(f"download stopped cleanly{' (cookie temporary files deleted)'
              if any(k in str(worker_kwargs) for k in ('cookies_from_browser',)) else ''};"
              f" full log: {path}", file=sys.stderr)
        return 1
    finally:
        control.request_stop()
        thread.join(5)
    if result_container["exception"]:
        raise result_container["exception"]
    return result_container["return_value"] if result_container["return_value"] is not None else 0


def _latest_log_path() -> str:
    import glob
    paths = sorted(glob.glob("logs/ytm-offline-*.log"))
    return paths[-1] if paths else "logs/"


def supports_tui() -> bool:
    return sys.stdout.isatty()


# --- demo worker (no network, no files, no yt-dlp) ---------------------------


def _demo_worker(event_callback=None, control=None, seconds: float = 60.0,
                 seed: int = 7) -> None:
    """tui-demo: simulated batch through the same State/Event path."""
    rng = random.Random(seed)
    artists = ("Demo Artist A", "Demo Artist B", "The Demo Bros")
    albums = ("Demo Album I", "Demo Album II")
    total = 40
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
        for rem in (0.5, 0.4, 0.3, 0.2, 0.1):
            if stopped():
                return
            emit("delay", {"remaining": rem})
            time.sleep(0.05)
    emit("done", {"reason": "demo finished"})
