"""Pure-function tests for the sync pipeline."""

from __future__ import annotations

import json
import pathlib

from packrat.manifest import Manifest
from packrat.naming import clean, parse_playlist_id, track_filename, track_path
from packrat.playlist import write_m3u8
from packrat.sizes import format_size, parse_size


def test_clean_removes_unsafe_characters() -> None:
    assert clean("AC/DC") == "AC_DC"
    assert clean("  Spaces  ") == "Spaces"
    assert clean("dots...") == "dots"


def test_track_filename_with_track_number() -> None:
    assert track_filename("Song", 5) == "05 - Song.m4a"


def test_track_filename_without_track_number() -> None:
    assert track_filename("Song") == "Song.m4a"


def test_track_path_layout() -> None:
    p = track_path(pathlib.Path("music"), "Artist", "Album", "Title", 3)
    assert p == pathlib.Path("music/Artist/Album/03 - Title.m4a")


def test_parse_playlist_id_from_url() -> None:
    assert (
        parse_playlist_id(
            "playlist:https://music.youtube.com/playlist?list=PL123&other=x"
        )
        == "PL123"
    )


def test_parse_playlist_id_from_id() -> None:
    assert parse_playlist_id("playlist:PLabc") == "PLabc"


def test_parse_size_variants() -> None:
    assert parse_size("3GB") == 3_000_000_000
    assert parse_size("250M") == 250_000_000
    assert parse_size("10KB") == 10_000


def test_format_size_roundtrips() -> None:
    assert "GB" in format_size(3_000_000_000)
    assert "MB" in format_size(250_000_000)


def test_manifest_counts() -> None:
    m = Manifest()
    m.add("v1", "T1", "A1", "Al1", 1_000, "pending")
    m.add("v2", "T2", "A2", "Al2", 2_000, "downloaded")
    m.add("v3", "T3", "A3", "Al3", None, "failed")
    assert m.counts()["pending"] == 1
    assert m.counts()["downloaded"] == 1
    assert m.total_est_size() == 3_000


def test_manifest_writes_json(tmp_path: pathlib.Path) -> None:
    m = Manifest()
    m.add("v1", "T1", "A1", "Al1", 1_000, "pending")
    path = tmp_path / "manifest.json"
    m.write(path, "json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["counts"]["pending"] == 1
    assert data["total_est_size"] == 1_000


def test_playlist_relative_paths(tmp_path: pathlib.Path) -> None:
    music = tmp_path / "music"
    track = music / "Artist" / "Album" / "01 - Song.m4a"
    track.parent.mkdir(parents=True)
    track.write_text("dummy")
    playlist = music / "playlists" / "liked.m3u8"
    write_m3u8(
        playlist,
        [
            {
                "path": track,
                "duration": 123,
                "title": "Song",
                "artist": "Artist",
            }
        ],
    )
    text = playlist.read_text(encoding="utf-8")
    assert "#EXTM3U" in text
    assert "Artist - Song" in text
    assert "../Artist/Album/01 - Song.m4a" in text
