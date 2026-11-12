"""Relative-path .m3u8 playlist generation."""

from __future__ import annotations

import os
import pathlib
from typing import Iterable

from packrat.manifest import atomic_write_text


def m3u8_entry(entry: dict) -> dict:
    """The four fields write_m3u8 needs, from a manifest entry."""
    return {
        "path": entry["path"],
        "duration": entry.get("duration", 0),
        "title": entry.get("title", "Unknown"),
        "artist": entry.get("artist", "Unknown"),
    }


def write_m3u8(
    path: pathlib.Path,
    entries: Iterable[dict],
) -> None:
    """Write a UTF-8 .m3u8 with relative paths.

    Each entry is a dict with keys: path (Path or str), duration (int/float),
    title, artist.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    playlist_abs = path.parent.resolve()
    lines = ["#EXTM3U"]
    for entry in entries:
        filepath = pathlib.Path(entry["path"]).resolve()
        rel = pathlib.Path(os.path.relpath(filepath, playlist_abs)).as_posix()
        duration = int(entry.get("duration", 0) or 0)
        title = entry.get("title", "Unknown")
        artist = entry.get("artist", "Unknown")
        lines.append(f"#EXTINF:{duration},{artist} - {title}")
        lines.append(rel)
    atomic_write_text(path, "\n".join(lines) + "\n", encoding="utf-8")
