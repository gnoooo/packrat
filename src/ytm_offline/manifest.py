"""Dry-run / final manifest of a sync run."""

from __future__ import annotations

import csv
import json
import pathlib
from typing import Any, Iterable


class Manifest:
    """Collects per-track status and writes JSON/CSV."""

    VALID_STATUSES = {
        "pending",
        "archived",
        "skipped_size",
        "failed",
        "downloaded",
    }

    def __init__(self) -> None:
        self.entries: list[dict] = []

    def add(
        self,
        video_id: str,
        title: str,
        artist: str,
        album: str,
        est_size: int | None,
        status: str,
        error: str | None = None,
        path: str | None = None,
        **extra: Any,
    ) -> None:
        if status not in self.VALID_STATUSES:
            raise ValueError(f"invalid status: {status}")
        entry: dict[str, Any] = {
            "video_id": video_id,
            "title": title,
            "artist": artist,
            "album": album,
            "est_size": est_size,
            "status": status,
            "error": error,
            "path": path,
        }
        entry.update(extra)
        self.entries.append(entry)

    def total_est_size(self) -> int:
        return sum(
            (e.get("est_size") or 0) for e in self.entries
            if e["status"] in ("pending", "downloaded")
        )

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {s: 0 for s in self.VALID_STATUSES}
        for e in self.entries:
            out[e["status"]] += 1
        return out

    @classmethod
    def load(cls, path: pathlib.Path) -> "Manifest":
        manifest = cls()
        manifest.entries = json.loads(path.read_text(encoding="utf-8")).get("entries", [])
        return manifest

    def failed_ids(self) -> list[str]:
        return [e["video_id"] for e in self.entries if e["status"] == "failed"]

    def pending(self) -> list[dict]:
        return [e for e in self.entries if e.get("status") == "pending"]

    def write(self, path: pathlib.Path, fmt: str = "json") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "json":
            path.write_text(
                json.dumps(
                    {
                        "entries": self.entries,
                        "total_est_size": self.total_est_size(),
                        "counts": self.counts(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        elif fmt == "csv":
            with path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=self.entries[0].keys())
                writer.writeheader()
                writer.writerows(self.entries)
        else:
            raise ValueError(f"unsupported manifest format: {fmt}")
