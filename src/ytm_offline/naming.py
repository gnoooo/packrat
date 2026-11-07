"""Filename and path helpers for the music layout."""

from __future__ import annotations

import pathlib
import re

from yt_dlp.utils import sanitize_filename


def clean(text: str) -> str:
    """Sanitize a single path component."""
    return sanitize_filename(str(text).strip(), restricted=True).strip(".")


def track_filename(
    title: str,
    track_num: int | None = None,
    ext: str = "m4a",
) -> str:
    """Return 'NN - title.m4a' with a sanitized title."""
    name = clean(title)
    if track_num is not None:
        name = f"{int(track_num):02d} - {name}"
    return f"{name}.{ext.lstrip('.')}"


def track_path(
    root: pathlib.Path,
    artist: str,
    album: str,
    title: str,
    track_num: int | None = None,
    ext: str = "m4a",
) -> pathlib.Path:
    """Return music/<Artist>/<Album>/<NN - title>.m4a."""
    return (
        root
        / clean(artist or "Unknown Artist")
        / clean(album or "Unknown Album")
        / track_filename(title, track_num, ext)
    )


def parse_playlist_id(source: str) -> str:
    """Extract a ytmusicapi playlist id from 'playlist:<id/url>'."""
    prefix = "playlist:"
    if not source.startswith(prefix):
        raise ValueError(f"expected source to start with '{prefix}'")
    value = source[len(prefix) :].strip()
    # If it looks like a URL, grab the list= parameter
    if "://" in value or value.startswith("www."):
        match = re.search(r"[?&]list=([^&]+)", value)
        if match:
            return match.group(1)
        raise ValueError(f"could not find playlist id in URL: {value}")
    return value
