"""Playback-queue discovery via get_watch_playlist(radio=True)."""

from __future__ import annotations

import os
import pathlib
import random
import re
from collections import Counter
from typing import Any, Callable

from ytmusicapi import YTMusic

from ytm_offline.archive import Archive
from ytm_offline.batch import run_batch_download
from ytm_offline.discover import (
    ALLOWED_VIDEO_TYPES,
    _album_name,
    _artist_name,
    _duration_seconds,
    _normalize,
    _video_type,
)
from ytm_offline.manifest import Manifest
from ytm_offline.naming import clean, track_path
from ytm_offline.playlist import write_m3u8
from ytm_offline.sizes import format_size, heuristic_size, parse_size


def _load_liked_tracks(
    yt: YTMusic,
    min_duration: int,
    max_duration: int,
) -> list[dict[str, Any]]:
    """Return official music liked tracks inside the duration window."""
    liked = yt.get_liked_songs(limit=None)
    tracks: list[dict[str, Any]] = []
    for t in liked.get("tracks", []):
        if not t.get("videoId"):
            continue
        if _video_type(t) not in ALLOWED_VIDEO_TYPES:
            continue
        dur = _duration_seconds(t)
        if dur is None or not (min_duration <= dur <= max_duration):
            continue
        tracks.append(t)
    return tracks


