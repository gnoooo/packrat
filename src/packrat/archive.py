"""Append-only download archive of completed video IDs."""

from __future__ import annotations

import pathlib
from typing import Iterable


class Archive:
    """Set of video IDs persisted append-only to a text file."""

    def __init__(self, path: str | pathlib.Path) -> None:
        self.path = pathlib.Path(path)
        self._ids: set[str] = set()
        self._dirty: set[str] = set()
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            text = self.path.read_text(encoding="utf-8")
            self._ids = {line.strip() for line in text.splitlines() if line.strip()}

    def __contains__(self, video_id: object) -> bool:
        return str(video_id) in self._ids

    def add(self, video_id: str) -> None:
        vid = str(video_id).strip()
        self._ids.add(vid)
        self._dirty.add(vid)

    def update(self, video_ids: Iterable[str]) -> None:
        for vid in video_ids:
            self.add(vid)

    def ids(self) -> set[str]:
        """Return a snapshot of the IDs currently held in memory."""
        return set(self._ids)

    def save(self) -> None:
        """Atomically rewrite the archive, never leaving a partial file."""
        if not self._dirty:
            return
        from packrat.manifest import atomic_write_text

        atomic_write_text(
            self.path,
            "".join(f"{vid}\n" for vid in sorted(self._ids)),
        )
        self._dirty.clear()

    def __len__(self) -> int:
        return len(self._ids)
