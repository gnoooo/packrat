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
    """Return music/<Artist>/<Album>/<NN - title>.m4a.

    Singles (see is_single) go to music/<Artist>/<singles_dir>/<title>.m4a
    with no track number.
    """
    artist_dir = clean(artist or "Unknown Artist")
    if is_single(album, release_type):
        return root / artist_dir / clean(singles_dir) / track_filename(title, None, ext)
    return root / artist_dir / clean(album or UNKNOWN_ALBUM) / track_filename(title, track_num, ext)


def singles_path(
    root: pathlib.Path,
    artist: str,
    title: str,
    *,
    year: int | str | None = None,
    singles_dir: str = SINGLES_DIR_DEFAULT,
    taken: set[str] | None = None,
    ext: str = "m4a",
) -> pathlib.Path:
    """Pick a deterministic, collision-free path inside Singles.

    'Title.m4a' when free, else 'Title (<year>).m4a' when a year is given
    and free, else 'Title (2).m4a', 'Title (3).m4a', ... Never overwrites
    an existing file. `taken` holds already-used paths (as strings) in the
    current run so files that exist on disk after earlier moves also count.
    """
    base = root / clean(artist or "Unknown Artist") / clean(singles_dir)
    candidate = base / track_filename(title, None, ext)
    if not candidate.exists() and not (taken and str(candidate) in taken):
        return candidate
    if year not in (None, ""):
        with_year = base / track_filename(f"{clean(title)} ({year})", None, ext)
        if not with_year.exists() and not (taken and str(with_year) in taken):
            return with_year
    n = 2
    while True:
        numbered = base / track_filename(f"{clean(title)} ({n})", None, ext)
        if not numbered.exists() and not (taken and str(numbered) in taken):
            return numbered
        n += 1


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
