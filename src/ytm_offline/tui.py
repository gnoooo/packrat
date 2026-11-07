"""Curses TUI for ytm_offline downloads."""

from __future__ import annotations

import curses
import os
import sys
import threading
import time
import traceback
from typing import Any, Callable

from ytm_offline.control import Control
from ytm_offline.events import Event, State
from ytm_offline.logging_config import RingBufferHandler, redact, setup_logging


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
    if width <= 0:
        return ""
    percent = max(0.0, min(100.0, percent))
    filled = int(width * percent / 100)
    if ascii_mode:
        return "[" + "#" * filled + "-" * (width - filled) + "]"
    # Unicode block characters.
    frac = (width * percent / 100) - filled
    if frac > 0.66 and filled < width:
        mid = "\u2588"
    elif frac > 0.33 and filled < width:
        mid = "\u2584"
    else:
        mid = ""
    bar = "\u2588" * filled + mid
    # Pad with light shade.
    pad = width - len(bar)
    if pad > 0:
        bar += "\u2591" * pad
    return bar


def truncate(text: str, width: int) -> str:
    if width <= 0:
        return ""
    text = redact(str(text))
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 1] + "\u2026"


def _safe_addstr(win, y: int, x: int, text: str, attr: int = 0) -> None:
    try:
        win.addstr(y, x, text, attr)
    except curses.error:
        pass


def _draw_header(
    win,
    state: State,
    ascii_mode: bool,
    colors: dict[str, int],
) -> None:
    h, w = win.getmaxyx()
    mode = truncate(state.mode, 30)
    status = state.status
    status_color = colors.get("cyan", 0)
    if status == "DONE":
        status_color = colors.get("green", 0)
    elif status in ("STOPPED", "ABORTED"):
        status_color = colors.get("red", 0)
    elif status.startswith("WAITING"):
        status_color = colors.get("yellow", 0)
    elapsed = format_duration(state.elapsed_seconds)
    line = f" ytm-offline  |  {mode}  |  {status}  |  elapsed {elapsed}"
    _safe_addstr(win, 0, 0, truncate(line, w - 1).ljust(w - 1), status_color | curses.A_BOLD)


def _draw_overall(
    win,
    state: State,
    ascii_mode: bool,
    colors: dict[str, int],
) -> None:
    h, w = win.getmaxyx()
    if state.total <= 0:
        _safe_addstr(win, 0, 0, " overall: resolving tracks...", colors.get("cyan", 0))
        return
    pct = 100 * state.done / state.total if state.total else 0
    bar_w = max(10, w - 40)
    bar = render_bar(bar_w, pct, ascii_mode)
    eta = state.eta_seconds()
    eta_str = format_duration(eta) if eta is not None else "--:--"
    line = f" {state.done}/{state.total} {bar} {pct:5.1f}%  ETA {eta_str}"
    _safe_addstr(win, 0, 0, truncate(line, w - 1), colors.get("cyan", 0))

    size_line = (
        f" downloaded {format_bytes(state.total_bytes_downloaded)}"
        f" / est {format_bytes(state.total_est_bytes)}"
    )
    if state.max_size_bytes:
        size_line += f" / max {format_bytes(state.max_size_bytes)}"
    _safe_addstr(win, 1, 0, truncate(size_line, w - 1), colors.get("cyan", 0))


def _draw_current(
    win,
    state: State,
    ascii_mode: bool,
    colors: dict[str, int],
) -> None:
    h, w = win.getmaxyx()
    cur = state.current
    if not cur.video_id:
        _safe_addstr(win, 0, 0, " current: idle".ljust(w - 1), colors.get("cyan", 0))
        for y in range(1, h - 1):
            _safe_addstr(win, y, 0, " " * (w - 1))
        return

    title = truncate(cur.title, w - 12)
    artist = truncate(cur.artist, w - 12)
    album = truncate(cur.album, w - 12)
    source = truncate(cur.source, w - 12)
    stage = cur.stage or "resolving"
    _safe_addstr(win, 0, 0, f" {title}", curses.A_BOLD)
    _safe_addstr(win, 1, 0, f" {artist} - {album}")
    _safe_addstr(win, 2, 0, f" source: {source}  |  stage: {stage}")

    pct = cur.percent or 0.0
    bar_w = max(10, w - 40)
    bar = render_bar(bar_w, pct, ascii_mode)
    speed = cur.speed or ""
    eta = format_duration(cur.eta_seconds) if cur.eta_seconds is not None else "--:--"
    line = f" {pct:5.1f}% {bar} {speed}  ETA {eta}"
    _safe_addstr(win, 3, 0, truncate(line, w - 1), colors.get("green", 0))

    if cur.total_bytes:
        size_line = f" {format_bytes(cur.downloaded_bytes)} / {format_bytes(cur.total_bytes)}"
        _safe_addstr(win, 4, 0, truncate(size_line, w - 1))