def _select_seeds(
    liked: list[dict[str, Any]],
    count: int,
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Weighted by artist like-count plus a slice of older/low-play tracks."""
    if not liked:
        return []
    artist_counts = Counter(_artist_name(t) for t in liked)
    # Weighted sampling by artist frequency.
    weights = [1 + artist_counts[_artist_name(t)] for t in liked]
    hot = rng.choices(liked, weights=weights, k=min(count // 2, len(liked)))
    # Older / less common picks: sort by year ascending, take first half.
    with_year = [(t, t.get("year") or "9999") for t in liked]
    with_year.sort(key=lambda x: x[1])
    cold_pool = with_year[: max(1, len(with_year) // 2)]
    cold = rng.sample(
        [t for t, _ in cold_pool], k=min(count - len(hot), len(cold_pool))
    )
    seeds = hot + cold
    rng.shuffle(seeds)
    # Deduplicate by video_id.
    seen: set[str] = set()
    unique = []
    for t in seeds:
        vid = t["videoId"]
        if vid not in seen:
            seen.add(vid)
            unique.append(t)
    return unique[:count]


def _filter_queue_track(
    raw: dict[str, Any],
    archive: Archive,
    seen_keys: set[tuple[str, str]],
    min_duration: int,
    max_duration: int,
) -> bool:
    vid = raw.get("videoId")
    if not vid:
        return False
    if vid in archive:
        return False
    if _video_type(raw) not in ALLOWED_VIDEO_TYPES:
        return False
    dur = _duration_seconds(raw)
    if dur is None or not (min_duration <= dur <= max_duration):
        return False
    key = (_normalize(_artist_name(raw)), _normalize(raw.get("title", "")))
    if key in seen_keys:
        return False
    return True


def _build_queue(
    yt: YTMusic,
    seed: dict[str, Any],
    archive: Archive,
    min_duration: int,
    max_duration: int,
    per_artist_per_queue: int,
    root: pathlib.Path,
    seed_artists: set[str],
    max_tracks: int | None = None,
    hop: int = 0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (accepted_tracks, hop_seed_candidates) for one queue."""
    data = yt.get_watch_playlist(
        videoId=seed["videoId"], radio=True, limit=200
    )
    accepted: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    artist_counts: Counter[str] = Counter()
    hop_candidates: list[dict[str, Any]] = []

    for raw in data.get("tracks", []):
        artist = _artist_name(raw)
        if not _filter_queue_track(
            raw, archive, seen_keys, min_duration, max_duration
        ):
            continue
        if artist_counts[artist] >= per_artist_per_queue:
            continue
        if max_tracks is not None and len(accepted) >= max_tracks:
            break
        artist_counts[artist] += 1
        key = (_normalize(artist), _normalize(raw.get("title", "")))
        seen_keys.add(key)
        path = track_path(
            root,
            artist,
            _album_name(raw),
            raw.get("title", "Unknown"),
        )
        vid = raw["videoId"]
        accepted.append(
            {
                "video_id": vid,
                "title": raw.get("title", "Unknown"),
                "artist": artist,
                "album": _album_name(raw),
                "duration": _duration_seconds(raw) or 0,
                "path": str(path),
                "seed": seed.get("title", seed["videoId"]),
                "seed_video_id": seed["videoId"],
                "hop": hop,
                "reason": f"queue from {seed.get('title', seed['videoId'])}",
            }
        )
        # Candidates for a second hop must not be by a seed artist.
        if hop == 0 and artist not in seed_artists:
            hop_candidates.append(raw)

    return accepted, hop_candidates


def discover_queues(
    headers_path: str,
    output_root: pathlib.Path,
    archive_path: str | pathlib.Path,
    seed_count: int = 12,
    hop_depth: int = 1,
    hop_width: int = 3,
    per_artist_per_queue: int = 3,
    per_queue_limit: int = 25,
    total_cap: int = 200,
    min_duration: int = 90,
    max_duration: int = 540,
    random_seed: int | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Build manifests and .m3u8 playlists from playback queues."""
    yt = YTMusic(headers_path)
    archive = Archive(archive_path)
    rng = random.Random(random_seed)

    liked = _load_liked_tracks(yt, min_duration, max_duration)
    seeds = _select_seeds(liked, seed_count, rng)
    seed_artists = {_artist_name(s) for s in seeds}

    playlists_dir = output_root / "playlists" / "queues"
    playlists_dir.mkdir(parents=True, exist_ok=True)

    queue_summaries: list[dict[str, Any]] = []
    all_tracks: list[dict[str, Any]] = []
    global_keys: set[tuple[str, str]] = set()
    total_new = 0

    # Distribute the total cap round-robin across seeds, bounded by per_queue_limit.
    num_seeds = len(seeds)
    base = total_cap // num_seeds if num_seeds else 0
    extra = total_cap - base * num_seeds if num_seeds else 0

    for idx, seed in enumerate(seeds):
        if total_new >= total_cap:
            break
        queue_limit = min(per_queue_limit, base + (1 if idx < extra else 0))
        remaining = min(queue_limit, total_cap - total_new)
        accepted, hop_candidates = _build_queue(
            yt,
            seed,
            archive,
            min_duration,
            max_duration,
            per_artist_per_queue,
            output_root,
            seed_artists,
            max_tracks=remaining,
            hop=0,
        )

        # Optional second hop only if we still have room within this queue's slot.
        if hop_depth > 0 and len(accepted) < remaining:
            rng.shuffle(hop_candidates)
            for hop_seed in hop_candidates[:hop_width]:
                remaining_hop = remaining - len(accepted)
                if remaining_hop <= 0:
                    break
                hop_tracks, _ = _build_queue(
                    yt,
                    hop_seed,
                    archive,
                    min_duration,
                    max_duration,
                    per_artist_per_queue,
                    output_root,
                    seed_artists,
                    max_tracks=remaining_hop,
                    hop=1,
                )
                # Append only tracks not already in this queue by key.
                for t in hop_tracks:
                    key = (_normalize(t["artist"]), _normalize(t["title"]))
                    if key in global_keys:
                        continue
                    global_keys.add(key)
                    accepted.append(t)

        # Register accepted tracks globally.
        for t in accepted:
            key = (_normalize(t["artist"]), _normalize(t["title"]))
            global_keys.add(key)

        total_new += len(accepted)
        all_tracks.extend(accepted)

        # Per-queue m3u8.
        seed_label = str(seed.get("title", seed["videoId"]))
        safe_seed = os.path.basename(seed_label.replace(" ", "_").replace("/", "_"))
        queue_dir = playlists_dir / safe_seed
        queue_dir.mkdir(parents=True, exist_ok=True)
        m3u8_path = queue_dir / f"queue-{safe_seed}.m3u8"
        m3u8_entries = [
            {
                "path": t["path"],
                "duration": t["duration"],
                "title": t["title"],
                "artist": t["artist"],
            }
            for t in accepted
        ]
        write_m3u8(m3u8_path, m3u8_entries)

        queue_summaries.append(
            {
                "seed": seed_label,
                "seed_video_id": seed["videoId"],
                "tracks": len(accepted),
                "unique_artists": len({t["artist"] for t in accepted}),
                "est_size": sum(heuristic_size(t["duration"]) for t in accepted),
                "m3u8": str(m3u8_path),
            }
        )

    # Write manifest.
    manifest = Manifest()
    for t in all_tracks:
        manifest.add(
            video_id=t["video_id"],
            title=t["title"],
            artist=t["artist"],
            album=t["album"],
            est_size=heuristic_size(t["duration"]),
            status="pending" if dry_run else "pending",
            path=t["path"],
            seed=t["seed"],
            seed_video_id=t["seed_video_id"],
            hop=t["hop"],
            reason=t["reason"],
            duration=t["duration"],
        )

    manifest_path = output_root / "playlists" / "queues.manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest.write(manifest_path)

    total_size = manifest.total_est_size()
    return {
        "manifest_path": str(manifest_path),
        "seeds_used": len(seeds),
        "total_tracks": len(all_tracks),
        "total_est_size": total_size,
        "total_est_size_human": format_size(total_size),
        "queues": queue_summaries,
    }


def download_queues(
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
    """Download pending tracks from queues.manifest.json."""
    manifest_path = output_root / "playlists" / "queues.manifest.json"
    if not manifest_path.exists():
        return {"error": f"manifest not found: {manifest_path}"}
    manifest = Manifest.load(manifest_path)
    entries = manifest.pending()
    if not entries:
        return {"error": "no pending tracks in queues manifest"}

    if event_callback:
        event_callback(
            {
                "type": "start",
                "payload": {
                    "mode": "queues",
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
        mode="queues",
    )
    return {"summary": summary, "manifest_path": str(manifest_path)}


_VIDEO_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")


def parse_video_id(spec: str) -> str | None:
    """Extract a video id from a bare id or a YouTube Music/YouTube URL."""
    if re.fullmatch(_VIDEO_ID_RE, spec.strip()):
        return spec.strip()
    m = (
        re.search(r"[?&]v=([A-Za-z0-9_-]{11})", spec)
        or re.search(r"youtu\.be/([A-Za-z0-9_-]{11})", spec)
    )
    if m:
        return m.group(1)
    return None


def build_radio(
    seed_spec: str,
    headers_path: str,
    output_root: pathlib.Path,
    archive_path: str | pathlib.Path,
    limit: int = 50,
    per_artist_per_queue: int = 3,
    min_duration: int = 90,
    max_duration: int = 540,
    dry_run: bool = True,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
    browser_profile: str | None = None,
    max_size: str | None = None,
    delay: float = 7.0,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    control: Any = None,
) -> dict[str, Any]:
    """Build (and optionally download) the radio built from one track.

    SEED may be a video id or a video URL (music.youtube.com, youtu.be, ...).
    Writes per-seed manifest + .m3u8 so runs resume incrementally.
    """
    video_id = parse_video_id(seed_spec)
    if not video_id:
        return {"error": f"cannot parse video id from: {seed_spec}"}

    yt = YTMusic(headers_path)
    archive = Archive(archive_path)

    seed = {"videoId": video_id, "title": video_id}
    try:
        song = yt.get_song(video_id)
        seed["title"] = (song.get("videoDetails") or {}).get("title") or video_id
    except Exception:
        pass

    # Build the radio from the seed, numbers first track like track 1.
    accepted, _ = _build_queue(
        yt,
        seed,
        archive,
        min_duration,
        max_duration,
        per_artist_per_queue,
        output_root,
        set(),
        max_tracks=limit - 1,
        hop=1,
    )
    entries = []
    for t in accepted:
        entries.append(
            {
                "video_id": t["video_id"],
                "title": t["title"],
                "artist": t["artist"],
                "album": t["album"],
                "duration": t["duration"],
                "est_size": heuristic_size(t["duration"]),
                "status": "pending",
                "path": None,
                "error": None,
                "source": f"radio:{video_id}",
            }
        )
    total_est = sum(e["est_size"] for e in entries)

    result: dict[str, Any] = {
        "seed": seed["title"],
        "seed_video_id": video_id,
        "total_tracks": len(entries),
        "total_est_size_human": format_size(total_est),
        "tracks": [
            f"{e['artist']} - {e['title']} ({e['duration']}s)"
            for e in entries
        ],
    }

    if dry_run:
        return result

    playlists_dir = output_root / "playlists" / "radios"
    playlists_dir.mkdir(parents=True, exist_ok=True)
    seed_label = video_id if seed["title"] == video_id else clean(seed["title"])
    manifest_path = output_root / "playlists" / f"radio-{seed_label}.manifest.json"
    m3u8_path = playlists_dir / f"radio-{seed_label}.m3u8"
    manifest = Manifest()
    manifest.entries = entries
    manifest.write(manifest_path)

    if event_callback:
        event_callback(
            {
                "type": "start",
                "payload": {
                    "mode": f"radio:{seed['title']}",
                    "total": len(entries),
                    "total_est_bytes": total_est,
                    "max_size_bytes": parse_size(max_size) if max_size else None,
                },
            }
        )

    def save_callback(current_entries: list[dict[str, Any]]) -> None:
        manifest.entries = current_entries
        manifest.write(manifest_path)
        write_m3u8(
            m3u8_path,
            [
                {
                    "path": e["path"],
                    "duration": e["duration"],
                    "title": e["title"],
                    "artist": e["artist"],
                }
                for e in current_entries
                if e.get("status") == "downloaded" and e.get("path")
            ],
        )

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
        mode=f"radio:{seed['title']}",
    )
    result["summary"] = summary
    result["manifest_path"] = str(manifest_path)
    result["m3u8_path"] = str(m3u8_path)
    return result
