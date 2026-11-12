"""Phase 4 discovery: taste-adjacent music beyond YT Music's own sections."""

from __future__ import annotations

import json
import pathlib
import time
from collections import Counter
from typing import Any

from ytmusicapi import YTMusic

from packrat.archive import Archive
from packrat.discover import (
    ALLOWED_VIDEO_TYPES,
    _collect_tracks,
    _resolve_album_tracks,
    _resolve_playlist_tracks,
)
from packrat.normalize import (
    artist_name as _artist_name,
    normalize as _normalize,
    track_key as _track_key,
)
from packrat.lastfm import LastFMClient
from packrat.manifest import Manifest, atomic_write_text
from packrat import paths
from packrat.sizes import format_size, heuristic_size, parse_size


class _DummyArchive:
    """Drop-in replacement for Archive when we only need the set API."""

    def __init__(self, ids: set[str] | list[str]) -> None:
        self._ids = set(ids)

    def __contains__(self, video_id: object) -> bool:
        return str(video_id) in self._ids

    def ids(self) -> set[str]:
        return set(self._ids)


def _load_manifest_ids(path: pathlib.Path | str) -> set[str]:
    p = pathlib.Path(path)
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {e["video_id"] for e in data.get("entries", []) if e.get("video_id")}
    except Exception:
        return set()


def _load_manifest_keys(path: pathlib.Path | str) -> set[tuple[str, str]]:
    p = pathlib.Path(path)
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {
            (_normalize(e.get("artist", "")), _normalize(e.get("title", "")))
            for e in data.get("entries", [])
        }
    except Exception:
        return set()


def _build_artist_families(
    yt: YTMusic,
    profile: dict[str, Any],
    max_top: int = 20,
) -> dict[str, list[str]]:
    """Map discovered-artist name -> liked artists they are related to.

    Only top liked artists with browseIds are checked; family relationships
    beyond those are not detectable via ytmusicapi.
    """
    families: dict[str, list[str]] = {}
    checked = 0
    for entry in profile.get("top_artists", [])[:max_top]:
        name = entry["name"]
        artist_id = profile["artist_ids"].get(name)
        if not artist_id:
            continue
        try:
            info = yt.get_artist(artist_id)
        except Exception:
            continue
        checked += 1
        for related in info.get("related", {}).get("results", []):
            rel_name = related.get("title")
            if not rel_name:
                continue
            families.setdefault(rel_name, []).append(name)
    profile["_family_checked"] = checked
    return families


def _is_official_music(raw: dict[str, Any]) -> bool:
    return (raw.get("videoType") or raw.get("musicVideoType")) in ALLOWED_VIDEO_TYPES


def _artist_id_map(tracks: list[dict[str, Any]]) -> dict[str, str]:
    """Map first-artist name -> browseId from tracks that have ids."""
    out: dict[str, str] = {}
    for t in tracks:
        for a in t.get("artists") or []:
            if a.get("id") and a.get("name"):
                out[a["name"]] = a["id"]
                break
    return out


def _collaborator_ids(tracks: list[dict[str, Any]], main_artists: set[str]) -> dict[str, str]:
    """Collect featured/collaborator artist ids that are not the main liked artists."""
    out: dict[str, str] = {}
    for t in tracks:
        artists = t.get("artists") or []
        if len(artists) <= 1:
            continue
        for a in artists[1:]:
            name = a.get("name")
            if name and name not in main_artists and a.get("id"):
                out[name] = a["id"]
    return out


