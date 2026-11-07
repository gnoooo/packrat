"""Atomic download archive of completed video IDs."""

from __future__ import annotations

import os
import pathlib
import tempfile
from typing import Iterable


class Archive:
    """Set of video IDs persisted atomically to a text file."""

    def __init__(self, path: str | pathlib.Path) -> None:
        self.path = pathlib.Path(path)
        self._ids: set[str] = set()
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            text = self.path.read_text(encoding="utf-8")
            self._ids = {line.strip() for line in text.splitlines() if line.strip()}

    def __contains__(self, video_id: object) -> bool:
        return str(video_id) in self._ids

    def add(self, video_id: str) -> None:
        self._ids.add(str(video_id).strip())

    def update(self, video_ids: Iterable[str]) -> None:
        for vid in video_ids:
            self.add(vid)

    def save(self) -> None:
        """Write to a temp file in the same directory, then atomic rename."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = sorted(self._ids)
        fd, tmp = tempfile.mkstemp(
            dir=self.path.parent, prefix=self.path.name + ".tmp."
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
                if lines:
                    f.write("\n")
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def __len__(self) -> int:
        return len(self._ids)
