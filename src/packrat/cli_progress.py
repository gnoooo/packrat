"""One-line ANSI progress for headless downloads.

The TUI owns its curses renderer.  This module mirrors its gradient/glyph
vocabulary for normal terminal commands without pulling curses into the CLI.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Any, TextIO

from packrat import tui_glyphs as tg
from packrat import tui_theme as tt


class ConsoleProgress:
    """Render a compact TUI-style bar on a TTY; stay silent otherwise."""

    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        theme: str = "ocean",
        no_color: bool | None = None,
        ascii_mode: bool = False,
        width: int | None = None,
    ) -> None:
        self.stream = stream if stream is not None else sys.stdout
        self.enabled = bool(getattr(self.stream, "isatty", lambda: False)())
        if os.environ.get("PACKRAT_NO_PROGRESS"):
            self.enabled = False
        self.no_color = (
            bool(no_color) if no_color is not None else bool(os.environ.get("NO_COLOR"))
        )
        self.ascii_mode = ascii_mode
        self.theme = tt.THEMES.get(theme, tt.THEMES["ocean"])
        self.glyphs = tg.pick_set(ascii_mode, self.stream)
        self.width = width
        self.total = 0
        self.done = 0
        self.title = ""
        self.subtitle = ""
        self._last_len = 0
        self._gradient = tt.theme_gradient_rgb(self.theme, 64)
        self._track = tt.parse_hex(self.theme.dim)

    # --- ANSI helpers -------------------------------------------------
    def _fg(self, rgb: tuple[int, int, int]) -> str:
        if self.no_color or self.ascii_mode:
            return ""
        return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"

    def _reset(self) -> str:
        return "\x1b[0m" if not (self.no_color or self.ascii_mode) else ""

    def _bar(self, percent: float, width: int) -> str:
        percent = max(0.0, min(100.0, percent))
        fill = str(self.glyphs.get("bar_fill", "#"))
        track = str(self.glyphs.get("bar_track_line", "-"))
        edge_chars = str(self.glyphs.get("bar_edge", ""))
        units = round(width * percent / 100 * 8)
        full, frac = divmod(units, 8)
        edge = edge_chars[frac - 1] if frac and edge_chars and full < width else ""
        full = min(full, width)
        rest = max(0, width - full - len(edge))
        cells: list[str] = []
        for i in range(full):
            pos = round(i / max(1, width - 1) * (len(self._gradient) - 1))
            cells.append(self._fg(self._gradient[pos]) + fill)
        if edge:
            cells.append(self._fg(self._gradient[-1]) + edge)
        cells.append(self._fg(self._track) + track * rest)
        return "".join(cells) + self._reset()

    def _write(self, line: str) -> None:
        if not self.enabled:
            return
        self.stream.write("\r" + line + "\x1b[K")
        self.stream.flush()
        self._last_len = 1

    def _clear(self) -> None:
        if self.enabled and self._last_len:
            self.stream.write("\r\x1b[K")
            self.stream.flush()
            self._last_len = 0

    def _columns(self) -> int:
        return self.width or shutil.get_terminal_size((80, 20)).columns

    def _line(self, percent: float, *, suffix: str = "", stage: str = "") -> str:
        """Build a single line that always fits the terminal width."""
        cols = max(20, self._columns())
        prefix = f"{self.done + 1}/{self.total} " if self.total else ""
        if stage:
            suffix = f" {stage}"
        if tg.display_width(prefix) + tg.display_width(suffix) + 12 > cols:
            suffix = tg.truncate_width(
                suffix, max(0, cols - tg.display_width(prefix) - 12)
            )
        fixed = tg.display_width(prefix) + tg.display_width(suffix) + 2
        title_w = max(0, min(34, cols - fixed - 10))
        title = tg.truncate_width(self.title, title_w)
        bar_w = max(6, min(48, cols - fixed - tg.display_width(title) - 1))
        if tg.display_width(prefix) + tg.display_width(title) + 1 + bar_w + tg.display_width(suffix) > cols:
            bar_w = max(4, bar_w - 1)
        return f"{prefix}{title} {self._bar(percent, bar_w)}{suffix}"

    def _format_speed(self, speed: Any) -> str:
        if isinstance(speed, (int, float)):
            return f"{speed / 1024 / 1024:.1f} MB/s"
        return str(speed or "")

    # --- public API ---------------------------------------------------
    def start(self, title: str = "", subtitle: str = "", total: int = 0) -> None:
        self.title = title
        self.subtitle = subtitle
        self.total = total
        self.done = 0
        if title:
            self.stream.write(title + "\n")
        if subtitle:
            self.stream.write(subtitle + "\n")
        self.stream.flush()

    def update(
        self,
        percent: float,
        *,
        speed: str = "",
        eta: int | None = None,
        stage: str = "",
    ) -> None:
        if not self.enabled:
            return
        suffix = f" {percent:3.0f}%"
        speed_text = self._format_speed(speed)
        if speed_text:
            suffix += f" {speed_text}"
        if eta is not None:
            suffix += f" ETA {eta}s"
        self._write(self._line(percent, suffix=suffix, stage=stage))

    def stage(self, text: str, percent: float = 0.0) -> None:
        if not self.enabled:
            return
        self._write(self._line(percent, stage=text))

    def converting(self) -> None:
        self.stage("converting", 100.0)

    def finish(self, lines: list[str] | None = None) -> None:
        self._clear()
        if lines:
            for line in lines:
                self.stream.write(line + "\n")
            self.stream.flush()

    def fail(self, message: str) -> None:
        self._clear()
        if self.enabled:
            self.stream.write(f"✗ {message}\n")
            self.stream.flush()

    # --- yt-dlp hook --------------------------------------------------
    def on_ytdlp(self, data: dict[str, Any]) -> None:
        status = data.get("status")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            downloaded = data.get("downloaded_bytes", 0)
            percent = (downloaded / total * 100) if total else 0.0
            speed = data.get("_speed_str") or data.get("speed") or ""
            eta = data.get("eta")
            self.update(
                percent,
                speed=self._format_speed(speed),
                eta=eta if isinstance(eta, int) else None,
            )
        elif status == "finished":
            self.converting()

    # --- batch event callback ----------------------------------------
    def __call__(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        payload = event.get("payload") or {}
        if kind == "start":
            self.start(total=payload.get("total", 0))
        elif kind == "track_start":
            self.title = str(payload.get("title") or "")
            self.subtitle = str(payload.get("artist") or "")
            self.stage(str(payload.get("stage") or "resolving"))
        elif kind == "track_progress":
            stage = str(payload.get("stage") or "")
            if stage == "converting":
                self.converting()
            elif stage.startswith("retry") or stage == "resolving":
                self.stage(stage)
            else:
                self.update(
                    float(payload.get("percent") or 0),
                    speed=payload.get("speed") or "",
                    eta=payload.get("eta_seconds"),
                )
        elif kind == "delay":
            if payload.get("paused"):
                return
            remaining = float(payload.get("remaining") or 0)
            suffix = f" waiting {remaining:.1f}s"
            nxt = str(payload.get("next") or "")
            if nxt:
                suffix += f" · next {nxt}"
            self._write(self._line(0.0, suffix=suffix))
        elif kind == "status":
            status = str(payload.get("status") or "")
            reason = str(payload.get("reason") or "")
            if status == "STOPPING":
                self._write(self._line(0.0, stage=f"stopping: {reason}"))
        elif kind == "track_done":
            self.done += 1
            self._clear()
            if payload.get("status") == "failed" and self.enabled:
                self.stream.write(
                    f"✗ {payload.get('title', '')}: {payload.get('error', 'failed')}\n"
                )
                self.stream.flush()
        elif kind in ("done", "stopped"):
            self.finish()


_configured = {"theme": "ocean", "no_color": None, "ascii_mode": False}


def configure(*, theme: str = "ocean", no_color: bool | None = None, ascii_mode: bool = False) -> None:
    _configured.update(theme=theme, no_color=no_color, ascii_mode=ascii_mode)


def console_progress(stream: TextIO | None = None) -> ConsoleProgress:
    return ConsoleProgress(
        stream,
        theme=_configured["theme"],
        no_color=_configured["no_color"],
        ascii_mode=_configured["ascii_mode"],
    )
