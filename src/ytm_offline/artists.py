"""Whole-artist catalog discovery and download."""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

from ytmusicapi import YTMusic

from ytm_offline.archive import Archive
from ytm_offline.batch import run_batch_download
from ytm_offline.discover import _normalize
from ytm_offline.naming import clean, track_path
from ytm_offline.playlist import write_m3u8
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
    if "single" in kind or release.get("_section") == "singles":
        return "single"
    if "ep" == kind:
        return "ep"
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
        base = _base_title(release.get("title", ""))
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
        key = (_normalize(artist), _normalize(track["title"]))
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


def build_top_songs(
    yt: YTMusic,
    browse_id: str,
    archive_path: str | pathlib.Path,
    output_root: pathlib.Path,
    *,
    top_limit: int | None = None,
    max_per_album: int | None = None,
) -> dict[str, Any]:
    """Build pending entries for an artist's top songs with owned-dedupe."""
    discography = get_top_songs(yt, browse_id, limit=top_limit)
    artist_name = discography["artist"]
    archive = Archive(archive_path)
    output_root.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {"alternates": 0, "max_per_album": 0}
    capacity: dict[str, int] = {}
    entries: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    already_owned = 0
    total_est = 0

    for track in _dedupe_top_versions(discography["tracks"]):
        key = (_normalize(track["artist"] or artist_name), _normalize(track["title"]))
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
        owned = video_id in archive or owned_key_misses(
            output_root, track, artist_name
        )
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


def owned_key_misses(output_root: pathlib.Path, track: dict[str, Any], artist_name: str) -> bool:
    """Heuristic disk check when the top-song has no album folder name."""
    album = track.get("album") or ""
    if not album:
        return False
    probe = {
        "artist": track.get("artist") or artist_name,
        "title": track["title"],
        "track_num": None,
    }
    return owned_file_exists(output_root, probe, {"title": album})


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
) -> dict[str, Any]:
    """Fetch, filter and dedupe an artist catalog; return manifest payload.

    Does not write anything; the caller saves the manifest.
    """
    yt = YTMusic(headers_path)
    top = (
        build_top_songs(
            yt,
            browse_id,
            archive_path=archive_path,
            output_root=output_root,
            top_limit=top_limit,
            max_per_album=max_per_album,
        )
        if scope in ("top-songs", "all")
        else None
    )
    if scope == "top-songs":
        discography = {"releases": [], "name": (top or {}).get("artist", ""), "subscribers": ""}
    else:
        discography = get_discography(yt, browse_id)
    releases = discography["releases"]
    artist_name = discography["name"] or (top or {}).get("artist", "")

    kept, excluded = filter_releases(
        releases,
        include_live=include_live,
        include_compilations=include_compilations,
        include_remixes=include_remixes,
        albums_only=albums_only,
        no_singles=no_singles,
    )
    kept, dropped = dedupe_editions(kept)
    excluded["edition_duplicates"] = len(dropped)
    excluded.update(top["excluded_counts"] if top else {})
    kept_ids = {r["browseId"] for r in kept}
    dropped_reasons = {
        r.get("browseId"): reason for r, reason in dropped
    }

    archive = Archive(archive_path)
    output_root.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, Any]] = []
    release_meta: list[dict[str, Any]] = []
    unavailable = 0
    already_owned = 0
    skipped_duration = 0
    new_tracks = 0
    total_est = 0
    owned_keys: set[tuple[str, str]] = set()
    if top:
        # Top songs come first; a catalog track equal to a top song is a dup.
        for t in top["entries"]:
            owned_keys.add((_normalize(t["artist"]), _normalize(t["title"])))
    kept_map = {r["browseId"]: r for r in kept}

    # Report all releases not surviving filtering/deduping.
    for release in releases:
        if release["browseId"] in kept_map:
            continue
        reason = release.get("excluded_rule") or dropped_reasons.get(
            release["browseId"], "excluded"
        )
        release_meta.append(
            {**_release_summary(release), "status": "excluded", "reason": reason}
        )

    def window_ok(duration: int) -> bool:
        return not duration_window or 90 <= duration <= 540

    for release in kept:
        tracks = resolve_release_tracks(yt, release)
        if tracks is None:
            unavailable += 1
            release_meta.append(
                {**_release_summary(release), "status": "excluded", "reason": "unavailable"}
            )
            continue
        year = _release_year(release)
        new_in_release = 0
        for track in tracks:
            key = (_normalize(track["artist"] or artist_name), _normalize(track["title"]))
            video_id = track["video_id"]
            duration = track.get("duration_seconds", 0)
            if not window_ok(duration):
                skipped_duration += 1
                continue
            if (
                video_id in archive
                or owned_file_exists(output_root, track, release)
                or key in owned_keys
            ):
                already_owned += 1
                continue
            owned_keys.add(key)
            est = heuristic_size(duration) if duration else 0
            total_est += est
            new_in_release += 1
            new_tracks += 1
            entries.append(
                {
                    "video_id": video_id,
                    "title": track["title"],
                    "artist": track["artist"] or artist_name,
                    "album": release.get("title", ""),
                    "track_num": track.get("track_num"),
                    "duration": duration,
                    "est_size": est,
                    "status": "pending",
                    "path": None,
                    "error": None,
                    "source": f"artist:{artist_name}",
                    "release_year": year,
                    "release_type": release.get("kind", "album"),
                }
            )
        release_meta.append(
            {
                **_release_summary(release),
                "status": "kept",
                "tracks": len(tracks),
                "new_tracks": new_in_release,
            }
        )

    label = clean(artist_name) or clean(browse_id)
    manifest_path = output_root / "playlists" / f"artist-{label}.manifest.json"
    excluded_counts = {**excluded, "unavailable": unavailable}
    if top:
        # Top songs first, then the rest of the catalog, deduped via owned_keys.
        all_entries = top["entries"] + entries
        already_owned += top["totals"]["already_owned"]
        total_est += top["totals"]["est_bytes"]
        top_totals = top["totals"]
    else:
        all_entries = entries
        top_totals = None
    total_seconds = sum(e["duration"] for e in all_entries)
    est_time = len(all_entries) * delay + total_seconds
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
            "new_tracks": len(all_entries),
            "already_owned": already_owned,
            "skipped_duration": skipped_duration,
            "excluded_counts": excluded_counts,
            "est_bytes": total_est,
            "est_human": format_size(total_est),
            "est_time_seconds": int(est_time),
            "max_size": payload["max_size"],
        },
        "entries": all_entries,
    }


def owned_file_exists(output_root: pathlib.Path, track: dict[str, Any], release: dict[str, Any]) -> bool:
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
        by_album.setdefault(entry.get("album", "Unknown Album"), []).append(entry)
    for album, tracks in by_album.items():
        tracks.sort(key=lambda x: int(x.get("track_num") or 0))
        m3u8_path = playlists_dir / f"{clean(album) or 'album'}.m3u8"
        write_m3u8(m3u8_path, [m3u8_entry(t) for t in tracks])
        written.append(str(m3u8_path))
    all_path = playlists_dir / "artist-all.m3u8"
    write_m3u8(all_path, [m3u8_entry(e) for e in artist_all_entries(downloaded)])
    written.append(str(all_path))
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
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


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
) -> dict[str, Any]:
    """Download pending tracks from artist-<label>.manifest.json."""
    manifest_path = output_root / "playlists" / f"artist-{label}.manifest.json"
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
        payload["entries"] = current_entries
        manifest_path.write_text(
            json.dumps({k: v for k, v in payload.items()}, indent=2),
            encoding="utf-8",
        )
        write_artist_playlists(current_entries, playlists_dir)

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
