"""Whole-album discovery from liked artists."""

from __future__ import annotations

import pathlib
import re
from collections import Counter, defaultdict
from typing import Any

from ytmusicapi import YTMusic

from ytm_offline.archive import Archive
from ytm_offline.discover import (
    ALLOWED_VIDEO_TYPES,
    _album_name,
    _artist_name,
    _duration_seconds,
    _normalize,
    _video_type,
)
from ytm_offline.manifest import Manifest
from ytm_offline.batch import run_batch_download
from ytm_offline.naming import clean, track_path
from ytm_offline.playlist import write_m3u8
from ytm_offline.sizes import format_size, heuristic_size, parse_size


_EDITION_WORDS = (
    "deluxe",
    "explicit",
    "clean",
    "remastered",
    "expanded",
    "anniversary",
    "edition",
    "version",
    "reissue",
    "remix",
)

_UNWANTED_ALBUM_WORDS = (
    "live",
    "compilation",
    "greatest hits",
    "best of",
    "remix",
    "bootleg",
    "cover",
)


def _album_base_title(title: str) -> str:
    """Strip edition tags and parentheses to group variants."""
    base = str(title)
    # Remove parenthetical/bracket suffixes.
    base = re.sub(r"[\(\[].*?[\)\]]", "", base)
    # Drop edition words.
    for word in _EDITION_WORDS:
        base = re.sub(rf"\b{word}\b", "", base, flags=re.IGNORECASE)
    return _normalize(base)


def _is_wanted_album(title: str) -> bool:
    t = title.lower()
    return not any(w in t for w in _UNWANTED_ALBUM_WORDS)


def _pick_album_variants(
    albums: list[dict[str, Any]],
    max_per_artist: int,
) -> list[dict[str, Any]]:
    """Group by base title and pick the best variant per album."""
    wanted = [a for a in albums if _is_wanted_album(a.get("title", ""))]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a in wanted:
        groups[_album_base_title(a["title"])].append(a)

    picked: list[dict[str, Any]] = []
    for base, variants in groups.items():
        if not variants:
            continue
        # Prefer the variant whose title has the fewest edition words.
        def edition_score(a: dict[str, Any]) -> int:
            t = a.get("title", "").lower()
            return sum(1 for w in _EDITION_WORDS if w in t)
        variants.sort(key=lambda a: (edition_score(a), a.get("year") or "9999"))
        picked.append(variants[0])

    # Prefer earlier years, then shorter titles (standard editions).
    picked.sort(key=lambda a: (a.get("year") or "9999", len(a.get("title", ""))))
    return picked[:max_per_artist]


def _load_owned_keys(yt: YTMusic) -> set[tuple[str, str]]:
    """Keys from liked songs and history to catch alternate versions."""
    keys: set[tuple[str, str]] = set()
    try:
        liked = yt.get_liked_songs(limit=None)
        for t in liked.get("tracks", []):
            keys.add((_normalize(_artist_name(t)), _normalize(t.get("title", ""))))
    except Exception:
        pass
    try:
        for t in yt.get_history():
            keys.add((_normalize(_artist_name(t)), _normalize(t.get("title", ""))))
    except Exception:
        pass
    return keys


def _top_liked_artists(
    yt: YTMusic,
    top_k: int,
) -> list[tuple[str, str]]:
    """Return [(name, browseId), ...] ranked by like-count."""
    liked = yt.get_liked_songs(limit=None)
    counts = Counter()
    ids: dict[str, str] = {}
    for t in liked.get("tracks", []):
        artist = _artist_name(t)
        counts[artist] += 1
        # Try to capture browseId from the first liked artist.
        artists = t.get("artists") or []
        if artists and artists[0].get("id"):
            ids[artist] = artists[0]["id"]

    # Fill missing browseIds from library artists.
    try:
        for entry in yt.get_library_artists(limit=200):
            name = entry.get("artist", entry.get("title"))
            if name and entry.get("browseId") and name not in ids:
                ids[name] = entry["browseId"]
    except Exception:
        pass

    ranked = counts.most_common()
    result = []
    for name, _ in ranked[:top_k]:
        if name in ids:
            result.append((name, ids[name]))
    return result


def _album_track_path(
    root: pathlib.Path,
    artist: str,
    album_title: str,
    track: dict[str, Any],
) -> pathlib.Path:
    return track_path(
        root,
        artist,
        album_title,
        track.get("title", "Unknown"),
        track_num=track.get("trackNumber"),
    )


