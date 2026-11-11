"""Shared state and events for the TUI worker thread."""

from __future__ import annotations

import collections
import dataclasses
import threading
import time
from typing import Any


@dataclasses.dataclass
class Event:
    """A single state update from the worker to the UI."""

    type: str
    payload: dict[str, Any] = dataclasses.field(default_factory=dict)
    ts: float = dataclasses.field(default_factory=time.time)


@dataclasses.dataclass
class CurrentTrack:
    video_id: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""
    source: str = ""
    stage: str = ""
    percent: float = 0.0
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    speed: str = ""
    eta_seconds: int | None = None


class State:
    """Thread-safe snapshot of the download run."""

    def __init__(self, mode: str = "", total: int = 0) -> None:
        self._lock = threading.RLock()
        self.mode = mode
        self.status = "RUNNING"
        self.total = total
        self.done = 0
        self.downloaded = 0
        self.archived = 0
        self.skipped_size = 0
        self.failed = 0
        self.retries = 0
        self.consecutive_retryable = 0
        self.start_ts = time.time()
        self.elapsed_seconds = 0
        self.total_bytes_downloaded = 0
        self.total_est_bytes = 0
        self.max_size_bytes: int | None = None
        self.current = CurrentTrack()
        self.last_finished: str = ""
        self.next_title: str = ""
        self.recent_failures: collections.deque[dict[str, Any]] = collections.deque(
            maxlen=20
        )
        self.log_lines: collections.deque[str] = collections.deque(maxlen=200)
        self._track_times: collections.deque[float] = collections.deque(maxlen=20)
        self._stop_reason: str | None = None

    def apply(self, event: Event) -> None:
        with self._lock:
            self.elapsed_seconds = time.time() - self.start_ts
            p = event.payload
            if event.type == "start":
                self.mode = p.get("mode", self.mode)
                self.total = p.get("total", self.total)
                self.total_est_bytes = p.get("total_est_bytes", self.total_est_bytes)
                self.max_size_bytes = p.get("max_size_bytes")
                self.status = "RUNNING"
            elif event.type == "status":
                self.status = p.get("status", self.status)
                if p.get("reason"):
                    self._stop_reason = p["reason"]
            elif event.type == "track_start":
                self.current = CurrentTrack(
                    video_id=p.get("video_id", ""),
                    title=p.get("title", ""),
                    artist=p.get("artist", ""),
                    album=p.get("album", ""),
                    source=p.get("source", ""),
                    stage=p.get("stage", "resolving"),
                )
            elif event.type == "track_progress":
                self.current.stage = p.get("stage", self.current.stage)
                self.current.percent = p.get("percent", self.current.percent)
                self.current.downloaded_bytes = p.get("downloaded_bytes", 0)
                self.current.total_bytes = p.get("total_bytes")
                self.current.speed = p.get("speed", "")
                self.current.eta_seconds = p.get("eta_seconds")
            elif event.type == "track_done":
                self.done += 1
                status = p.get("status", "")
                if status == "downloaded":
                    self.downloaded += 1
                    self.total_bytes_downloaded += p.get("bytes", 0)
                    self._track_times.append(time.time())
                    self.consecutive_retryable = 0
                elif status == "archived":
                    self.archived += 1
                    self.consecutive_retryable = 0
                elif status == "skipped_size":
                    self.skipped_size += 1
                    self.consecutive_retryable = 0
                elif status == "failed":
                    self.failed += 1
                    if p.get("retryable"):
                        self.consecutive_retryable += 1
                        self.retries += 1
                    else:
                        self.consecutive_retryable = 0
                    self.recent_failures.append(
                        {
                            "video_id": p.get("video_id", ""),
                            "title": p.get("title", ""),
                            "reason": p.get("error", ""),
                        }
                    )
                self.current = CurrentTrack()
            elif event.type == "delay":
                self.status = f"WAITING {p.get('remaining', 0):.1f}s"
                if p.get("finished") is not None:
                    self.last_finished = str(p["finished"])
                self.next_title = str(p.get("next", ""))
            elif event.type == "bytes":
                self.total_bytes_downloaded = p.get("total", self.total_bytes_downloaded)
            elif event.type == "log":
                self.log_lines.append(p.get("message", ""))
            elif event.type == "stopped":
                self.status = "STOPPED"
                self._stop_reason = p.get("reason")
            elif event.type == "done":
                self.status = "DONE"
                self._stop_reason = p.get("reason")

    def snapshot(self) -> "State":
        with self._lock:
            snap = State.__new__(State)
            snap.mode = self.mode
            snap.status = self.status
            snap.total = self.total
            snap.done = self.done
            snap.downloaded = self.downloaded
            snap.archived = self.archived
            snap.skipped_size = self.skipped_size
            snap.failed = self.failed
            snap.retries = self.retries
            snap.consecutive_retryable = self.consecutive_retryable
            snap.start_ts = self.start_ts
            snap.elapsed_seconds = time.time() - self.start_ts
            snap.total_bytes_downloaded = self.total_bytes_downloaded
            snap.total_est_bytes = self.total_est_bytes
            snap.max_size_bytes = self.max_size_bytes
            snap.current = dataclasses.replace(self.current)
            snap.last_finished = self.last_finished
            snap.next_title = self.next_title
            snap.recent_failures = collections.deque(self.recent_failures, maxlen=20)
            snap.log_lines = collections.deque(self.log_lines, maxlen=200)
            snap._track_times = collections.deque(self._track_times, maxlen=20)
            snap._stop_reason = self._stop_reason
            return snap

    def recent_track_rate(self) -> float:
        """Tracks per second over the last 20 completed downloads."""
        lock = getattr(self, "_lock", None)
        if lock is None:
            return _track_rate(self._track_times)
        with lock:
            return _track_rate(self._track_times)

    def eta_seconds(self) -> int | None:
        lock = getattr(self, "_lock", None)
        if lock is None:
            return _eta(self.total, self.done, _track_rate(self._track_times))
        with lock:
            return _eta(self.total, self.done,
                        _track_rate(self._track_times))


def _track_rate(track_times) -> float:
    """Tracks per second over completion timestamps (len < 2 -> 0.0)."""
    times = list(track_times)
    if len(times) < 2:
        return 0.0
    deltas = [b - a for a, b in zip(times[:-1], times[1:]) if b > a]
    if not deltas:
        return 0.0
    return len(deltas) / sum(deltas)


def _eta(total: int, done: int, rate: float) -> int | None:
    remaining = total - done
    if remaining <= 0:
        return 0
    if rate <= 0:
        return None
    return int(remaining / rate)
