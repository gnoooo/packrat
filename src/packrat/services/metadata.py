"""Metadata export service: manifests in, interoperable files out."""

from __future__ import annotations

import csv
import io
import json
import pathlib

from packrat.manifest import Manifest, atomic_write_text


def export_manifest_metadata(
    manifest_path: pathlib.Path,
    target: pathlib.Path,
    fmt: str = "json",
) -> pathlib.Path:
    """Write normalized track metadata from a saved manifest."""
    manifest = Manifest.load(manifest_path)
    rows = [
        {
            "video_id": entry.get("video_id", ""),
            "title": entry.get("title", ""),
            "artist": entry.get("artist", ""),
            "album": entry.get("album", ""),
            "track_num": entry.get("track_num"),
            "duration_seconds": entry.get("duration"),
            "release_year": entry.get("release_year"),
            "release_type": entry.get("release_type"),
            "status": entry.get("status"),
            "path": entry.get("path"),
            "source": entry.get("source"),
        }
        for entry in manifest.entries
    ]
    if fmt == "csv":
        buf = io.StringIO(newline="")
        writer = csv.DictWriter(buf, fieldnames=list(rows[0]) if rows else [])
        writer.writeheader()
        writer.writerows(rows)
        atomic_write_text(target, buf.getvalue(), newline="")
    else:
        atomic_write_text(
            target,
            json.dumps({"source": str(manifest_path), "tracks": rows}, indent=2),
        )
    return target
