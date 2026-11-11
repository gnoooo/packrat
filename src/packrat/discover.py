"""Discovery from YouTube Music recommendations (Home, Charts, Moods, New Releases)."""

from __future__ import annotations

import logging
import pathlib
from typing import Any, Callable

from ytmusicapi import YTMusic

from packrat.archive import Archive
from packrat.batch import exit_code_from_result, run_batch_download
from packrat.manifest import Manifest
from packrat.naming import SINGLES_DIR_DEFAULT
from packrat.normalize import (
    album_name as _album_name,
    artist_name as _artist_name,
    duration_seconds as _duration_seconds,
    normalize as _normalize,
    track_key as _track_key,
)
from packrat.playlist import m3u8_entry, write_m3u8
from packrat.sizes import format_size, heuristic_size

logger = logging.getLogger(__name__)


# Values observed in real ytmusicapi responses.
ALLOWED_VIDEO_TYPES = {
    "MUSIC_VIDEO_TYPE_ATV",
    "MUSIC_VIDEO_TYPE_OMV",
}


def _video_type(raw: dict[str, Any], yt: YTMusic | None = None) -> str | None:
    vt = raw.get("videoType") or raw.get("musicVideoType")
    if vt:
        return vt
    if yt is not None and raw.get("videoId"):
        try:
            song = yt.get_song(raw["videoId"])
            return song["videoDetails"].get("musicVideoType")
        except Exception as exc:
            logger.debug("optional API lookup failed: %s", exc)
    return None


def _to_track(
    raw: dict[str, Any],
    source: str,
    container: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "video_id": raw["videoId"],
        "title": raw.get("title", "Unknown"),
        "artist": _artist_name(raw),
        "album": _album_name(raw),
        "duration": _duration_seconds(raw) or 0,
        "source": source,
        "container": container,
        "reason": reason,
        "bucket": raw.get("__bucket__", source),
        "seed": raw.get("__seed__", ""),
        "query": raw.get("__query__", ""),
    }


def _collect_tracks(
    candidates: list[dict[str, Any]],
    source: str,
    quota: int,
    per_container: int,
    per_artist: int,
    yt: YTMusic | None,
    min_duration: int,
    max_duration: int,
    archive: Archive,
    seen_video_ids: set[str],
    seen_keys: set[tuple[str, str]],
    return_funnel: bool = False,
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], dict[str, int]]:
    """Apply filters, caps and deduplication to a list of candidate tracks."""
    result: list[dict[str, Any]] = []
    container_counts: dict[tuple[str, str], int] = {}
    artist_counts: dict[str, int] = {}

    funnel: dict[str, int] = {
        "resolved": len(candidates),
        "type": 0,
        "duration": 0,
        "dedupe": 0,
        "caps": 0,
    }

    for raw in candidates:
        video_id = raw.get("videoId")
        if not video_id:
            continue
        if video_id in archive:
            continue
        if video_id in seen_video_ids:
            continue

        video_type = _video_type(raw, yt)
        if video_type not in ALLOWED_VIDEO_TYPES:
            continue
        funnel["type"] += 1

        duration = _duration_seconds(raw, yt)
        if duration is None or not (min_duration <= duration <= max_duration):
            continue
        funnel["duration"] += 1

        key = _track_key(raw)
        if key in seen_keys:
            continue
        funnel["dedupe"] += 1

        container = raw.get("__container__", source)
        artist = _artist_name(raw)

        if container_counts.get((source, container), 0) >= per_container:
            continue
        if artist_counts.get(artist, 0) >= per_artist:
            continue
        funnel["caps"] += 1

        container_counts[(source, container)] = container_counts.get((source, container), 0) + 1
        artist_counts[artist] = artist_counts.get(artist, 0) + 1
        seen_video_ids.add(video_id)
        seen_keys.add(key)
        result.append(
            _to_track(
                raw,
                source,
                container,
                raw.get("__reason__", source),
            )
        )
        if len(result) >= quota:
            break

    if return_funnel:
        return result, funnel
    return result


def _resolve_playlist_tracks(
    yt: YTMusic,
    playlist_id: str,
    container: str,
    reason: str,
    limit: int,
) -> list[dict[str, Any]]:
    try:
        data = yt.get_playlist(playlistId=playlist_id, limit=limit)
    except Exception as exc:
        logger.warning("discovery source failed: %s", exc)
        return []
    tracks = []
    for t in data.get("tracks", []):
        t["__container__"] = container
        t["__reason__"] = reason
        tracks.append(t)
    return tracks


def _resolve_album_tracks(
    yt: YTMusic,
    browse_id: str,
    container: str,
    reason: str,
    limit: int,
) -> list[dict[str, Any]]:
    try:
        data = yt.get_album(browseId=browse_id)
    except Exception as exc:
        logger.warning("discovery source failed: %s", exc)
        return []
    tracks = []
    album_artists = data.get("artists") or []
    for t in data.get("tracks", [])[:limit]:
        t["__container__"] = container
        t["__reason__"] = reason
        if album_artists:
            # Album tracks can list a featured artist first; the album artist
            # is the one that owns the folder.
            t["artists"] = album_artists
        tracks.append(t)
    return tracks