def _draw_counters(
    win,
    state: State,
    colors: dict[str, int],
) -> None:
    h, w = win.getmaxyx()
    consec = state.consecutive_retryable
    consec_color = colors.get("green", 0)
    if consec == 2:
        consec_color = colors.get("yellow", 0)
    elif consec >= 3:
        consec_color = colors.get("red", 0)

    line1 = (
        f" downloaded {state.downloaded}"
        f"  archived {state.archived}"
        f"  skipped-size {state.skipped_size}"
        f"  failed {state.failed}"
        f"  retries {state.retries}"
    )
    _safe_addstr(win, 0, 0, truncate(line1, w - 1))
    line2 = f" consecutive retryable errors: {consec}/3"
    _safe_addstr(win, 1, 0, truncate(line2, w - 1), consec_color | curses.A_BOLD)


def _draw_failures(
    win,
    state: State,
    colors: dict[str, int],
) -> None:
    h, w = win.getmaxyx()
    _safe_addstr(win, 0, 0, " recent failures".ljust(w - 1), curses.A_UNDERLINE)
    y = 1
    for fail in list(state.recent_failures)[-max(1, h - 2) :]:
        if y >= h - 1:
            break
        text = f" {truncate(fail.get('title', ''), w // 2)}: {truncate(fail.get('reason', ''), w // 2 - 4)}"
        _safe_addstr(win, y, 0, truncate(text, w - 1), colors.get("red", 0))
        y += 1


def _draw_log(
    win,
    state: State,
    ring_handler: RingBufferHandler | None,
    log_offset: int,
    colors: dict[str, int],
) -> None:
    h, w = win.getmaxyx()
    _safe_addstr(win, 0, 0, " log".ljust(w - 1), curses.A_UNDERLINE)
    lines = list(state.log_lines)
    if ring_handler is not None:
        try:
            lines = ring_handler.lines(500)
        except Exception:
            pass
    if log_offset:
        lines = lines[-(len(lines) - log_offset) :]
    visible = h - 2
    for i, line in enumerate(lines[-visible:]):
        y = 1 + i
        if y >= h - 1:
            break
        _safe_addstr(win, y, 0, truncate(redact(line), w - 1))


def _draw_footer(win, show_log: bool, colors: dict[str, int]) -> None:
    h, w = win.getmaxyx()
    help_text = " p pause/resume  s skip  q quit  l log  ? help "
    _safe_addstr(win, 0, 0, help_text.ljust(w - 1), curses.A_REVERSE)


def _layout(stdscr, show_log: bool) -> tuple[Any, Any, Any, Any, Any, Any]:
    h, w = stdscr.getmaxyx()
    # Header 1, overall 2, current 5, counters 2, failures variable, log variable, footer 1.
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


def _draw_all(
    stdscr,
    state: State,
    control: Control,
    ring_handler: RingBufferHandler | None,
    colors: dict[str, int],
    ascii_mode: bool,
    show_log: bool,
    log_offset: int,
) -> None:
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    if h < 12 or w < 40:
        _safe_addstr(stdscr, 0, 0, "terminal too small")
        stdscr.refresh()
        return

    layout = _layout(stdscr, show_log)
    if layout[0] is None:
        _safe_addstr(stdscr, 0, 0, "terminal too small for layout")
        stdscr.refresh()
        return

    header, overall, current, counters, failures, log, footer = layout
    _draw_header(header, state, ascii_mode, colors)
    _draw_overall(overall, state, ascii_mode, colors)
    _draw_current(current, state, ascii_mode, colors)
    _draw_counters(counters, state, colors)
    _draw_failures(failures, state, colors)
    if show_log and log:
        _draw_log(log, state, ring_handler, log_offset, colors)
    _draw_footer(footer, show_log, colors)

    stdscr.refresh()


def _init_colors() -> dict[str, int]:
    colors: dict[str, int] = {}
    if not curses.has_colors():
        return colors
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN, -1)
    curses.init_pair(2, curses.COLOR_YELLOW, -1)
    curses.init_pair(3, curses.COLOR_RED, -1)
    curses.init_pair(4, curses.COLOR_CYAN, -1)
    colors["green"] = curses.color_pair(1)
    colors["yellow"] = curses.color_pair(2)
    colors["red"] = curses.color_pair(3)
    colors["cyan"] = curses.color_pair(4)
    return colors


