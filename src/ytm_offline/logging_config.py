"""Logging setup: file + ring buffer, with redaction."""

from __future__ import annotations

import collections
import logging
import pathlib
import re
from datetime import datetime, timezone
from typing import Any


class RedactFilter(logging.Filter):
    """Mask auth tokens from log records."""

    _PATTERNS = [
        (re.compile(r"(?i)(Cookie:\s*)[^\s\r\n]+"), r"\1<redacted>"),
        (re.compile(r"(?i)(Authorization:\s*)[^\s\r\n]+"), r"\1<redacted>"),
        (re.compile(r"(?i)(SAPISID\s*=\s*)[^\s;,&]+"), r"\1<redacted>"),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(record.getMessage())
        record.args = ()  # already interpolated; avoids msg % args double-formatting
        return True

    @classmethod
    def redact(cls, text: Any) -> str:
        s = str(text)
        for pat, repl in cls._PATTERNS:
            s = pat.sub(repl, s)
        return s


class RingBufferHandler(logging.Handler):
    """Keep the last N redacted log lines in memory."""

    def __init__(self, capacity: int = 200) -> None:
        super().__init__()
        self._buffer: collections.deque[str] = collections.deque(maxlen=capacity)
        self._lock = collections.deque  # not used; buffer is thread-safe for append

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
            self._buffer.append(line)
        except Exception:
            self.handleError(record)

    def lines(self, n: int | None = None) -> list[str]:
        if n is None:
            return list(self._buffer)
        return list(self._buffer)[-n:]


def redact(text: Any) -> str:
    return RedactFilter.redact(text)


def setup_logging(
    tui_active: bool,
    log_dir: pathlib.Path | None = None,
    ring_capacity: int = 200,
) -> RingBufferHandler | None:
    """Configure root logging.

    - Always logs to a timestamped file in logs/.
    - In TUI mode, also keeps a redacted ring buffer and removes console output.
    - Otherwise, also echoes to stderr.
    """
    if log_dir is None:
        log_dir = pathlib.Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    log_file = log_dir / f"ytm-offline-{ts}.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    # Remove existing handlers to avoid duplicates.
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s")
    redact_filter = RedactFilter()

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    file_handler.addFilter(redact_filter)
    root.addHandler(file_handler)

    ring_handler: RingBufferHandler | None = None
    if tui_active:
        ring_handler = RingBufferHandler(capacity=ring_capacity)
        ring_handler.setLevel(logging.INFO)
        ring_handler.setFormatter(logging.Formatter("%(levelname)-8s %(message)s"))
        ring_handler.addFilter(redact_filter)
        root.addHandler(ring_handler)
    else:
        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        console.setFormatter(fmt)
        console.addFilter(redact_filter)
        root.addHandler(console)

    logging.info("logging initialized: %s", log_file)
    return ring_handler
