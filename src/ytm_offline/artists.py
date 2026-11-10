"""Whole-artist catalog discovery and download."""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import sys
import time
import unicodedata
from math import ceil
from typing import Any

from ytmusicapi import YTMusic

from ytm_offline.archive import Archive
from ytm_offline.batch import run_batch_download
from ytm_offline.discover import _normalize
from ytm_offline.manifest import atomic_write_text
from ytm_offline.naming import SINGLES_DIR_DEFAULT, clean, is_single, track_path
from ytm_offline.playlist import write_m3u8
from ytm_offline import runs
from ytm_offline.sizes import format_size, heuristic_size, parse_size


_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_CHANNEL_URL_RE = re.compile(
    r"(?:music\.youtube\.com|youtube\.com)/channel/(UC[A-Za-z0-9_-]{22})"
)

_LIVE_RE = re.compile(r"\b(live|unplugged|in concert|concert|tour)\b", re.IGNORECASE)
_COMPILATION_RE = re.compile(
    r"\b(greatest hits|best of|very best|anthology|collection|compilation|"
    r"essentials|icon|the singles)\b",
    re.IGNORECASE,
)
_REMIX_RE = re.compile(
    r"\b(remix|remixes|remixed|rmx|instrumental|karaoke|nightcore|slowed|"
    r"sped up|8d|reverb)\b",
    re.IGNORECASE,
)
_EDITION_WORDS = (
    "deluxe", "explicit", "clean", "remastered", "expanded", "anniversary",
    "edition", "version", "reissue", "remix", "bonus",
)


def parse_channel_id(spec: str) -> str | None:
    """Return a UC... channel id for ids and channel URLs, else None."""
    s = str(spec).strip()
    if match := _CHANNEL_URL_RE.search(s):
        return match.group(1)
    if _CHANNEL_ID_RE.match(s):
        return s
    return None


_VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})(?:[?&/]|$)")


def parse_subscribers(text: Any) -> int:
    """Parse '3.86M subscribers' style text into an int (-1 if unknown)."""
    match = re.search(r"([\d.,]+)\s*([KMB])", str(text or ""))
    if match:
        try:
            return int(float(match.group(1).replace(",", ""))
                       * {"K": 1e3, "M": 1e6, "B": 1e9}[match.group(2)])
        except ValueError:
            return -1
    match = re.search(r"([\d,]+)", str(text or ""))
    if not match:
        return -1
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return -1


def search_artists(yt: YTMusic, name: str, limit: int = 6) -> list[dict[str, Any]]:
    """Search artists by name, ranked by subscribers, with a sample album."""
    try:
        results = yt.search(name, filter="artists") or []
    except StopIteration:
        results = []
    candidates = []
    for item in results:
        browse_id = str(item.get("browseId") or "")
        if not browse_id.startswith("UC"):
            continue
        candidates.append(
            {
                "name": item.get("artist") or item.get("title") or "",
                "browseId": browse_id,
                "subscribers": item.get("subscribers", ""),
                "sample": "",
            }
        )
    candidates.sort(key=lambda c: -parse_subscribers(c.get("subscribers")))
    candidates = candidates[:limit]
    for candidate in candidates:
        try:
            artist = yt.get_artist(candidate["browseId"])
            if albums := (artist.get("albums") or {}).get("results"):
                candidate["sample"] = albums[0].get("title", "")
            # Search rows never carry subscriber counts; backfill from the profile.
            candidate["subscribers"] = candidate.get("subscribers") or artist.get(
                "subscribers", ""
            )
        except Exception:
            pass
    return candidates


def looks_like_video_id(spec: str) -> bool:
    """True for watch/music URLs with an id or a bare 11-char id token."""
    return bool(
        _VIDEO_ID_RE.search(spec)
        or re.fullmatch(r"[A-Za-z0-9_-]{11}", spec.strip())
    )


def extract_video_id(spec: str) -> str:
    """Pull the 11-char id out of a URL, or use the bare id as-is."""
    m = _VIDEO_ID_RE.search(spec)
    return m.group(1) if m else spec.strip()


def resolve_video(yt: YTMusic, video_id: str):
    """Resolve a video id to its primary artist via the watch playlist."""
    vid = extract_video_id(video_id)
    try:
        wp = yt.get_watch_playlist(videoId=vid)
    except Exception:
        return None
    if not wp.get("tracks"):
        return None
    track = wp["tracks"][0]
    artists = track.get("artists") or []
    name = artists[0].get("name") if artists else ""
    browse_id = artists[0].get("id") if artists else ""
    if not browse_id.startswith("UC"):
        return None
    return {"name": name or vid, "browseId": browse_id, "subscribers": "", "sample": ""}


def classify_release(title: str) -> list[str]:
    """Return exclusion-rule names matching the release title."""
    reasons: list[str] = []
    if _LIVE_RE.search(title):
        reasons.append("live")
    if _COMPILATION_RE.search(title):
        reasons.append("compilation")
    if _REMIX_RE.search(title):
        reasons.append("remix")
    return reasons


def release_kind(release: dict[str, Any]) -> str:
    """Normalize a release type into album/ep/single."""
    kind = str(release.get("type", "")).lower()
    if kind == "ep":
        # EPs live in the singles section on YT Music but download to an
        # album folder (is_single only routes release_type 'single').
        return "ep"
    if "single" in kind or release.get("_section") == "singles":
        return "single"
    return "album"


