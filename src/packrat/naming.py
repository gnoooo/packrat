"""Filename and path helpers for the music layout."""

from __future__ import annotations

import pathlib
import re

from yt_dlp.utils import sanitize_filename


def clean(text: str) -> str:
    """Sanitize a single path component without discarding Unicode letters.

    yt-dlp's restricted mode replaces every non-ASCII character with ``_``,
    which collapses entire CJK/Cyrillic/Greek libraries into one directory.
    Keep Unicode, but replace path separators and spaces with ``_`` so the
    existing ``The_Hives`` layout stays stable.
    """
    name = sanitize_filename(str(text).strip(), restricted=False)
    name = name.replace(" ", "_")
    name = name.replace("⧸", "_").replace("⧹", "_")
    name = name.replace("：", "_")
    name = name.strip().strip(".")
    return name or "_"


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


def collision_path(path: pathlib.Path, video_id: str) -> pathlib.Path:
    """Return a deterministic alternate path for a filename collision."""
    safe_id = clean(video_id) or "video"
    return path.with_name(f"{path.stem} [{safe_id}]{path.suffix}")


SINGLES_DIR_DEFAULT = "Singles"
UNKNOWN_ALBUM = "Unknown Album"


def is_single(album: str | None, release_type: str | None) -> bool:
    """True when the track should live in the Singles folder.

    A track is a single if its release type is 'single' (YT-Music 'Single',
    including multi-track singles; EPs stay albums) or if it has no album
    info at all (queues/liked/top-song rows without an album).
    """
    if (release_type or "").strip().lower() == "single":
        return True
    name = (album or "").strip()
    return not name or name.lower() == UNKNOWN_ALBUM.lower()


def track_path(
    root: pathlib.Path,
    artist: str,
    album: str,
    title: str,
    track_num: int | None = None,
    ext: str = "m4a",
    *,
    release_type: str | None = None,
    singles_dir: str = SINGLES_DIR_DEFAULT,
) -> pathlib.Path:
    """Return ~/Music/PackratMidden/<Artist>/<Album>/<NN - title>.m4a.

    Singles (see is_single) go to ~/Music/PackratMidden/<Artist>/<singles_dir>/<title>.m4a
    with no track number.
    """
    artist_dir = clean(artist or "Unknown Artist")
    if is_single(album, release_type):
        return root / artist_dir / clean(singles_dir) / track_filename(title, None, ext)
    return root / artist_dir / clean(album or UNKNOWN_ALBUM) / track_filename(title, track_num, ext)


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
