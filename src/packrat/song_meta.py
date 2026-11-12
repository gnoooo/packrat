"""Resolve canonical YouTube Music metadata for download-one."""

from __future__ import annotations

import dataclasses
import pathlib
from typing import Any

from ytmusicapi import YTMusic

from packrat.normalize import normalize


@dataclasses.dataclass(frozen=True)
class SongMetadata:
    video_id: str
    title: str
    artist: str
    album: str
    track_num: int | None = None
    release_type: str | None = None
    year: int | None = None
    duration: int | None = None


def _artist(raw: dict[str, Any]) -> str:
    artists = raw.get("artists") or []
    if artists:
        return str(artists[0].get("name") or "Unknown Artist")
    return str(raw.get("author") or "Unknown Artist")


def _duration(raw: dict[str, Any]) -> int | None:
    value = raw.get("duration_seconds")
    if isinstance(value, int):
        return value
    return None


def _pick_album_track(album: dict[str, Any], video_id: str, title: str) -> dict[str, Any] | None:
    tracks = album.get("tracks") or []
    for track in tracks:
        if track.get("videoId") == video_id:
            return track
    wanted = normalize(title)
    for track in tracks:
        if normalize(track.get("title")) == wanted:
            return track
    return None


def resolve_song_metadata(video_id: str, headers_path: str) -> SongMetadata:
    """Resolve the canonical artist/album/track number for a video.

    YouTube Music often exposes the same recording as an album track and as an
    "Official Audio" music video with a different uploader.  The watch playlist
    gives us the canonical title/artist; searching the song catalog gives the
    album, whose track list gives the track number.
    """
    yt = YTMusic(headers_path)
    watch = yt.get_watch_playlist(videoId=video_id)
    tracks = watch.get("tracks") or []
    current = next((t for t in tracks if t.get("videoId") == video_id), tracks[0] if tracks else {})
    title = str(current.get("title") or "")
    artist = _artist(current)
    duration = _duration(current)

    if not title:
        song = yt.get_song(video_id)
        details = song.get("videoDetails") or {}
        title = str(details.get("title") or "")
        artist = str(details.get("author") or artist)

    album_name = ""
    album_id = ""
    for result in yt.search(f"{title} {artist}", filter="songs", limit=10):
        result_artist = _artist(result)
        if normalize(result_artist) != normalize(artist):
            continue
        result_duration = _duration(result)
        if duration is not None and result_duration is not None and abs(duration - result_duration) > 3:
            continue
        album = result.get("album")
        if isinstance(album, dict) and album.get("id"):
            album_name = str(album.get("name") or "")
            album_id = str(album["id"])
            title = str(result.get("title") or title)
            break

    if album_id:
        album = yt.get_album(album_id)
        album_title = str(album.get("title") or album_name or "")
        track = _pick_album_track(album, video_id, title)
        if track is not None:
            title = str(track.get("title") or title)
            artist = _artist(track) or artist
            duration = _duration(track) or duration
            track_num = track.get("trackNumber")
            return SongMetadata(
                video_id=video_id,
                title=title,
                artist=artist,
                album=album_title,
                track_num=track_num if isinstance(track_num, int) else None,
                release_type=str(album.get("type") or "") or None,
                year=album.get("year") if isinstance(album.get("year"), int) else None,
                duration=duration,
            )
        return SongMetadata(
            video_id=video_id,
            title=title,
            artist=artist,
            album=album_title,
            release_type=str(album.get("type") or "") or None,
            year=album.get("year") if isinstance(album.get("year"), int) else None,
            duration=duration,
        )

    return SongMetadata(
        video_id=video_id,
        title=title,
        artist=artist,
        album="",
        duration=duration,
    )


def apply_song_metadata(path: pathlib.Path, meta: SongMetadata) -> None:
    """Rewrite MP4 tags with canonical catalog metadata; keep the cover."""
    import mutagen

    audio = mutagen.File(str(path), easy=False)
    if audio is None:
        return
    tags = getattr(audio, "tags", None)
    if tags is None:
        return
    tags["\xa9nam"] = [meta.title]
    tags["\xa9ART"] = [meta.artist]
    tags["aART"] = [meta.artist]
    if meta.album:
        tags["\xa9alb"] = [meta.album]
    if meta.track_num is not None:
        tags["trkn"] = [(int(meta.track_num), 0)]
    if meta.year is not None:
        tags["\xa9day"] = [str(meta.year)]
    audio.save()