def discover_albums(
    headers_path: str,
    output_root: pathlib.Path,
    archive_path: str | pathlib.Path,
    artist_top_k: int = 15,
    albums_per_artist: int = 2,
    include_singles: bool = False,
    min_duration: int = 90,
    max_duration: int = 540,
    max_size: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Discover whole albums from top liked artists."""
    yt = YTMusic(headers_path)
    archive = Archive(archive_path)
    owned_keys = _load_owned_keys(yt)
    max_size_bytes = parse_size(max_size) if max_size else None

    top_artists = _top_liked_artists(yt, artist_top_k)
    playlists_dir = output_root / "playlists" / "albums"
    playlists_dir.mkdir(parents=True, exist_ok=True)

    manifest = Manifest()
    album_summaries: list[dict[str, Any]] = []
    total_new = 0
    total_owned = 0
    total_skipped_size = 0

    for artist_name, browse_id in top_artists:
        try:
            info = yt.get_artist(browse_id)
        except Exception:
            continue

        album_items: list[dict[str, Any]] = list(
            info.get("albums", {}).get("results", [])
        )
        if include_singles:
            album_items.extend(info.get("singles", {}).get("results", []))

        selected = _pick_album_variants(album_items, albums_per_artist)
        for album in selected:
            browse_id = album.get("browseId")
            if not browse_id:
                continue
            try:
                data = yt.get_album(browse_id)
            except Exception:
                continue
            album_title = data.get("title", album.get("title", "Unknown Album"))
            tracks = data.get("tracks", [])

            new_tracks: list[dict[str, Any]] = []
            owned_in_album = 0
            skipped_in_album = 0
            for t in tracks:
                vid = t.get("videoId")
                if not vid:
                    continue
                if vid in archive:
                    owned_in_album += 1
                    continue
                if _video_type(t) not in ALLOWED_VIDEO_TYPES:
                    continue
                dur = _duration_seconds(t)
                if dur is None or not (min_duration <= dur <= max_duration):
                    continue
                key = (_normalize(_artist_name(t)), _normalize(t.get("title", "")))
                if key in owned_keys:
                    owned_in_album += 1
                    continue
                path = _album_track_path(output_root, artist_name, album_title, t)
                est_size = heuristic_size(dur)
                status = "pending"
                if max_size_bytes is not None and est_size > max_size_bytes:
                    status = "skipped_size"
                    skipped_in_album += 1
                new_tracks.append(
                    {
                        "video_id": vid,
                        "title": t.get("title", "Unknown"),
                        "artist": _artist_name(t),
                        "album": album_title,
                        "duration": dur,
                        "path": str(path),
                        "status": status,
                        "est_size": est_size,
                    }
                )

            if not new_tracks and owned_in_album == 0:
                continue

            # Update owned keys so later albums don't re-add same track.
            for t in new_tracks:
                if t["status"] != "skipped_size":
                    owned_keys.add(
                        (_normalize(t["artist"]), _normalize(t["title"]))
                    )

            pending_tracks = [t for t in new_tracks if t["status"] == "pending"]
            total_new += len(pending_tracks)
            total_owned += owned_in_album
            total_skipped_size += skipped_in_album

            pl_name = f"{clean(artist_name)} - {clean(album_title)}.m3u8"
            m3u8_path = playlists_dir / pl_name

            m3u8_entries = [
                {
                    "path": t["path"],
                    "duration": t["duration"],
                    "title": t["title"],
                    "artist": t["artist"],
                }
                for t in pending_tracks
            ]
            if m3u8_entries:
                write_m3u8(m3u8_path, m3u8_entries)

            for t in new_tracks:
                manifest.add(
                    video_id=t["video_id"],
                    title=t["title"],
                    artist=t["artist"],
                    album=t["album"],
                    est_size=t["est_size"],
                    status=t["status"],
                    path=t["path"],
                    album_browse_id=browse_id,
                    reason=f"album from liked artist {artist_name}",
                    duration=t["duration"],
                )

            album_summaries.append(
                {
                    "artist": artist_name,
                    "album": album_title,
                    "browse_id": browse_id,
                    "tracks": len(pending_tracks),
                    "already_owned": owned_in_album,
                    "skipped_size": skipped_in_album,
                    "est_size": sum(t["est_size"] for t in pending_tracks),
                    "m3u8": str(m3u8_path) if m3u8_entries else None,
                }
            )

    manifest_path = output_root / "playlists" / "albums.manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest.write(manifest_path)

    total_size = manifest.total_est_size()
    return {
        "manifest_path": str(manifest_path),
        "artists_considered": len(top_artists),
        "albums": album_summaries,
        "total_new_tracks": total_new,
        "total_already_owned": total_owned,
        "total_skipped_size": total_skipped_size,
        "total_est_size": total_size,
        "total_est_size_human": format_size(total_size),
    }


def download_albums(
    output_root: pathlib.Path,
    archive_path: str | pathlib.Path,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
    browser_profile: str | None = None,
    max_size: str | None = None,
    delay: float = 7.0,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    control: Any = None,
) -> dict[str, Any]:
    """Download pending tracks from albums.manifest.json."""
    manifest_path = output_root / "playlists" / "albums.manifest.json"
    if not manifest_path.exists():
        return {"error": f"manifest not found: {manifest_path}"}
    manifest = Manifest.load(manifest_path)
    entries = manifest.pending()
    if not entries:
        return {"error": "no pending tracks in albums manifest"}

    if event_callback:
        event_callback(
            {
                "type": "start",
                "payload": {
                    "mode": "albums",
                    "total": len(entries),
                    "total_est_bytes": sum(
                        (e.get("est_size") or 0) for e in entries
                    ),
                    "max_size_bytes": parse_size(max_size) if max_size else None,
                },
            }
        )

    def save_callback(current_entries: list[dict[str, Any]]) -> None:
        manifest.entries = current_entries
        manifest.write(manifest_path)

    summary = run_batch_download(
        entries,
        output_root=output_root,
        save_callback=save_callback,
        cookies=cookies,
        cookies_from_browser=cookies_from_browser,
        browser_profile=browser_profile,
        max_size=parse_size(max_size) if max_size else None,
        delay=delay,
        event_callback=event_callback,
        control=control,
        archive_path=archive_path,
        mode="albums",
    )
    return {"summary": summary, "manifest_path": str(manifest_path)}
