"""Thread-safe control flags shared between the UI and worker threads."""

from __future__ import annotations

import threading
import time


class Control:
    """Pause/resume/skip/stop signals from the UI to the worker."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.paused = False
        self.skip_current = False
        self.stop_after_current = False

    def pause(self) -> None:
        with self._lock:
            self.paused = True

    def resume(self) -> None:
        with self._lock:
            self.paused = False

    def toggle_pause(self) -> bool:
        with self._lock:
            self.paused = not self.paused
            return self.paused

    def request_skip(self) -> None:
        with self._lock:
            self.skip_current = True

    def request_stop(self) -> None:
        with self._lock:
            self.stop_after_current = True

    def should_stop(self) -> bool:
        with self._lock:
            return self.stop_after_current

    def should_skip(self) -> bool:
        with self._lock:
            if self.skip_current:
                self.skip_current = False
                return True
            return False

    def wait_while_paused(self, callback=None, interval: float = 0.2) -> None:
        """Block until paused is False, optionally calling callback each interval."""
        while True:
            with self._lock:
                if not self.paused:
                    return
            if callback:
                callback()
            time.sleep(interval)