def filter_releases(
    releases: list[dict[str, Any]],
    include_live: bool = False,
    include_compilations: bool = False,
    include_remixes: bool = False,
    albums_only: bool = False,
    no_singles: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Filter a discography by type and title-keyword rules.

    Excluded releases get 'excluded_rule' set in place and are removed from
    the returned list. Returns (kept, excluded_counts_per_rule).
    """
    counted = {"live", "compilation", "remix"}
    if include_live:
        counted.discard("live")
    if include_compilations:
        counted.discard("compilation")
    if include_remixes:
        counted.discard("remix")

    excluded: dict[str, int] = {"live": 0, "compilation": 0, "remix": 0,
                                "albums_only": 0, "no_singles": 0}
    kept: list[dict[str, Any]] = []
    for release in releases:
        release["kind"] = release_kind(release)
        rules = [r for r in classify_release(str(release.get("title", "")))
                 if r in counted]
        if rules:
            release["excluded_rule"] = ",".join(rules)
            for rule in rules:
                excluded[rule] += 1
            continue
        if albums_only and release["kind"] != "album":
            release["excluded_rule"] = "albums_only"
            excluded["albums_only"] += 1
            continue
        if no_singles and release["kind"] == "single":
            release["excluded_rule"] = "no_singles"
            excluded["no_singles"] += 1
            continue
        kept.append(release)
    return kept, excluded


def _base_title(title: str) -> str:
    """Strip parenthetical/bracket suffixes and edition words, normalized."""
    base = re.sub(r"[\(\[].*?[\)\]]", "", str(title))
    for word in _EDITION_WORDS:
        base = re.sub(rf"\b{word}\b", "", base, flags=re.IGNORECASE)
    return _normalize(base).strip()


_MARKER_RE = re.compile(r"\b(day|part|vol|volume|disc)\b", re.IGNORECASE)


def _variant_stamp(title: str) -> str:
    """The suffix stripped by _base_title, normalized ("" if none)."""
    removed: list[str] = []
    stripped = re.sub(
        r"[\(\[].*?[\)\]]", lambda m: removed.append(m.group(0)) or "", str(title),
        count=1,
    )
    return _normalize(" ".join(removed)).strip()


def _differs_by_edition(stamp: str) -> bool:
    """True when a stripped suffix marks a separate release, not an edition.

    '[DAY 1]' vs '[DAY 2]', 'Part 3', 'Vol. 2', '(Disc 1)' are different
    releases; parenthetical numbers or markers always disqualify the dup.
    """
    return bool(stamp) and (any(ch.isdigit() for ch in stamp) or bool(_MARKER_RE.search(stamp)))


def _edition_score(title: str) -> int:
    t = str(title).lower()
    return sum(1 for w in _EDITION_WORDS if w in t)


def dedupe_editions(
    releases: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], str]]]:
    """Keep one edition per album: prefer standard over deluxe/remaster.

    Returns (kept, [(dropped_release, reason), ...]). Clean/explicit variants
    are considered editions of the same album.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for release in releases:
        title = str(release.get("title", ""))
        base = _base_title(title)
        # '[DAY 1]' vs '[DAY 2]' style suffixes mark separate releases,
        # not editions of one album.
        stamp = _variant_stamp(title)
        if _differs_by_edition(stamp):
            base = f"{base}::{stamp}"
        if base not in groups:
            groups[base] = []
            order.append(base)
        groups[base].append(release)

    kept: list[dict[str, Any]] = []
    dropped: list[tuple[dict[str, Any], str]] = []
    for base in order:
        variants = sorted(
            groups[base],
            key=lambda r: (_edition_score(r.get("title", "")), len(str(r.get("title", "")))),
        )
        best = variants[0]
        kept.append(best)
        for other in variants[1:]:
            dropped.append((other, f"duplicate edition of {best.get('title', '')}"))
    return kept, dropped


def _release_year(release: dict[str, Any]) -> int:
    try:
        return int(str(release.get("year_int") or release.get("year", "0"))[:4])
    except (TypeError, ValueError):
        return 9999


def get_discography(yt: YTMusic, artist_id: str) -> dict[str, Any]:
    """Fetch artist info and the FULL album + single list via 'see all' params."""
    artist = yt.get_artist(artist_id)
    releases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for section in ("albums", "singles"):
        data = artist.get(section) or {}
        section_results = list(data.get("results") or [])
        params, browse_id = data.get("params"), data.get("browseId")
        if params and browse_id:
            try:
                section_results.extend(
                    yt.get_artist_albums(browse_id, params, limit=None)
                )
            except Exception:
                pass
        for row in section_results:
            browse = row.get("browseId") or row.get("playlistId")
            if not browse or browse in seen:
                continue
            seen.add(browse)
            releases.append(
                {
                    "title": row.get("title", ""),
                    "year": row.get("year"),
                    "type": row.get("type", ""),
                    "browseId": browse,
                    "_section": section,
                }
            )
    return {
        "name": artist.get("name") or "",
        "browse_id": artist_id,
        "subscribers": artist.get("subscribers", ""),
        "releases": releases,
    }


_TOP_VERSION_WEIGHTS = (
    "cover",
    "remix",
    "live",
    "karaoke",
    "instrumental",
    "nightcore",
    "sped up",
    "slowed",
    "8d",
)


def _dedupe_top_versions(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One version per song, preferring the album/official-audio version."""
    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for track in tracks:
        artists = track.get("artists") or []
        artist = artists[0].get("name") if artists else ""
        key = owned_key(artist, track["title"])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(track)

    kept: list[dict[str, Any]] = []
    for key in order:
        variants = groups[key]
        # Lowest bad-word count wins; ties prefer the earliest occurrence.
        best = variants[
            min(
                range(len(variants)),
                key=lambda i: (
                    sum(1 for w in _TOP_VERSION_WEIGHTS if w in variants[i]["title"].lower()),
                    i,
                ),
            )
        ]
        kept.append(best)
    return kept


def get_top_songs(yt: YTMusic, artist_id: str, limit: int | None = None) -> dict[str, Any]:
    """FULL 'Top songs' via the songs section's see-all playlist browseId."""
    artist = yt.get_artist(artist_id)
    songs_section = artist.get("songs") or {}
    all_tracks: list[dict[str, Any]] = list(songs_section.get("results") or [])
    browse_id = songs_section.get("browseId")
    if browse_id:
        try:
            playlist = yt.get_playlist(playlistId=browse_id, limit=limit)
            all_tracks = playlist.get("tracks") or []
        except Exception:
            pass
    if limit is not None:
        all_tracks = all_tracks[:limit]
    tracks = []
    for raw in all_tracks:
        if not raw.get("videoId"):
            continue
        artists = raw.get("artists") or []
        tracks.append(
            {
                "video_id": raw["videoId"],
                "title": raw.get("title", ""),
                "artist": artists[0].get("name") if artists else "",
                "album": (raw.get("album") or {}).get("name", ""),
                "duration_seconds": raw.get("duration_seconds")
                or _parse_duration(raw.get("duration")),
                "album_id": (raw.get("album") or {}).get("id", ""),
            }
        )
    return {
        "artist": artist.get("name") or "",
        "browse_id": artist_id,
        "subscribers": artist.get("subscribers", ""),
        "tracks": tracks,
    }


def owned_key(artist_name: str, title: str) -> tuple[str, str]:
    """The ONE dedupe key for owned-ness: catalog artist + base song title.

    Uses _base_title so album versions and "(feat. X)" / live / single
    versions of the same song collapse to the same key.
    """
    return (_normalize(artist_name or ""), _base_title(title or ""))


def disk_owned_keys(output_root: pathlib.Path) -> set[tuple[str, str]]:
    """Normalized (artist, title) keys for audio files already on disk.

    Derived from paths under output_root/<Artist>/<Album>/NN - Title.m4a
    (or <Artist>/<file> for legacy singles). This is the ONE owned-check
    used by top songs and releases alike — filenames only, never auth data.
    """
    keys: set[tuple[str, str]] = set()
    root = pathlib.Path(output_root)
    if not root.exists():
        return keys
    for p in root.rglob("*.m4a"):
        parent = p.parent
        artist_dir = parent.parent.name if parent.parent != root else parent.name
        title = re.sub(r"^\d+\s*-\s*", "", p.stem)
        keys.add(owned_key(artist_dir, title))
    return keys


def summarize_entries(
    entries: list[dict[str, Any]],
    *,
    delay: float = 7.0,
    measured: float | None = None,
) -> dict[str, Any]:
    """THE counting function: recap and manifest both derive from this."""
    est_bytes = sum(e.get("est_size") or 0 for e in entries)
    total_seconds = sum(e.get("duration") or 0 for e in entries)
    if measured:
        est_time = len(entries) * measured
        source = "measured"
    else:
        est_time = len(entries) * delay + total_seconds
        source = "duration-model"
    return {
        "new_tracks": len(entries),
        "est_bytes": est_bytes,
        "est_human": format_size(est_bytes),
        "est_time_seconds": int(est_time),
        "est_source": source,
        "measured_s_per_track": measured,
    }


def build_top_songs(
    yt: YTMusic,
    browse_id: str,
    archive_path: str | pathlib.Path,
    output_root: pathlib.Path,
    *,
    top_limit: int | None = None,
    max_per_album: int | None = None,
    singles_dir: str = SINGLES_DIR_DEFAULT,
    disk_keys: set[tuple[str, str]] | None = None,
    reserve_keys: set[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Build pending entries for an artist's top songs with owned-dedupe."""
    discography = get_top_songs(yt, browse_id, limit=top_limit)
    artist_name = discography["artist"]
    archive = Archive(archive_path)
    output_root.mkdir(parents=True, exist_ok=True)
    if disk_keys is None:
        disk_keys = disk_owned_keys(output_root)
    reserve = reserve_keys or set()

    counts: dict[str, int] = {"alternates": 0, "max_per_album": 0}
    capacity: dict[str, int] = {}
    entries: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    already_owned = 0
    total_est = 0

    for track in _dedupe_top_versions(discography["tracks"]):
        # Owned-ness is keyed on the CATALOG artist + base title so that
        # "(feat. X)" / live alternates match the album/single on disk.
        key = owned_key(artist_name, track["title"])
        if key in seen_keys:
            counts["alternates"] += 1
            continue
        seen_keys.add(key)
        video_id = track["video_id"]
        duration = track.get("duration_seconds") or 0
        album = track.get("album") or ""
        if max_per_album and album:
            used = capacity.get(track["album_id"], 0)
            if used >= max_per_album:
                counts["max_per_album"] += 1
                continue
            capacity[track["album_id"]] = used + 1
        owned = video_id in archive or key in disk_keys or key in reserve
        if owned:
            already_owned += 1
            continue
        est = heuristic_size(duration) if duration else 0
        total_est += est
        entries.append(
            {
                "video_id": video_id,
                "title": track["title"],
                "artist": track["artist"] or artist_name,
                "album": album,
                "track_num": None,
                "duration": duration,
                "est_size": est,
                "status": "pending",
                "path": None,
                "error": None,
                "source": f"top:{artist_name}",
                "release_year": 9999,
                "release_type": "top",
                "popularity": len(entries),
                "singles_dir": singles_dir,
            }
        )

    est_bytes = total_est
    est_time = len(entries) * 7.0 + sum(e["duration"] for e in entries)
    return {
        "artist": artist_name,
        "browse_id": browse_id,
        "entries": entries,
        "excluded_counts": counts,
        "totals": {
            "total_tracks": len(entries) + already_owned,
            "new_tracks": len(entries),
            "already_owned": already_owned,
            "excluded_counts": counts,
            "est_bytes": est_bytes,
            "est_human": format_size(est_bytes),
            "est_time_seconds": int(est_time),
        },
        "top_versions_skipped": sum(counts.values()),
    }


def _parse_duration(text: Any) -> int | None:
    """'3:41' -> seconds."""
    parts = [p for p in str(text or "").split(":")]
    if not all(p.isdigit() for p in parts) or not parts:
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def resolve_release_tracks(
    yt: YTMusic,
    release: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """Fetch one release's tracks via get_album; None when unavailable."""
    try:
        album = yt.get_album(release["browseId"])
    except Exception:
        return None
    tracks: list[dict[str, Any]] = []
    for index, raw in enumerate(album.get("tracks") or [], start=1):
        if not raw.get("videoId"):
            continue
        artists = raw.get("artists") or []
        artist = artists[0].get("name", "") if artists else ""
        duration = raw.get("duration_seconds") or _parse_duration(raw.get("duration"))
        tracks.append(
            {
                "video_id": raw["videoId"],
                "title": raw.get("title", ""),
                "artist": artist,
                "duration_seconds": int(duration or 0),
                "track_num": raw.get("trackNumber") or index,
            }
        )
    release["year_int"] = album.get("year") or release.get("year")
    return tracks


def build_catalog(
    headers_path: str,
    browse_id: str,
    output_root: pathlib.Path,
    archive_path: str | pathlib.Path,
    *,
    include_live: bool = False,
    include_compilations: bool = False,
    include_remixes: bool = False,
    albums_only: bool = False,
    no_singles: bool = False,
    duration_window: bool = False,
    scope: str = "all",
    top_limit: int | None = None,
    max_per_album: int | None = None,
    max_size: str | None = None,
    delay: float = 7.0,
    singles_dir: str = SINGLES_DIR_DEFAULT,
    pause: float = 0.5,
) -> dict[str, Any]:
    """Fetch, filter and dedupe an artist catalog; return manifest payload.

    Does not write anything; the caller saves the manifest. ``pause`` is the
    sleep between per-release tracklist fetches (tests pass 0).
    """
    yt = YTMusic(headers_path)
    disk_keys = disk_owned_keys(output_root)
    if scope == "top-songs":
        top = build_top_songs(
            yt,
            browse_id,
            archive_path=archive_path,
            output_root=output_root,
            top_limit=top_limit,
            max_per_album=max_per_album,
            singles_dir=singles_dir,
            disk_keys=disk_keys,
        )
        discography = {"releases": [], "name": top["artist"], "subscribers": ""}
        kept: list[dict[str, Any]] = []
        dropped: list[tuple[dict[str, Any], str]] = []
    else:
        top = None
        discography = get_discography(yt, browse_id)
    artist_name = discography["name"] or (top or {}).get("artist", "")
    releases = discography["releases"]

    kept, excluded = filter_releases(
        kept if scope == "top-songs" else releases,
        include_live=include_live,
        include_compilations=include_compilations,
        include_remixes=include_remixes,
        albums_only=albums_only,
        no_singles=no_singles,
    )
    if scope != "top-songs":
        kept, dropped = dedupe_editions(kept)
    excluded["edition_duplicates"] = len(dropped)
    excluded.update(top["excluded_counts"] if top else {})
    kept_ids = {r["browseId"] for r in kept}
    dup_of = {
        r.get("browseId"): reason[len("duplicate edition of "):]
        for r, reason in dropped
    }
    excluded_reasons = {
        r["browseId"]: r.get("excluded_rule") or "excluded"
        for r in releases
        if r["browseId"] not in kept_ids
    }

    archive = Archive(archive_path)
    output_root.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, Any]] = []
    unavailable = 0
    already_owned = 0
    skipped_duration = 0
    new_tracks = 0
    owned_keys: set[tuple[str, str]] = set()

    def window_ok(duration: int) -> bool:
        return not duration_window or 90 <= duration <= 540

    # Resolve the tracklist of EVERY release up front (tagged releases must
    # show counts too), with a cache so nothing is fetched twice.
    resolve_cache: dict[str, list[dict[str, Any]] | None] = {}
    total_releases = len(releases)

    def resolve(release: dict[str, Any]) -> list[dict[str, Any]] | None:
        bid = release["browseId"]
        if bid in resolve_cache:
            return resolve_cache[bid]
        if resolve_cache and pause > 0:
            time.sleep(pause)
        print(f"resolving releases {len(resolve_cache) + 1}/{total_releases}", end="\r", flush=True)
        tracks = resolve_release_tracks(yt, release)
        resolve_cache[bid] = tracks
        return tracks

    classified: dict[str, list[tuple[dict[str, Any], int, tuple[str, str]]]] = {
        r["browseId"]: [] for r in kept
    }
    track_counts: dict[str, int] = {}
    display_counts: dict[str, tuple[int, int] | None] = {}

    # Pass A: resolve all releases, decide owned (archive id / disk/key).
    # Kept rows seed owned_keys so top songs dedupe against the real catalog;
    # tagged (excluded) rows only collect display counts.
    for release in releases:
        bid = release["browseId"]
        tracks = resolve(release)
        if tracks is None:
            display_counts[bid] = None
            if bid in kept_ids:
                unavailable += 1
            continue
        year = _release_year(release)
        is_kept = bid in kept_ids
        n_new = 0
        for track in tracks:
            duration = track.get("duration_seconds", 0)
            if not window_ok(duration):
                if is_kept:
                    skipped_duration += 1
                continue
            key = owned_key(artist_name, track["title"])
            video_id = track["video_id"]
            if (
                video_id in archive
                or owned_file_exists(output_root, track, release)
                or key in disk_keys
            ):
                if is_kept:
                    already_owned += 1
                    owned_keys.add(key)
                continue
            if is_kept:
                classified[bid].append((track, year, key))
            else:
                n_new += 1
        if is_kept:
            track_counts[bid] = len(tracks)
        else:
            display_counts[bid] = (len(tracks), n_new)
    if total_releases:
        print()

    if scope == "all":
        top = build_top_songs(
            yt,
            browse_id,
            archive_path=archive_path,
            output_root=output_root,
            top_limit=top_limit,
            max_per_album=max_per_album,
            singles_dir=singles_dir,
            disk_keys=disk_keys,
            reserve_keys=owned_keys,
        )
        excluded.update(top["excluded_counts"])

    if top:
        # Top songs come first; a catalog track equal to a top song is a dup.
        for t in top["entries"]:
            owned_keys.add(owned_key(artist_name, t["title"]))

    # Pass B: form kept-release entries; rows that dup a selected top song
    # are counted as owned-duplicates and dropped.
    release_row_index = {r["browseId"]: i for i, r in enumerate(releases)}
    kept_new: dict[str, int] = {}
    for release in kept:
        bid = release["browseId"]
        if bid not in track_counts:
            continue  # already reported (unavailable)
        new_in_release = 0
        meta_row = release_row_index[bid]
        for track, year, key in classified[bid]:
            if key in owned_keys:
                already_owned += 1
                continue
            owned_keys.add(key)
            new_in_release += 1
            new_tracks += 1
            entries.append(_entry_for(track, release, artist_name, singles_dir=singles_dir, release_row=meta_row))
        kept_new[bid] = new_in_release

    # One meta row per release, in releases order; tags replace dropping.
    release_meta: list[dict[str, Any]] = []
    for idx, release in enumerate(releases):
        bid = release["browseId"]
        meta: dict[str, Any] = {
            "row": idx,
            **_release_summary(release),
            "section": "S" if release.get("_section") == "singles" else "A",
        }
        tags: list[str] = []
        if bid in dup_of:
            # A release can't be both rule-excluded and dup-dropped.
            tags.append(f"dup of {dup_of[bid]}")
        elif bid in excluded_reasons:
            tags.append(excluded_reasons[bid])
        if bid in kept_ids and bid in track_counts:
            meta["status"] = "kept"
            meta["tracks"] = track_counts[bid]
            meta["new_tracks"] = kept_new.get(bid, 0)
        else:
            meta["status"] = "excluded"
            counts = display_counts.get(bid)
            if counts is not None:
                meta["tracks"], meta["new_tracks"] = counts
            else:
                meta["unavailable"] = True
            meta["reason"] = ", ".join(tags) or (
                "unavailable" if counts is None else "excluded"
            )
        if tags:
            meta["tags"] = tags
        release_meta.append(meta)

    label = clean(artist_name) or clean(browse_id)
    manifest_path = output_root / "playlists" / f"artist-{label}.manifest.json"
    excluded_counts = {**excluded, "unavailable": unavailable}
    if top:
        # Top songs first, then the rest of the catalog, deduped via owned_keys.
        all_entries = top["entries"] + entries
        already_owned += top["totals"]["already_owned"]
        top_totals = top["totals"]
    else:
        all_entries = entries
        top_totals = None
    total_seconds = sum(e["duration"] for e in all_entries)
    measured = runs.last_measured(output_root)
    summary = summarize_entries(all_entries, delay=delay, measured=measured)
    payload = {
        "command": "artist",
        "artist": artist_name,
        "browse_id": browse_id,
        "label": label,
        "manifest_path": str(manifest_path),
        "releases": release_meta,
        "excluded_counts": excluded_counts,
        "entries": all_entries,
        "scope": scope,
        "top_songs": top_totals,
        "max_size": parse_size(max_size) if max_size else None,
        "totals": summary,
    }
    return {
        "manifest_path": manifest_path,
        "payload": payload,
        "artist": artist_name,
        "subscribers": discography.get("subscribers", ""),
        "label": label,
        "totals": {
            "releases_kept": len(kept_ids),
            "releases_excluded": len(releases) - len(kept),
            **summary,
            "already_owned": already_owned,
            "skipped_duration": skipped_duration,
            "excluded_counts": excluded_counts,
            "max_size": payload["max_size"],
        },
        "entries": all_entries,
        "_resolve_cache": resolve_cache,
    }


def owned_file_exists(output_root: pathlib.Path, track: dict[str, Any], release: dict[str, Any]) -> bool:
    """Check disk for the track under either layout.

    Singles: the new <Artist>/<singles_dir>/<title>.m4a path or the legacy
    <Artist>/<SingleTitle>/NN - title.m4a path; other releases unchanged.
    """
    kind = release.get("kind", "")
    if kind == "single":
        singles_layout = track_path(
            output_root,
            track.get("artist", ""),
            release.get("title", ""),
            track.get("title", ""),
            track_num=track.get("track_num"),
            ext="m4a",
            release_type="single",
        )
        if singles_layout.exists():
            return True
        return track_path(
            output_root,
            track.get("artist", ""),
            release.get("title", ""),
            track.get("title", ""),
            track_num=track.get("track_num"),
            ext="m4a",
            release_type="single",
        ).exists()
    return track_path(
        output_root,
        track.get("artist", ""),
        release.get("title", ""),
        track.get("title", ""),
        track_num=track.get("track_num"),
        ext="m4a",
    ).exists()


def _release_summary(release: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": release.get("title", ""),
        "year": release.get("year_int") or release.get("year"),
        "type": release.get("kind") or release.get("type"),
        "browse_id": release.get("browseId", ""),
    }


def _entry_for(
    track: dict[str, Any],
    release: dict[str, Any],
    artist_name: str,
    *,
    singles_dir: str = SINGLES_DIR_DEFAULT,
    release_row: int | None = None,
) -> dict[str, Any]:
    """Manifest entry dict for one pending release track."""
    duration = track.get("duration_seconds", 0)
    return {
        "video_id": track["video_id"],
        "title": track["title"],
        "artist": track.get("artist") or artist_name,
        "album": release.get("title", ""),
        "track_num": track.get("track_num"),
        "duration": duration,
        "est_size": heuristic_size(duration) if duration else 0,
        "status": "pending",
        "path": None,
        "error": None,
        "source": f"artist:{artist_name}",
        "release_year": _release_year(release),
        "release_type": release.get("kind", "album"),
        "singles_dir": singles_dir,
        "release_row": release_row,
    }


def artist_all_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order entries by (release year, track number)."""

    def sort_key(entry: dict[str, Any]) -> tuple[int, int]:
        try:
            year = int(entry.get("release_year") or 0)
        except (TypeError, ValueError):
            year = 0
        try:
            num = int(entry.get("track_num") or 0)
        except (TypeError, ValueError):
            num = 0
        return (year if year else 9999, num)

    return sorted(entries, key=sort_key)


def write_artist_playlists(
    entries: list[dict[str, Any]],
    playlists_dir: pathlib.Path,
) -> list[str]:
    """Write one .m3u8 per downloaded album plus artist-all.m3u8."""
    downloaded = [
        e for e in entries if e.get("path") and e.get("status") == "downloaded"
    ]
    if not downloaded:
        return []

    def m3u8_entry(entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "path": entry["path"],
            "duration": entry.get("duration", 0),
            "title": entry.get("title", ""),
            "artist": entry.get("artist", ""),
        }

    written: list[str] = []
    playlists_dir.mkdir(parents=True, exist_ok=True)
    by_album: dict[str, list[dict[str, Any]]] = {}
    for entry in downloaded:
        if is_single(entry.get("album", ""), entry.get("release_type")):
            continue  # no per-single m3u8; they go into <artist>-singles.m3u8
        by_album.setdefault(entry.get("album", "Unknown Album"), []).append(entry)
    for album, tracks in by_album.items():
        tracks.sort(key=lambda x: int(x.get("track_num") or 0))
        m3u8_path = playlists_dir / f"{clean(album) or 'album'}.m3u8"
        write_m3u8(m3u8_path, [m3u8_entry(t) for t in tracks])
        written.append(str(m3u8_path))
    all_path = playlists_dir / "artist-all.m3u8"
    write_m3u8(all_path, [m3u8_entry(e) for e in artist_all_entries(downloaded)])
    written.append(str(all_path))
    single_downloaded = [
        e for e in downloaded if is_single(e.get("album", ""), e.get("release_type"))
    ]
    if single_downloaded:
        single_artist = single_downloaded[0].get("artist", "artist")
        singles_path_m3u8 = playlists_dir / f"{clean(single_artist) or 'artist'}-singles.m3u8"

        def singles_sort_key(entry: dict[str, Any]) -> tuple[int, str]:
            try:
                year = int(entry.get("release_year") or 0)
            except (TypeError, ValueError):
                year = 0
            return (year if year else 9999, str(entry.get("title", "")))

        ordered = sorted(single_downloaded, key=singles_sort_key)
        write_m3u8(singles_path_m3u8, [m3u8_entry(e) for e in ordered])
        written.append(str(singles_path_m3u8))
    top_downloaded = [e for e in downloaded if e.get("release_type") == "top"]
    if top_downloaded:
        top_artist = top_downloaded[0].get("artist", "artist")
        top_path = playlists_dir / f"{clean(top_artist) or 'artist'}-top.m3u8"
        # Popularity order = the order the top entries appear in the manifest.
        write_m3u8(top_path, [m3u8_entry(e) for e in top_downloaded])
        written.append(str(top_path))
    return written


def save_manifest(payload: dict[str, Any]) -> None:
    manifest_path = pathlib.Path(payload["manifest_path"])
    atomic_write_text(manifest_path, json.dumps(payload, indent=2))


def download_artist(
    label: str,
    output_root: pathlib.Path,
    archive_path: str,
    *,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
    browser_profile: str | None = None,
    max_size: str | None = None,
    delay: float = 7.0,
    event_callback=None,
    control=None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Download pending tracks from artist-<label>.manifest.json."""
    manifest_path = output_root / "playlists" / f"artist-{label}.manifest.json"
    if payload is None:
        if not manifest_path.exists():
            return {"error": f"manifest not found: {manifest_path}"}
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = payload.get("entries", [])
    pending = [e for e in entries if e.get("status") == "pending"]
    if not pending:
        return {"error": "no pending tracks in manifest"}

    playlists_dir = output_root / "playlists" / "artists" / label
    manifest_max_size = payload.get("max_size")
    if max_size:
        manifest_max_size = parse_size(max_size)

    def save_callback(current_entries: list[dict[str, Any]]) -> None:
        # current_entries is the pending subset; the original dicts are
        # mutated in place so payload["entries"] already reflects updates.
        atomic_write_text(
            manifest_path,
            json.dumps({k: v for k, v in payload.items()}, indent=2),
        )
        write_artist_playlists(payload["entries"], playlists_dir)

    summary = run_batch_download(
        pending,
        output_root=output_root,
        save_callback=save_callback,
        cookies=cookies,
        cookies_from_browser=cookies_from_browser,
        browser_profile=browser_profile,
        max_size=manifest_max_size,
        delay=delay,
        event_callback=event_callback,
        control=control,
        archive_path=archive_path,
        mode=f"artist:{label}",
    )
    return {
        "summary": summary,
        "manifest_path": str(manifest_path),
        "m3u8_dir": str(playlists_dir),
        "m3u8_paths": write_artist_playlists(entries, playlists_dir),
    }


def interactive_pick(candidates: list[dict[str, Any]]) -> int | None:
    """Prompt the user to pick a candidate. Returns index, or None to cancel."""
    print("Multiple artists match:")
    for index, candidate in enumerate(candidates, start=1):
        subs = candidate.get("subscribers", "")
        sample = candidate.get("sample", "")
        suffix = f"  sample album: {sample}" if sample else ""
        print(
            f"  {index}) {candidate['name']}  "
            f"[{subs if subs else 'subscribers unknown'} subscribers]{suffix}"
        )
    try:
        answer = input("pick one [1-N] or 'q': ").strip()
    except EOFError:
        return None
    if answer.lower() in ("q", "quit", "exit"):
        return None
    if answer.isdigit() and 1 <= int(answer) <= len(candidates):
        return int(answer) - 1
    return None


# ---------------------------------------------------------------------------
# Interactive release picker (the two YouTube Music release sections:
# A = Albums, S = Singles & EPs).

SECTION_LETTERS = ("A", "S")
_SECTION_TITLES = {
    "A": "Albums",
    "S": "Singles & EPs",
}


def build_sections(catalog: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Numbered picker rows for the two release sections a build_catalog made.

    Mirrors the YouTube Music artist page: every release appears exactly once
    (albums section -> A, singles section -> S); tagged releases stay visible.
    Numbered newest year first, then title; stable across runs.
    """
    payload = catalog["payload"]
    sections: dict[str, list[dict[str, Any]]] = {k: [] for k in SECTION_LETTERS}
    for meta in payload["releases"]:
        letter = str(meta.get("section") or "")
        if letter not in sections:
            # Older manifests without 'section': single kind -> S, else A.
            letter = "S" if str(meta.get("type", "")) == "single" else "A"
        sections[letter].append({"kind": "release", "meta": meta})

    def year_key(row: dict[str, Any]) -> int:
        try:
            return int(str(row["meta"].get("year") or 0)[:4])
        except ValueError:
            return 0

    for letter, rows in sections.items():
        rows.sort(key=lambda r: (-year_key(r), str(r["meta"]["title"]).casefold()))
        for i, row in enumerate(rows, start=1):
            row["n"] = i
            meta = row["meta"]
            row["selected"] = (
                meta.get("status") == "kept"
                and not meta.get("tags")
                and int(meta.get("new_tracks") or 0) >= 1
            )

    # Dup tags reference the picked original as 'dup of A3' (section letter
    # + index) instead of the full title, so cells stay short.
    targets = {
        str(row["meta"].get("title", "")).casefold(): f"{letter}{row['n']}"
        for letter in SECTION_LETTERS
        for row in sections[letter]
    }
    for rows in sections.values():
        for row in rows:
            for i, tag in enumerate(row["meta"].get("tags") or []):
                if tag.startswith("dup of "):
                    short = targets.get(tag[len("dup of "):].casefold())
                    if short:
                        row["meta"]["tags"][i] = f"dup of {short}"
    return sections

def parse_selection(
    text: str,
    *,
    valid: tuple[str, ...] = ("A", "S"),
) -> list[dict[str, Any]]:
    """Parse the picker grammar into tokens.

    Token: '-?[AS]([ranges]|[*])?' — 'A' / 'A[*]' = whole section,
    'A[1-3]' range, 'A[1,3]', 'A[3-]' open range, '-S[4]' exclusion,
    letters case-insensitive. 'all' = everything. Any unknown section
    letter raises ``unknown section 'T' (valid: A, S)``. Each token is
    ``{'letter': 'A'|'S', 'spec': [(lo, hi|None), ...] | None,
    'exclude': bool}``; spec None means the whole section.
    """
    s = str(text or "").strip()
    if s.lower() == "all":
        return [{"letter": letter, "spec": None, "exclude": False} for letter in valid]

    def ranges(body: str) -> list[tuple[int, int | None]]:
        spec: list[tuple[int, int | None]] = []
        for part in body.split(","):
            part = part.strip()
            if part.isdigit():
                spec.append((int(part), int(part)))
                continue
            if "-" in part:
                lo, _, hi = part.partition("-")
                if lo.strip().isdigit():
                    spec.append((int(lo), int(hi) if hi.strip().isdigit() else None))
                    continue
            raise ValueError(f"bad range '{part}'")
        return spec

    out: list[dict[str, Any]] = []
    i = 0
    n = len(s)
    while i < n:
        while i < n and s[i] in " ,":
            i += 1
        if i >= n:
            break
        exclude = False
        if s[i] == "-":
            exclude = True
            i += 1
        if i >= n or s[i].upper() not in valid:
            bad = s[i] if i < n else "<end>"
            if bad.isalpha():
                raise ValueError(f"unknown section '{bad}' (valid: {', '.join(valid)})")
            raise ValueError(f"bad selection token '{s[i:i + 8] or s.strip()}'")
        letter = s[i].upper()
        i += 1
        spec: list[tuple[int, int | None]] | None = None
        if i < n and s[i] == "*":
            i += 1
        elif i < n and s[i] == "[":
            j = s.find("]", i)
            if j < 0:
                raise ValueError(f"unterminated '[...]' in '{s.strip()}'")
            body = s[i + 1:j].strip()
            if body != "*":
                spec = ranges(body)
            i = j + 1
        out.append({"letter": letter, "spec": spec, "exclude": exclude})
    if not out:
        raise ValueError("(empty)")
    return out


def _token_rows(
    sections: dict[str, list[dict[str, Any]]],
    token: dict[str, Any],
) -> list[dict[str, Any]]:
    """Rows a token addresses; out-of-range indices silently dropped."""
    rows = sections.get(token["letter"], [])
    spec = token.get("spec")
    if spec is None:
        return list(rows)
    picked: list[dict[str, Any]] = []
    for lo, hi in spec:
        end = len(rows) if hi is None else hi
        for i in range(lo, end + 1):
            if 1 <= i <= len(rows):
                picked.append(rows[i - 1])
    return picked


def chosen_rows(
    sections: dict[str, list[dict[str, Any]]],
    tokens: list[dict[str, Any]],
    *,
    use_default: bool = True,
) -> list[dict[str, Any]]:
    """Expand tokens to rows in display order (A then S).

    With no include token and use_default, the default selection rows come
    first; include tokens add, exclusion tokens subtract. Empty -> [].
    """
    picked: dict[int, dict[str, Any]] = {}
    if not any(not t.get("exclude") for t in tokens) and use_default:
        for rows in sections.values():
            for row in rows:
                if row.get("selected"):
                    picked[id(row)] = row
    for token in tokens:
        rows = _token_rows(sections, token)
        if token.get("exclude"):
            for row in rows:
                picked.pop(id(row), None)
        else:
            for row in rows:
                picked[id(row)] = row
    out: list[dict[str, Any]] = []
    for rows in sections.values():
        for row in rows:
            if id(row) in picked:
                out.append(row)
    return out


def apply_selection(
    catalog: dict[str, Any],
    sections: dict[str, list[dict[str, Any]]],
    tokens: list[dict[str, Any]],
    *,
    headers_path: str,
    output_root: pathlib.Path,
    archive_path: str | pathlib.Path,
    duration_window: bool = False,
    singles_dir: str = SINGLES_DIR_DEFAULT,
    delay: float = 7.0,
) -> dict[str, Any] | None:
    """Apply a picker selection; returns an updated catalog or None if empty.

    Kept releases reuse their already-classified entries; tagged (excluded)
    releases are resolved with the SAME counting rules (owned_key), from the
    run's _resolve_cache when available.
    """
    rows = chosen_rows(sections, tokens)
    if not rows:
        return None
    payload = catalog["payload"]
    artist_name = payload["artist"]
    archive = Archive(archive_path)

    # Seed owned keys with what the user is keeping, nothing else.
    selected_entries: list[dict[str, Any]] = []
    owned_keys: set[tuple[str, str]] = set()
    selected_meta_rows: set[int] = set()

    def window_ok(duration: int) -> bool:
        return not duration_window or 90 <= duration <= 540

    # Already-kept release entries first.
    for row in rows:
        meta = row["meta"]
        if meta.get("status") != "kept":
            continue
        for entry in payload["entries"]:
            if entry.get("release_row") == meta.get("row"):
                selected_entries.append(entry)
                owned_keys.add(owned_key(artist_name, entry["title"]))
        selected_meta_rows.add(meta.get("row", -1))

    # Tagged/excluded releases: resolve and classify now.
    yt = disk_keys = None
    cache = catalog.get("_resolve_cache") or {}
    for row in rows:
        meta = row["meta"]
        if meta.get("status") == "kept":
            continue
        bid = meta.get("browse_id", "")
        if bid in cache:
            tracks = cache[bid]
            if disk_keys is None:
                disk_keys = disk_owned_keys(output_root)
        else:
            if yt is None:
                if not pathlib.Path(headers_path).exists():
                    raise FileNotFoundError(headers_path)
                yt = YTMusic(headers_path)
                disk_keys = disk_owned_keys(output_root)
            release = {
                "browseId": bid,
                "title": meta.get("title", ""),
                "year_int": meta.get("year"),
                "kind": meta.get("type", "album"),
            }
            tracks = resolve_release_tracks(yt, release)
        if tracks is None:
            continue
        release = {
            "browseId": bid,
            "title": meta.get("title", ""),
            "year_int": meta.get("year"),
            "kind": meta.get("type", "album"),
        }
        for track in tracks:
            duration = track.get("duration_seconds", 0)
            if not window_ok(duration):
                continue
            key = owned_key(artist_name, track["title"])
            video_id = track["video_id"]
            if (
                video_id in archive
                or owned_file_exists(output_root, track, release)
                or key in disk_keys
                or key in owned_keys
            ):
                owned_keys.add(key)
                continue
            owned_keys.add(key)
            selected_entries.append(
                _entry_for(track, release, artist_name, singles_dir=singles_dir)
            )
        selected_meta_rows.add(meta.get("row", -1))

    if not selected_entries:
        return None
    for meta in payload["releases"]:
        meta["selected"] = meta.get("row") in selected_meta_rows
    measured = runs.last_measured(output_root)
    summary = summarize_entries(selected_entries, delay=delay, measured=measured)
    payload["entries"] = selected_entries
    payload["totals"] = summary
    newer = {
        "manifest_path": catalog["manifest_path"],
        "payload": payload,
        "artist": catalog["artist"],
        "subscribers": catalog.get("subscribers", ""),
        "label": catalog["label"],
        "totals": {
            **{k: v for k, v in catalog["totals"].items()
               if k in ("releases_kept", "releases_excluded", "already_owned",
                        "skipped_duration", "excluded_counts", "max_size")},
            **summary,
        },
        "entries": selected_entries,
    }
    return newer


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _dwidth(text: str) -> int:
    """Display width in terminal cells (W/F = 2 cells, combining = 0).
    ANSI escape sequences (bold, colors) are ignored."""
    text = _ANSI_RE.sub("", text)
    total = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        total += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return total


def _dw_pad(text: str, width: int) -> str:
    """Right-pad to `width` display cells ( Stranger-safe for wide chars)."""
    return text + " " * max(0, width - _dwidth(text))


def _dw_truncate(text: str, budget: int) -> str:
    """Truncate to <= budget display cells with …, never splitting a wide char.
    ANSI escape sequences are ignored while measuring."""
    text = _ANSI_RE.sub("", text)
    if _dwidth(text) <= budget:
        return text
    out = ""
    for ch in text:
        w = 0 if unicodedata.combining(ch) else (
            2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        )
        if _dwidth(out) + w > budget - 1:  # reserve one cell for …
            break
        out += ch
    return out.rstrip() + "…"


def _utf8_fine(ascii_mode: bool) -> bool:
    """False when stdout (or --ascii) cannot render ■/□/✓/·."""
    if ascii_mode:
        return False
    enc = sys.stdout.encoding or "utf-8"
    try:
        "\u25a0\u25a1\u2713\u00b7".encode(enc)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def _cell_info(
    row: dict[str, Any], *, sel_box: str, no_box: str, check: str,
) -> dict[str, Any]:
    """Parts of one picker cell: box, index, title, year, counter.

    counter = owned/total (e.g. '3/10'), '14/14 *check*' when fully
    owned, '✓'/'' for single-track releases; no tags in the cell —
    classification stays internal. Only the title is ever truncated.
    """
    meta = row["meta"]
    selected = bool(row.get("selected"))
    year = str(meta.get("year") or "?")[:4]
    tracks = meta.get("tracks")
    new = int(meta.get("new_tracks") or 0)
    if meta.get("unavailable") or tracks is None:
        counter = "?"
    else:
        total = int(tracks or 0)
        owned = max(0, total - new)
        if total == 1:
            counter = check if owned else ""
        else:
            counter = f"{owned}/{total}" + (f" {check}" if owned == total else "")
    return {
        "box": sel_box if selected else no_box,
        "idx": f"{row.get('n', 0):>2}",
        "title": str(meta.get("title") or "?"),
        "year": year,
        "counter": counter,
        "selected": selected,
        # Fully owned releases are dimmed (never selected by default).
        "dim": counter.endswith(check),
    }


def print_sections(
    sections: dict[str, list[dict[str, Any]]],
    *,
    width: int | None = None,
    ascii_mode: bool = False,
) -> None:
    """Column-major picker menu. 3 columns at width >= 100, 2 at >= 66,
    else 1; cell width = (width - 2*gap) // columns capped at 64. Widths
    are measured in screen cells so Japanese titles stay aligned; only
    the title is truncated. ANSI colors only on a TTY with NO_COLOR unset.
    """
    if width is None:
        width = shutil.get_terminal_size((80, 24)).columns
    use_color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    # Bold is not a color: it stays on with NO_COLOR, off for dumb/piped.
    use_bold = sys.stdout.isatty() and os.environ.get("TERM", "xterm") != "dumb"
    bold = "\x1b[1m" if use_bold else ""
    reset = "\x1b[0m" if use_bold or use_color else ""
    dim_c = "\x1b[2m" if use_color else ""
    green = "\x1b[32m" if use_color else ""
    utf8 = _utf8_fine(ascii_mode)
    sel_box, no_box = ("\u25a0", "\u25a1") if utf8 else ("[x]", "[ ]")
    check = "\u2713" if utf8 else "ok"
    dot = "\u00b7" if utf8 else "|"
    gap = 2
    if width >= 100:
        columns = 3
    elif width >= 66:
        columns = 2
    else:
        columns = 1
    cell_w = min((width - 2 * gap) // columns, 64)
    for letter in SECTION_LETTERS:
        rows = sections.get(letter) or []
        if not rows:
            continue
        n_selected = sum(1 for r in rows if r.get("selected"))
        n_new = sum(
            int(r["meta"].get("new_tracks") or 0)
            for r in rows if r.get("selected")
        )
        word = "track" if n_new == 1 else "tracks"
        head = _dw_truncate(
            f"{letter}  {_SECTION_TITLES[letter]} {dot} "
            f"{n_selected}/{len(rows)} selected {dot} {n_new} {word} to download",
            width,
        )
        if bold:
            head = f"{bold}{head}{reset}"
        print()
        print(head)
        pieces: list[list[tuple[dict[str, Any], str]]] = []
        for row in rows:
            c = _cell_info(row, sel_box=sel_box, no_box=no_box, check=check)
            left = f"{c['box']} {c['idx']} "
            year_s = f" {c['year']}"
            counter = c["counter"]
            budget = max(
                # at least two cells between year and right-edge counter
                cell_w - _dwidth(left) - _dwidth(year_s) - _dwidth(counter) - 2,
                3,
            )
            text = left + _dw_truncate(c["title"], budget) + year_s
            if counter:
                pad = max(cell_w - _dwidth(text) - _dwidth(counter), 2)
                text = text + " " * max(0, pad) + counter
                # Counter leans on the right edge: overwrite any roundoff.
                text = text if _dwidth(text) == cell_w else _dw_pad(text, cell_w)
            pieces.append((c, _dw_pad(text, cell_w)))
        rows_per_col = ceil(len(pieces) / columns)
        slots: list[list[tuple[dict[str, Any], str] | None]] = [
            [None] * columns for _ in range(rows_per_col)
        ]
        for i, item in enumerate(pieces):
            slots[i % rows_per_col][i // rows_per_col] = item
        for line_cells in slots:
            out = []
            for cell in line_cells:
                if cell is None:
                    continue
                c, plain = cell
                if c["dim"]:
                    out.append(f"{dim_c}{plain}{reset}" if dim_c else plain)
                    continue
                wbox = len(c["box"])
                color = green if c["selected"] else dim_c
                if color:
                    plain = color + plain[:wbox] + reset + plain[wbox:]
                out.append(plain)
            print("  ".join(out).rstrip())
    # Under the listing: legend, default-db note, selection total.
    print()
    legend = _dw_truncate(
        f"owned/total = tracks you own / tracks in release   {check} = all owned"
        f"   {sel_box} selected   {no_box} not selected",
        width,
    )
    print(legend)
    unselected_new = sum(
        1
        for rows in sections.values()
        for r in rows
        if int(r["meta"].get("new_tracks") or 0) >= 1 and not r.get("selected")
    )
    if unselected_new:
        print(_dw_truncate(
            f"{unselected_new} releases with new tracks are unselected by"
            " default (live / remix / alternate versions):"
            " type 'all' to include them",
            width,
        ))
    sel_rows = [
        r for rows in sections.values() for r in rows if r.get("selected")
    ]
    sel_tracks = sum(
        int(r["meta"].get("new_tracks") or 0) for r in sel_rows
    )
    word = "track" if sel_tracks == 1 else "tracks"
    print(_dw_truncate(
        f"Selected: {len(sel_rows)} releases {dot} {sel_tracks} {word} to download",
        width,
    ))