def _handle_keys(
    stdscr,
    control: Control,
    state: State,
    show_log: bool,
    log_offset: int,
) -> tuple[bool, bool, int]:
    """Return (continue_running, show_log, log_offset)."""
    try:
        key = stdscr.getch()
    except curses.error:
        return True, show_log, log_offset

    if key == -1:
        return True, show_log, log_offset

    if key in (ord("q"), ord("Q")):
        control.request_stop()
        state.apply(Event("status", {"status": "STOPPING", "reason": "user quit"}))
        return True, show_log, log_offset
    if key in (ord("p"), ord("P"), ord(" ")):
        paused = control.toggle_pause()
        status = "PAUSED" if paused else "RUNNING"
        state.apply(Event("status", {"status": status}))
    elif key in (ord("s"), ord("S")):
        control.request_skip()
    elif key in (ord("l"), ord("L")):
        show_log = not show_log
        log_offset = 0
    elif key in (ord("?"),):
        # Help toggle handled by showing footer; could show overlay later.
        pass
    elif key == curses.KEY_UP:
        log_offset = max(0, log_offset - 1)
    elif key == curses.KEY_DOWN:
        log_offset += 1
    elif key == curses.KEY_PPAGE:
        log_offset = max(0, log_offset - 10)
    elif key == curses.KEY_NPAGE:
        log_offset += 10
    elif key == curses.KEY_RESIZE:
        pass

    return True, show_log, log_offset


def _ui(
    stdscr,
    state: State,
    control: Control,
    ring_handler: RingBufferHandler | None,
    ascii_mode: bool,
) -> None:
    stdscr.nodelay(True)
    stdscr.timeout(100)
    curses.curs_set(0)
    colors = _init_colors()
    show_log = False
    log_offset = 0

    while True:
        _draw_all(stdscr, state, control, ring_handler, colors, ascii_mode, show_log, log_offset)
        cont, show_log, log_offset = _handle_keys(stdscr, control, state, show_log, log_offset)
        if not cont:
            break
        if state.status in ("DONE", "STOPPED", "ABORTED"):
            # Keep rendering briefly then wait for q.
            stdscr.timeout(1000)
            key = stdscr.getch()
            if key in (ord("q"), ord("Q"), 27):
                break
            if key == curses.KEY_RESIZE:
                continue
        time.sleep(0.05)


def _worker_target(
    worker_func: Callable[..., Any],
    worker_kwargs: dict[str, Any],
    state: State,
    control: Control,
    result_container: dict[str, Any],
) -> None:
    def event_callback(event_dict: dict[str, Any]) -> None:
        event = Event(event_dict.get("type", "log"), event_dict.get("payload", {}))
        state.apply(event)

    kwargs = dict(worker_kwargs)
    kwargs["event_callback"] = event_callback
    kwargs["control"] = control
    try:
        result_container["return_value"] = worker_func(**kwargs)
    except Exception as exc:
        reason = f"worker crashed: {exc}"
        state.apply(Event("stopped", {"reason": reason}))
        result_container["exception"] = exc
        result_container["traceback"] = traceback.format_exc()
    else:
        if state.status not in ("STOPPED", "ABORTED"):
            state.apply(Event("done", {"reason": "finished"}))


def run_tui(
    worker_func: Callable[..., Any],
    worker_kwargs: dict[str, Any],
    mode: str = "",
    ascii_mode: bool = False,
) -> Any:
    """Run a download worker with the curses TUI. Falls back to plain logging if not a TTY."""
    if not sys.stdout.isatty():
        print("stdout is not a TTY; falling back to plain logging", file=sys.stderr)
        return worker_func(**worker_kwargs)

    # Ensure log directory exists.
    os.makedirs("logs", exist_ok=True)
    ring_handler = setup_logging(tui_active=True)
    state = State(mode=mode)
    control = Control()
    result_container: dict[str, Any] = {}

    worker = threading.Thread(
        target=_worker_target,
        args=(worker_func, worker_kwargs, state, control, result_container),
        daemon=True,
    )
    worker.start()

    try:
        curses.wrapper(_ui, state, control, ring_handler, ascii_mode)
    except KeyboardInterrupt:
        control.request_stop()
        state.apply(Event("status", {"status": "ABORTED", "reason": "Ctrl-C"}))
    finally:
        control.request_stop()
        worker.join(timeout=5.0)

    if result_container.get("exception"):
        raise result_container["exception"]
    return result_container.get("return_value", 1)


def supports_tui() -> bool:
    return sys.stdout.isatty()