def build_profile(
    yt: YTMusic,
    archive: Archive,
    discover_manifest_path: pathlib.Path | str | None,
    output_root: pathlib.Path,
) -> dict[str, Any]:
    """Build a taste profile from likes, library, history, subscriptions, and Phase 3."""
    liked = yt.get_liked_songs(limit=None).get("tracks", [])
    liked_music = [t for t in liked if _is_official_music(t)]

    library_artists = yt.get_library_artists(limit=1000)
    library_albums = yt.get_library_albums(limit=1000)
    subscriptions = yt.get_library_subscriptions(limit=1000)
    history = yt.get_history()
    history_music = [t for t in history if _is_official_music(t)]

    artist_counter: Counter[str] = Counter()
    for t in liked_music:
        name = _artist_name(t)
        if name and name != "Unknown Artist":
            artist_counter[name] += 1
    for a in library_artists:
        name = a.get("artist")
        if name:
            artist_counter[name] += 1
    for s in subscriptions:
        name = s.get("artist")
        if name:
            artist_counter[name] += 1

    top_artists = artist_counter.most_common(50)
    artist_ids = _artist_id_map(liked_music)
    # Also add ids from library/subscription artists if missing.
    for a in library_artists:
        name = a.get("artist")
        if name and a.get("browseId") and name not in artist_ids:
            artist_ids[name] = a["browseId"]
    for s in subscriptions:
        name = s.get("artist")
        if name and s.get("browseId") and name not in artist_ids:
            artist_ids[name] = s["browseId"]

    liked_video_ids = {t["videoId"] for t in liked_music if t.get("videoId")}
    history_video_ids = {t["videoId"] for t in history_music if t.get("videoId")}
    archive_ids = archive.ids()
    discover_ids = _load_manifest_ids(discover_manifest_path) if discover_manifest_path else set()
    library_video_ids = liked_video_ids | history_video_ids | archive_ids | discover_ids

    liked_keys = {_track_key(t) for t in liked_music}
    history_keys = {_track_key(t) for t in history_music}
    discover_keys = _load_manifest_keys(discover_manifest_path) if discover_manifest_path else set()
    seen_keys = liked_keys | history_keys | discover_keys

    # Optional Last.fm tags for the top artists.
    lastfm = LastFMClient()
    tag_counter: Counter[str] = Counter()
    if lastfm.available():
        for name, _ in top_artists[:15]:
            for tag in lastfm.get_top_tags(name, limit=5):
                if tag:
                    tag_counter[tag.lower()] += 1

    years = Counter()
    for alb in library_albums:
        y = alb.get("year")
        if y:
            years[str(y)[:4]] += 1
    for t in liked_music:
        y = t.get("year")
        if not y:
            album = t.get("album")
            if isinstance(album, dict):
                y = album.get("year")
        if y:
            years[str(y)[:4]] += 1

    profile: dict[str, Any] = {
        "liked_count_total": len(liked),
        "liked_official_count": len(liked_music),
        "library_artist_count": len(library_artists),
        "library_album_count": len(library_albums),
        "subscription_count": len(subscriptions),
        "history_count": len(history),
        "top_artists": [{"name": name, "count": count} for name, count in top_artists[:30]],
        "artist_ids": artist_ids,
        "library_video_ids": sorted(library_video_ids),
        "library_keys": [list(k) for k in sorted(seen_keys)],
        "tags": [{"name": name, "count": count} for name, count in tag_counter.most_common(20)],
        "years": [{"year": y, "count": c} for y, c in years.most_common(10)],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    profile_path = paths.PROFILE_PATH
    paths.ensure_config_dir()
    atomic_write_text(
        profile_path,
        json.dumps(profile, indent=2, ensure_ascii=False),
    )

    return profile


def _score_components(
    track: dict[str, Any],
    profile: dict[str, Any],
    phase3_ids: set[str],
    candidate_tags: dict[str, set[str]],
) -> dict[str, int]:
    comps: dict[str, int] = {}
    artist = _normalize(track.get("artist", _artist_name(track)))
    if artist in {_normalize(a) for a in profile["artist_ids"].keys()}:
        comps["profile_artist"] = 1
    vid = track.get("video_id")
    tags = candidate_tags.get(vid or "", set())
    profile_tags = {t["name"] for t in profile.get("tags", [])}
    if tags & profile_tags:
        comps["tag_overlap"] = 1
    if vid in phase3_ids:
        comps["bubble_penalty"] = -1
    return comps


def _finish_bucket(
    candidates: list[dict[str, Any]],
    bucket: str,
    quota: int,
    per_container: int,
    per_artist: int,
    yt: YTMusic,
    min_duration: int,
    max_duration: int,
    profile: dict[str, Any],
    seen_video_ids: set[str],
    seen_keys: set[tuple[str, str]],
) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
    selected, funnel = _collect_tracks(
        candidates, bucket, quota, per_container, per_artist, yt,
        min_duration, max_duration,
        _DummyArchive(profile["library_video_ids"]),
        seen_video_ids, seen_keys, return_funnel=True,
    )
    return selected, len(candidates), funnel


def _bucket_deep_cuts(
    yt: YTMusic,
    profile: dict[str, Any],
    quota: int,
    per_container: int,
    per_artist: int,
    min_duration: int,
    max_duration: int,
    seen_video_ids: set[str],
    seen_keys: set[tuple[str, str]],
) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
    candidates: list[dict[str, Any]] = []
    max_artists = min(15, len(profile["top_artists"]))
    for entry in profile["top_artists"][:max_artists]:
        artist_name = entry["name"]
        artist_id = profile["artist_ids"].get(artist_name)
        if not artist_id:
            continue
        try:
            info = yt.get_artist(artist_id)
        except Exception:
            continue

        # Exclude the artist's own top songs from "deep cut" definition.
        top_ids: set[str] = set()
        songs = info.get("songs", {})
        if songs and songs.get("browseId"):
            for t in _resolve_playlist_tracks(
                yt,
                songs["browseId"],
                "top songs",
                f"top songs of {artist_name}",
                limit=25,
            ):
                if t.get("videoId"):
                    top_ids.add(t["videoId"])

        albums = info.get("albums", {}).get("results", []) or []
        for album in albums[:6]:
            browse_id = album.get("browseId")
            if not browse_id:
                continue
            container = album.get("title", "Unknown Album")
            reason = f"deep cut from {artist_name}: {container}"
            for t in _resolve_album_tracks(
                yt,
                browse_id,
                container,
                reason,
                limit=per_container * 3,
            ):
                if t.get("videoId") in top_ids:
                    continue
                if t.get("videoId") in profile["library_video_ids"]:
                    continue
                t["__container__"] = container
                t["__reason__"] = reason
                t["__bucket__"] = "deep_cuts"
                t["__seed__"] = artist_name
                t["__query__"] = ""
                candidates.append(t)
        if len(candidates) >= quota * 6:
            break
    return _finish_bucket(
        candidates, "deep_cuts", quota, per_container, per_artist, yt,
        min_duration, max_duration, profile, seen_video_ids, seen_keys,
    )


def _bucket_adjacent(
    yt: YTMusic,
    profile: dict[str, Any],
    quota: int,
    per_container: int,
    per_artist: int,
    min_duration: int,
    max_duration: int,
    seen_video_ids: set[str],
    seen_keys: set[tuple[str, str]],
) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
    candidates: list[dict[str, Any]] = []
    main_artists = {a["name"] for a in profile.get("top_artists", [])}
    related: dict[str, str] = {}

    # Related artists from get_artist for top liked artists.
    for entry in profile["top_artists"][:10]:
        artist_id = profile["artist_ids"].get(entry["name"])
        if not artist_id:
            continue
        try:
            info = yt.get_artist(artist_id)
        except Exception:
            continue
        for r in (info.get("related", {}) or {}).get("results", []) or []:
            name = r.get("title")
            bid = r.get("browseId")
            if name and bid and name not in main_artists and name not in related:
                related[name] = bid

    # Collaborators / featured artists on liked tracks.
    related.update(_collaborator_ids(profile.get("_liked_music", []), main_artists))

    for name, bid in list(related.items())[:quota * 3]:
        try:
            info = yt.get_artist(bid)
        except Exception:
            continue
        albums = info.get("albums", {}).get("results", []) or []
        for album in albums[:3]:
            browse_id = album.get("browseId")
            if not browse_id:
                continue
            container = album.get("title", "Unknown Album")
            reason = f"adjacent artist {name}: {container}"
            for t in _resolve_album_tracks(
                yt,
                browse_id,
                container,
                reason,
                limit=per_container * 2,
            ):
                t["__container__"] = container
                t["__reason__"] = reason
                t["__bucket__"] = "adjacent"
                t["__seed__"] = name
                t["__query__"] = ""
                candidates.append(t)
        if len(candidates) >= quota * 5:
            break

    return _finish_bucket(
        candidates, "adjacent", quota, per_container, per_artist, yt,
        min_duration, max_duration, profile, seen_video_ids, seen_keys,
    )


def _pick_radio_seeds(liked_music: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Seed radios from less-obvious liked tracks (lower artist frequency first)."""
    artist_counts = Counter(_artist_name(t) for t in liked_music)
    sorted_tracks = sorted(liked_music, key=lambda t: artist_counts[_artist_name(t)])
    return sorted_tracks[:15]


def _bucket_radio(
    yt: YTMusic,
    profile: dict[str, Any],
    quota: int,
    per_container: int,
    per_artist: int,
    min_duration: int,
    max_duration: int,
    seen_video_ids: set[str],
    seen_keys: set[tuple[str, str]],
) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
    candidates: list[dict[str, Any]] = []
    liked_music = profile.get("_liked_music", [])
    seeds = _pick_radio_seeds(liked_music)
    for seed in seeds:
        vid = seed.get("videoId")
        if not vid:
            continue
        seed_artist = _artist_name(seed)
        seed_title = seed.get("title", "Unknown")
        try:
            data = yt.get_watch_playlist(videoId=vid, limit=25)
        except Exception:
            continue
        container = f"radio from {seed_title}"
        for t in data.get("tracks", [])[1:]:  # first item is usually the seed itself
            if not t or not t.get("videoId"):
                continue
            if t["videoId"] == vid or t["videoId"] in profile["library_video_ids"]:
                continue
            t["__container__"] = container
            t["__reason__"] = f"radio seeded by {seed_title} by {seed_artist}"
            t["__bucket__"] = "radio"
            t["__seed__"] = f"{seed_title} by {seed_artist}"
            t["__query__"] = ""
            candidates.append(t)
        if len(candidates) >= quota * 5:
            break

    return _finish_bucket(
        candidates, "radio", quota, per_container, per_artist, yt,
        min_duration, max_duration, profile, seen_video_ids, seen_keys,
    )


def _diverse_seeds(profile: dict[str, Any], total: int = 20) -> list[str]:
    """Pick a mix of top and tail liked artists to seed Last.fm exploration."""
    liked = profile.get("_liked_music", [])
    from collections import Counter

    counts: Counter[str] = Counter()
    for t in liked:
        name = _artist_name(t)
        if name and name != "Unknown Artist":
            counts[name] += 1
    sorted_artists = [a for a, _ in counts.most_common()]
    top = sorted_artists[: total // 2]
    tail = sorted_artists[total // 2 :]
    step = max(1, len(tail) // (total - len(top))) if tail else 1
    tail_picks = tail[::step][: total - len(top)]
    return top + tail_picks


def _bucket_external_graph(
    yt: YTMusic,
    profile: dict[str, Any],
    lastfm: LastFMClient,
    quota: int,
    per_container: int,
    per_artist: int,
    min_duration: int,
    max_duration: int,
    seen_video_ids: set[str],
    seen_keys: set[tuple[str, str]],
    similar_limit: int = 10,
) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
    candidates: list[dict[str, Any]] = []
    if not lastfm.available():
        return candidates, 0, {"resolved": 0, "type": 0, "duration": 0, "dedupe": 0, "caps": 0}

    main_artists = {a["name"] for a in profile.get("top_artists", [])}
    similar_seen: set[str] = set()
    seeds = _diverse_seeds(profile, total=20)
    for seed in seeds:
        for sim in lastfm.get_similar_artists(seed, limit=similar_limit):
            if not sim or sim in main_artists or sim in similar_seen:
                continue
            similar_seen.add(sim)
            try:
                results = yt.search(query=sim, filter="songs", limit=10)
            except Exception:
                continue
            found_for_sim = 0
            for t in results:
                if not _is_official_music(t):
                    continue
                if _normalize(_artist_name(t)) != _normalize(sim):
                    continue
                vid = t.get("videoId")
                if not vid or vid in profile["library_video_ids"]:
                    continue
                t["__container__"] = f"similar to {seed}"
                t["__reason__"] = f"Last.fm similar to {seed}"
                t["__bucket__"] = "external_graph"
                t["__seed__"] = sim
                t["__query__"] = sim
                candidates.append(t)
                found_for_sim += 1
                if found_for_sim >= 2:
                    break
        if len(candidates) >= quota * 3:
            break

    return _finish_bucket(
        candidates, "external_graph", quota, per_container, per_artist, yt,
        min_duration, max_duration, profile, seen_video_ids, seen_keys,
    )


def _bucket_tag_search(
    yt: YTMusic,
    profile: dict[str, Any],
    lastfm: LastFMClient,
    quota: int,
    per_container: int,
    per_artist: int,
    min_duration: int,
    max_duration: int,
    seen_video_ids: set[str],
    seen_keys: set[tuple[str, str]],
) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
    candidates: list[dict[str, Any]] = []
    profile_tags = [t["name"] for t in profile.get("tags", [])]
    if not profile_tags:
        # Try to build tags from Last.fm if not already in profile.
        if lastfm.available():
            tag_counter: Counter[str] = Counter()
            for entry in profile["top_artists"][:15]:
                for tag in lastfm.get_top_tags(entry["name"], limit=5):
                    tag_counter[tag.lower()] += 1
            profile_tags = [name for name, _ in tag_counter.most_common(10)]
    if not profile_tags:
        return candidates, 0, {}

    for tag in profile_tags[:5]:
        try:
            results = yt.search(query=tag, filter="songs", limit=10)
        except Exception:
            continue
        container = f"tag search: {tag}"
        for t in results:
            if not _is_official_music(t):
                continue
            vid = t.get("videoId")
            if not vid or vid in profile["library_video_ids"]:
                continue
            t["__container__"] = container
            t["__reason__"] = f"tag search: {tag}"
            t["__bucket__"] = "tag_search"
            t["__seed__"] = tag
            t["__query__"] = tag
            candidates.append(t)
        if len(candidates) >= quota * 3:
            break

    return _finish_bucket(
        candidates, "tag_search", quota, per_container, per_artist, yt,
        min_duration, max_duration, profile, seen_video_ids, seen_keys,
    )

def _tag_candidates(
    candidates: list[dict[str, Any]],
    lastfm: LastFMClient,
) -> dict[str, set[str]]:
    """Fetch Last.fm tags for candidate artists to enable tag-overlap scoring."""
    out: dict[str, set[str]] = {}
    if not lastfm.available():
        return out
    for raw in candidates:
        vid = raw.get("videoId")
        artist = _artist_name(raw)
        if vid and artist:
            out[vid] = {t.lower() for t in lastfm.get_top_tags(artist, limit=5)}
    return out


def run_discover_phase4(
    headers_path: str,
    output_root: str,
    archive_path: str,
    discover_manifest_path: str | None,
    quotas: dict[str, int],
    per_container: int,
    per_artist: int,
    family_cap: int,
    min_duration: int,
    max_duration: int,
    max_size: str | None,
    external_similar: int = 10,
) -> tuple[Manifest, dict[str, Any]]:
    yt = YTMusic(headers_path)
    archive = Archive(archive_path)
    out = pathlib.Path(output_root)
    out.mkdir(parents=True, exist_ok=True)

    profile = build_profile(yt, archive, discover_manifest_path, out)
    # Keep raw liked music for internal helpers.
    profile["_liked_music"] = [
        t for t in yt.get_liked_songs(limit=None).get("tracks", []) if _is_official_music(t)
    ]

    # Build artist-family map where detectable (ytmusicapi only exposes related
    # artists for top liked artists with browseIds).
    profile["_artist_families"] = _build_artist_families(yt, profile)

    phase3_ids = _load_manifest_ids(discover_manifest_path) if discover_manifest_path else set()

    seen_video_ids: set[str] = set(profile["library_video_ids"])
    seen_keys: set[tuple[str, str]] = {tuple(k) for k in profile["library_keys"]}

    lastfm = LastFMClient()

    bucket_results: dict[str, dict[str, Any]] = {}
    all_candidates: list[dict[str, Any]] = []
    candidate_buckets: dict[str, set[str]] = {}

    buckets = [
        ("deep_cuts", _bucket_deep_cuts),
        ("adjacent", _bucket_adjacent),
        ("radio", _bucket_radio),
        ("external_graph", _bucket_external_graph),
        ("tag_search", _bucket_tag_search),
    ]

    for name, func in buckets:
        if name in ("external_graph", "tag_search") and not lastfm.available():
            bucket_results[name] = {"selected": [], "candidates": 0, "funnel": {}}
            continue
        try:
            if name == "external_graph":
                selected, cand_count, funnel = func(
                    yt,
                    profile,
                    lastfm,
                    quotas.get(name, 10),
                    per_container,
                    per_artist,
                    min_duration,
                    max_duration,
                    seen_video_ids,
                    seen_keys,
                    similar_limit=external_similar,
                )
            elif name == "tag_search":
                selected, cand_count, funnel = func(
                    yt,
                    profile,
                    lastfm,
                    quotas.get(name, 5),
                    per_container,
                    per_artist,
                    min_duration,
                    max_duration,
                    seen_video_ids,
                    seen_keys,
                )
            else:
                selected, cand_count, funnel = func(
                    yt,
                    profile,
                    quotas.get(name, 10),
                    per_container,
                    per_artist,
                    min_duration,
                    max_duration,
                    seen_video_ids,
                    seen_keys,
                )
        except Exception as exc:
            selected, cand_count, funnel = [], 0, {}
            bucket_results[name] = {"selected": selected, "candidates": cand_count, "funnel": funnel, "error": str(exc)}
            continue
        bucket_results[name] = {"selected": selected, "candidates": cand_count, "funnel": funnel}
        for raw in selected:
            vid = raw.get("video_id") or raw.get("videoId")
            if not vid:
                continue
            candidate_buckets.setdefault(vid, set()).add(name)
        all_candidates.extend(selected)

    # Score and sort all selected candidates globally.
    candidate_tags = _tag_candidates(all_candidates, lastfm)
    scored = []
    for track in all_candidates:
        comps = _score_components(track, profile, phase3_ids, candidate_tags)
        multi = len(candidate_buckets.get(track["video_id"], set())) - 1
        if multi > 0:
            comps["multi_bucket"] = multi
        scored.append((sum(comps.values()), track, comps, multi))
    scored.sort(key=lambda x: x[0], reverse=True)

    max_size_bytes = parse_size(max_size) if max_size else None
    manifest = Manifest()
    artist_counts: Counter[str] = Counter()
    album_counts: Counter[tuple[str, str]] = Counter()
    final_selected: list[dict[str, Any]] = []
    skipped_by_size = 0

    families = profile.get("_artist_families", {})
    family_counts: dict[str, int] = Counter()

    for _score, track, comps, multi in scored:
        artist = track.get("artist", "")
        album = track.get("album", "")

        # Per-artist cap.
        if artist_counts[artist] >= per_artist:
            continue

        # Album cap (hard 2).
        if album_counts[(artist, album)] >= 2:
            continue

        # Artist-family cap where detectable.
        related_to = families.get(artist, [])
        if related_to and family_cap > 0:
            family_total = sum(family_counts.get(rel, 0) for rel in related_to)
            if family_total >= family_cap:
                continue
            for rel in related_to:
                family_counts[rel] += 1

        artist_counts[artist] += 1
        album_counts[(artist, album)] += 1

        duration = track.get("duration") or 0
        est_size = heuristic_size(duration)
        if max_size_bytes is not None and est_size > max_size_bytes:
            status = "skipped_size"
            skipped_by_size += 1
        else:
            status = "pending"

        entry = dict(track)
        entry.update({
            "score": _score,
            "score_components": comps,
            "est_size": est_size,
        })
        manifest.add(
            video_id=entry["video_id"],
            title=entry["title"],
            artist=entry["artist"],
            album=entry["album"],
            est_size=est_size,
            status=status,
            path=None,
            bucket=entry.get("bucket", "phase4"),
            seed=entry.get("seed", ""),
            query=entry.get("query", ""),
            score=entry["score"],
            score_components=entry["score_components"],
            reason=entry.get("reason", ""),
            duration=duration,
        )
        final_selected.append(entry)

    total_size = manifest.total_est_size()
    artist_counter = Counter(t["artist"] for t in final_selected)
    summary = {
        "total": len(final_selected),
        "per_bucket": {name: len(data["selected"]) for name, data in bucket_results.items()},
        "funnel": {name: {"candidates": data["candidates"], "selected": len(data["selected"])} for name, data in bucket_results.items()},
        "estimated_size_bytes": total_size,
        "estimated_size_human": format_size(total_size),
        "skipped_by_size": skipped_by_size,
        "top_artists": artist_counter.most_common(10),
        "profile": {
            "liked_official_count": profile["liked_official_count"],
            "top_artists": [a["name"] for a in profile["top_artists"][:10]],
            "tags": [t["name"] for t in profile["tags"][:10]],
        },
    }
    return manifest, summary
