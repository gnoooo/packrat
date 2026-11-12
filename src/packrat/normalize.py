"""Shared normalization for ytmusicapi track/release dicts."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from ytmusicapi import YTMusic

EDITION_WORDS = (
    "deluxe",
    "explicit",
    "clean",
    "remastered",
    "remaster",
    "expanded",
    "anniversary",
    "edition",
    "revisited",
    "re-recording",
    "remix",
    "instrumental",
    "live",
    "bonus",
)


def normalize(text: Any) -> str:
    """Case-fold and strip punctuation while preserving Unicode letters.

    The old ASCII-only regex collapsed every CJK/Cyrillic/Greek name to the
    empty string, making unrelated artists look identical.
    """
    value = unicodedata.normalize("NFKC", str(text)).casefold()
    return "".join(ch for ch in value if ch.isalnum())


def artist_name(raw: dict[str, Any]) -> str:
    artists = raw.get("artists") or []
    if artists:
        return artists[0].get("name", "Unknown Artist")
    return raw.get("author") or raw.get("channel", {}).get("name") or "Unknown Artist"


def album_name(raw: dict[str, Any]) -> str:
    album = raw.get("album")
    if isinstance(album, dict):
        return album.get("name") or "Unknown Album"
    if isinstance(album, str):
        return album or "Unknown Album"
    return "Unknown Album"


def parse_duration_string(value: str | None) -> int | None:
    if not value:
        return None
    parts = value.split(":")
    try:
        seconds = [int(p) for p in parts]
    except ValueError:
        return None
    if len(seconds) == 2:
        return seconds[0] * 60 + seconds[1]
    if len(seconds) == 3:
        return seconds[0] * 3600 + seconds[1] * 60 + seconds[2]
    return None


def duration_seconds(raw: dict[str, Any], yt: YTMusic | None = None) -> int | None:
    """Best-effort track duration in seconds."""
    if "duration_seconds" in raw and raw["duration_seconds"]:
        return int(raw["duration_seconds"])
    for key in ("lengthSeconds", "length_seconds"):
        if key in raw and raw[key]:
            return int(raw[key])
    for key in ("duration", "length"):
        if raw.get(key):
            parsed = parse_duration_string(str(raw[key]))
            if parsed is not None:
                return parsed
    if yt is not None and raw.get("videoId"):
        try:
            song = yt.get_song(raw["videoId"])
            return int(song["videoDetails"]["lengthSeconds"])
        except Exception:
            pass
    return None


def track_key(raw: dict[str, Any]) -> tuple[str, str]:
    return (normalize(artist_name(raw)), normalize(raw.get("title", "")))


def strip_edition(title: str) -> str:
    """Strip parenthetical/bracket suffixes and edition words."""
    base = re.sub(r"[\(\[].*?[\)\]]", "", str(title))
    for word in EDITION_WORDS:
        base = re.sub(rf"\b{word}\b", "", base, flags=re.IGNORECASE)
    return normalize(base).strip()
