"""Canonical metadata resolution for download-one."""

from __future__ import annotations

import pathlib

from packrat import song_meta
from packrat.naming import track_path


class FakeYTMusic:
    def __init__(self, headers_path):
        self.headers_path = headers_path

    def get_watch_playlist(self, videoId):
        return {
            "tracks": [
                {
                    "videoId": videoId,
                    "title": "Path of Most Resistance",
                    "artists": [{"name": "The Hives"}],
                    "duration_seconds": 203,
                }
            ]
        }

    def search(self, query, filter, limit):
        assert filter == "songs"
        return [
            {
                "videoId": "88IMGC_lt3E",
                "title": "Path Of Most Resistance",
                "album": {"name": "The Hives Forever Forever The Hives", "id": "MPREb_avmLP8Pabky"},
                "artists": [{"name": "The Hives"}],
                "duration_seconds": 203,
            }
        ]

    def get_album(self, album_id):
        return {
            "title": "The Hives Forever Forever The Hives",
            "type": "Album",
            "year": 2025,
            "tracks": [
                {
                    "videoId": "SGw5bKLV-tY",
                    "title": "Path Of Most Resistance",
                    "artists": [{"name": "The Hives"}],
                    "trackNumber": 12,
                    "duration_seconds": 203,
                }
            ],
        }


def test_resolve_uses_album_track_and_number(monkeypatch):
    monkeypatch.setattr(song_meta, "YTMusic", FakeYTMusic)
    meta = song_meta.resolve_song_metadata("SGw5bKLV-tY", "/tmp/headers.json")
    assert meta.artist == "The Hives"
    assert meta.album == "The Hives Forever Forever The Hives"
    assert meta.title == "Path Of Most Resistance"
    assert meta.track_num == 12
    assert meta.release_type == "Album"


def test_resolved_path_uses_artist_album_and_track(tmp_path: pathlib.Path, monkeypatch):
    monkeypatch.setattr(song_meta, "YTMusic", FakeYTMusic)
    meta = song_meta.resolve_song_metadata("SGw5bKLV-tY", "/tmp/headers.json")
    path = track_path(
        tmp_path,
        meta.artist,
        meta.album,
        meta.title,
        meta.track_num,
        release_type=meta.release_type,
    )
    assert path == (
        tmp_path
        / "The_Hives"
        / "The_Hives_Forever_Forever_The_Hives"
        / "12 - Path_Of_Most_Resistance.m4a"
    )
