"""Regression tests for track-number, release-type and state metadata."""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock, patch

from packrat import albums as albums_module
from packrat import queues as queues_module
from packrat import sync as sync_module


def test_sync_does_not_use_playlist_position_as_track_number():
    raw = {
        "videoId": "v1",
        "title": "Song",
        "artists": [{"name": "Artist"}],
        "album": {"name": "Album"},
        "duration_seconds": 180,
    }
    track = sync_module._normalize_track(raw, 250)
    assert track["track_num"] is None


def test_sync_keeps_real_track_number():
    raw = {
        "videoId": "v1",
        "title": "Song",
        "artists": [{"name": "Artist"}],
        "album": {"name": "Album"},
        "trackNumber": 3,
    }
    assert sync_module._normalize_track(raw, 99)["track_num"] == 3


@patch.object(albums_module, "YTMusic")
@patch.object(albums_module, "Archive")
def test_albums_manifest_keeps_track_number_and_release_type(mock_archive, mock_ytmusic, tmp_path):
    yt = MagicMock()
    mock_ytmusic.return_value = yt
    mock_archive.return_value = set()
    yt.get_liked_songs.return_value = {
        "tracks": [{
            "videoId": "liked1",
            "title": "Hit",
            "artists": [{"name": "Liked Artist", "id": "UCx"}],
            "album": {"name": "Liked Album"},
            "duration_seconds": 200,
            "videoType": "MUSIC_VIDEO_TYPE_ATV",
        }]
    }
    yt.get_library_artists.return_value = []
    yt.get_artist.return_value = {
        "albums": {"results": [{
            "title": "Album", "browseId": "album1", "year": "2020",
            "audioPlaylistId": "OLAK5uy_album1",
        }]},
        "singles": {"results": []},
    }
    yt.get_album.return_value = {
        "title": "Album",
        "type": "Album",
        "tracks": [{
            "videoId": "t1",
            "title": "Track One",
            "artists": [{"name": "Liked Artist", "id": "UCx"}],
            "trackNumber": 1,
            "duration_seconds": 200,
            "videoType": "MUSIC_VIDEO_TYPE_ATV",
        }],
    }

    albums_module.discover_albums(
        headers_path="dummy.json",
        output_root=tmp_path,
        archive_path=tmp_path / "archive",
        artist_top_k=1,
        albums_per_artist=1,
    )
    entries = json.loads(
        (tmp_path / "playlists" / "albums.manifest.json").read_text()
    )["entries"]
    assert entries[0]["track_num"] == 1
    assert entries[0]["release_type"] == "album"


@patch.object(queues_module, "YTMusic")
@patch.object(queues_module, "Archive")
def test_queues_manifest_keeps_custom_singles_dir(mock_archive, mock_ytmusic, tmp_path):
    yt = MagicMock()
    mock_ytmusic.return_value = yt
    mock_archive.return_value = set()
    yt.get_liked_songs.return_value = {"tracks": [{
        "videoId": "seed1", "title": "Seed", "artists": [{"name": "A"}],
        "album": {"name": "Al"}, "duration_seconds": 180,
        "videoType": "MUSIC_VIDEO_TYPE_ATV",
    }]}
    yt.get_watch_playlist.return_value = {"tracks": [{
        "videoId": "t1", "title": "Queue Single", "artists": [{"name": "B"}],
        "album": {"name": ""}, "duration_seconds": 180,
        "videoType": "MUSIC_VIDEO_TYPE_ATV",
    }]}

    queues_module.discover_queues(
        headers_path="dummy.json",
        output_root=tmp_path,
        archive_path=tmp_path / "archive",
        seed_count=1,
        total_cap=5,
        singles_dir="custom_singles",
    )
    entries = json.loads(
        (tmp_path / "playlists" / "queues.manifest.json").read_text()
    )["entries"]
    assert entries
    assert entries[0]["singles_dir"] == "custom_singles"