def discover_charts(
    yt: YTMusic,
    country: str,
    quota: int,
    per_container: int,
    per_artist: int,
    min_duration: int,
    max_duration: int,
    archive: Archive,
    seen_video_ids: set[str],
    seen_keys: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    try:
        charts = yt.get_charts(country=country)
    except Exception as exc:
        logger.warning("discovery source failed: %s", exc)
        return []
    videos = charts.get("videos")
    if not videos or not videos[0].get("playlistId"):
        return []
    playlist_id = videos[0]["playlistId"]
    container = videos[0].get("title", f"charts-{country}")
    candidates = _resolve_playlist_tracks(
        yt,
        playlist_id,
        container,
        f"chart {country}",
        limit=max(quota * 2, 50),
    )
    return _collect_tracks(
        candidates,
        "charts",
        quota,
        per_container,
        per_artist,
        yt,
        min_duration,
        max_duration,
        archive,
        seen_video_ids,
        seen_keys,
    )


def discover_new_releases(
    yt: YTMusic,
    quota: int,
    per_container: int,
    per_artist: int,
    min_duration: int,
    max_duration: int,
    archive: Archive,
    seen_video_ids: set[str],
    seen_keys: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    try:
        explore = yt.get_explore()
    except Exception as exc:
        logger.warning("discovery source failed: %s", exc)
        return []
    candidates: list[dict[str, Any]] = []
    for release in explore.get("new_releases", []):
        browse_id = release.get("browseId")
        if not browse_id:
            continue
        container = release.get("title", "Unknown Release")
        candidates.extend(
            _resolve_album_tracks(
                yt,
                browse_id,
                container,
                f"new release ({release.get('type', 'Album')})",
                limit=per_container * 2,
            )
        )
        if len(candidates) >= quota * 3:
            break
    return _collect_tracks(
        candidates,
        "new_releases",
        quota,
        per_container,
        per_artist,
        yt,
        min_duration,
        max_duration,
        archive,
        seen_video_ids,
        seen_keys,
    )


def discover_moods(
    yt: YTMusic,
    quota: int,
    per_container: int,
    per_artist: int,
    min_duration: int,
    max_duration: int,
    archive: Archive,
    seen_video_ids: set[str],
    seen_keys: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    try:
        categories = yt.get_mood_categories()
    except Exception as exc:
        logger.warning("discovery source failed: %s", exc)
        return []
    candidates: list[dict[str, Any]] = []
    moods_iterated = 0
    for group, moods in categories.items():
        for mood in moods[:3]:  # a few moods per group is enough
            try:
                playlists = yt.get_mood_playlists(mood["params"])
            except Exception as exc:
                logger.debug("optional playlist lookup failed: %s", exc)
                continue
            for pl in playlists[:3]:
                playlist_id = pl.get("playlistId")
                if not playlist_id:
                    continue
                candidates.extend(
                    _resolve_playlist_tracks(
                        yt,
                        playlist_id,
                        pl.get("title", "Unknown Playlist"),
                        f"mood: {mood['title']}",
                        limit=per_container * 2,
                    )
                )
            moods_iterated += 1
            if moods_iterated >= 3 or len(candidates) >= quota * 3:
                break
        if len(candidates) >= quota * 3:
            break
    return _collect_tracks(
        candidates,
        "moods",
        quota,
        per_container,
        per_artist,
        yt,
        min_duration,
        max_duration,
        archive,
        seen_video_ids,
        seen_keys,
    )


def discover_home(
    yt: YTMusic,
    quota: int,
    per_container: int,
    per_artist: int,
    min_duration: int,
    max_duration: int,
    archive: Archive,
    seen_video_ids: set[str],
    seen_keys: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    try:
        sections = yt.get_home(limit=10)
    except Exception as exc:
        logger.warning("discovery source failed: %s", exc)
        return []
    candidates: list[dict[str, Any]] = []
    for section in sections:
        section_title = section.get("title", "Home")
        for item in section.get("contents", [])[:15]:
            if not item:
                continue
            # Skip podcasts / episodes outright.
            if item.get("podcastId") or "podcast" in str(item.get("type", "")).lower():
                continue

            if item.get("videoId"):
                item["__container__"] = section_title
                item["__reason__"] = f"home: {section_title}"
                candidates.append(item)
            elif item.get("browseId"):
                candidates.extend(
                    _resolve_album_tracks(
                        yt,
                        item["browseId"],
                        item.get("title", section_title),
                        f"home album: {section_title}",
                        limit=per_container * 2,
                    )
                )
            elif item.get("playlistId"):
                candidates.extend(
                    _resolve_playlist_tracks(
                        yt,
                        item["playlistId"],
                        item.get("title", section_title),
                        f"home playlist: {section_title}",
                        limit=per_container * 2,
                    )
                )
        if len(candidates) >= quota * 3:
            break
    return _collect_tracks(
        candidates,
        "home",
        quota,
        per_container,
        per_artist,
        yt,
        min_duration,
        max_duration,
        archive,
        seen_video_ids,
        seen_keys,
    )


def run_discover(
    headers_path: str,
    sources: list[str],
    country: str,
    quota: int,
    per_container: int,
    per_artist: int,
    min_duration: int,
    max_duration: int,
    archive_path: str,
    cookies: str | None,
    cookies_from_browser: str | None,
    browser_profile: str | None,
    max_size: str | None,
    singles_dir: str = SINGLES_DIR_DEFAULT,
) -> tuple[Manifest, dict[str, Any]]:
    yt = YTMusic(headers_path)
    archive = Archive(archive_path)
    seen_video_ids: set[str] = set()
    seen_keys: set[tuple[str, str]] = set()

    all_tracks: list[dict[str, Any]] = []
    per_source: dict[str, list[dict[str, Any]]] = {}

    source_map = {
        "home": discover_home,
        "charts": discover_charts,
        "moods": discover_moods,
        "new_releases": discover_new_releases,
    }

    for source in sources:
        if source == "charts":
            tracks = discover_charts(
                yt,
                country,
                quota,
                per_container,
                per_artist,
                min_duration,
                max_duration,
                archive,
                seen_video_ids,
                seen_keys,
            )
        else:
            func = source_map.get(source)
            if not func:
                continue
            tracks = func(
                yt,
                quota,
                per_container,
                per_artist,
                min_duration,
                max_duration,
                archive,
                seen_video_ids,
                seen_keys,
            )
        per_source[source] = tracks
        all_tracks.extend(tracks)
        # Update global dedupe sets.
        for t in tracks:
            seen_video_ids.add(t["video_id"])
            seen_keys.add((_normalize(t["artist"]), _normalize(t["title"])))

    # Estimate sizes from the already-known durations.  Do not run yt-dlp
    # once per track here: that made discovery startup network-bound and was
    # repeated again by download_track.  The real max-size check happens in
    # download_track, where the actual format size is available.
    manifest = Manifest()
    total_est = 0
    skipped_size = 0
    for t in all_tracks:
        est = heuristic_size(int(t.get("duration") or 0)) or None
        if t["video_id"] not in archive:
            total_est += est or 0
        manifest.add(
            t["video_id"],
            t["title"],
            t["artist"],
            t["album"],
            est,
            "pending" if t["video_id"] not in archive else "archived",
            path=None,
            source=t["source"],
            container=t["container"],
            reason=t["reason"],
            singles_dir=singles_dir,
        )

    summary = {
        "total": len(all_tracks),
        "per_source": {k: len(v) for k, v in per_source.items()},
        "estimated_size": total_est,
        "estimated_size_human": format_size(total_est),
        "skipped_by_size": skipped_size,
        "top_artists": _top_artists(all_tracks),
    }
    return manifest, summary


def _top_artists(tracks: list[dict[str, Any]], n: int = 10) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for t in tracks:
        counts[t["artist"]] = counts.get(t["artist"], 0) + 1
    return sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:n]


def download_pending(
    manifest: Manifest,
    output_root: pathlib.Path,
    cookies: str | None,
    cookies_from_browser: str | None,
    browser_profile: str | None,
    archive_path: str,
    delay: float,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    control: Any = None,
    limit: int | None = None,
    max_size: int | None = None,
) -> int:
    """Download the pending tracks from a discovery manifest.

    Delegates to run_batch_download so behavior matches sync/artist/queue
    downloads (events, pause/resume, archive, retry rules).
    """
    pending = [e for e in manifest.entries if e.get("status") == "pending"]
    total_pending = len(pending)
    if limit and total_pending > limit:
        pending = pending[:limit]
    if not pending:
        print("no pending tracks to download")
        return 0
    if limit and total_pending > limit:
        print(f"limit applied: {limit} of {total_pending} new tracks")

    manifest_path = output_root / "playlists" / "discover.manifest.json"
    m3u8_path = output_root / "playlists" / "discover.m3u8"

    def save_callback(current_entries: list[dict[str, Any]]) -> None:
        # current_entries is the pending subset; the original dicts are mutated
        # in place, so manifest.entries already reflects the updates. Build the
        # playlist from the whole manifest so resumed runs keep earlier tracks.
        manifest.write(manifest_path, "json")
        downloaded = [
            m3u8_entry(e)
            for e in manifest.entries
            if e.get("status") == "downloaded" and e.get("path")
        ]
        if downloaded:
            write_m3u8(m3u8_path, downloaded)

    summary = run_batch_download(
        pending,
        output_root=output_root,
        save_callback=save_callback,
        cookies=cookies,
        cookies_from_browser=cookies_from_browser,
        browser_profile=browser_profile,
        delay=delay,
        max_size=max_size,
        event_callback=event_callback,
        control=control,
        archive_path=archive_path,
        mode="discover",
    )

    print("\ndiscover download report:")
    for status, count in manifest.counts().items():
        if count:
            print(f"  {status}: {count}")
    return exit_code_from_result(summary)
