"""Dry-run / final manifest of a sync run."""

from __future__ import annotations

import csv
import json
import logging
import os
import pathlib
import tempfile
from typing import Any


SCHEMA_VERSION = 1

logger = logging.getLogger(__name__)


def atomic_write_text(
    path: pathlib.Path,
    text: str,
    *,
    encoding: str = "utf-8",
    newline: str | None = None,
) -> None:
    """Write *text* to *path* atomically via a temp file in the same directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.tmp.", text=True
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline=newline) as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Manifest:
    """Collects per-track status and writes JSON/CSV."""

    VALID_STATUSES = {
        "pending",
        "archived",
        "skipped_size",
        "skipped",
        "failed",
        "downloaded",
    }

    def __init__(self) -> None:
        self.entries: list[dict] = []
        self.version: int = SCHEMA_VERSION

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
        data = json.loads(path.read_text(encoding="utf-8"))
        manifest = cls()
        manifest.version = data.get("version", SCHEMA_VERSION)
        if manifest.version > SCHEMA_VERSION:
            raise ValueError(
                f"manifest version {manifest.version} is newer than "
                f"supported {SCHEMA_VERSION}"
            )
        for entry in data.get("entries", []):
            status = entry.get("status")
            if status not in cls.VALID_STATUSES:
                logger.warning(
                    "manifest %s has unknown status %r for video_id %s; "
                    "treating as failed",
                    path,
                    status,
                    entry.get("video_id"),
                )
                entry["status"] = "failed"
                entry["error"] = f"unknown original status: {status}"
            manifest.entries.append(entry)
        return manifest

    def failed_ids(self) -> list[str]:
        return [e["video_id"] for e in self.entries if e["status"] == "failed"]

    def pending(self) -> list[dict]:
        return [e for e in self.entries if e.get("status") == "pending"]

    def write(self, path: pathlib.Path, fmt: str = "json") -> None:
        if fmt == "json":
            atomic_write_text(
                path,
                json.dumps(
                    {
                        "version": self.version,
                        "entries": self.entries,
                        "total_est_size": self.total_est_size(),
                        "counts": self.counts(),
                    },
                    indent=2,
                ),
            )
            return

        # JSON is always the source of truth. `csv` and `json+csv` also write
        # a CSV sidecar next to the JSON file.
        if fmt in ("csv", "json+csv"):
            self.write(path, "json")
            self._write_csv_sidecar(path)
            return

        raise ValueError(f"unsupported manifest format: {fmt}")

    def _write_csv_sidecar(self, json_path: pathlib.Path) -> None:
        """Write a CSV sidecar next to the JSON manifest."""
        import io

        if json_path.suffix == ".json":
            csv_path = json_path.with_suffix(".csv")
        else:
            csv_path = json_path.with_suffix(json_path.suffix + ".csv")

        fieldnames = list(self.entries[0].keys()) if self.entries else []
        buf = io.StringIO(newline="")
        writer = csv.DictWriter(buf, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(self.entries)
        atomic_write_text(csv_path, buf.getvalue(), newline="")
