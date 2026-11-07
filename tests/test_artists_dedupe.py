"""Characterization tests for artist owned-key seeding and top/catalog dedupe."""

from unittest.mock import MagicMock, patch

import pytest

from ytm_offline import artists as artists_module


def _make_top_entry(video_id, title, artist="Artist", duration=180):
    return {
        "video_id": video_id,
        "title": title,
        "artist": artist,
        "album": "Top songs",
        "track_num": None,
        "duration": duration,
        "est_size": duration * 16000,
        "status": "pending",
        "path": None,
        "error": None,
        "source": "top:Artist",
        "release_year": 9999,
        "release_type": "top",
        "popularity": 0,
    }


def _make_album_track(video_id, title, duration=180):
    return {
        "videoId": video_id,
        "title": title,
        "artists": [{"name": "Artist", "id": "UCartist"}],
        "duration_seconds": duration,
        "trackNumber": 1,
    }


@patch.object(artists_module, "YTMusic")
@patch.object(artists_module, "Archive")
@patch.object(artists_module, "build_top_songs")
@patch.object(artists_module, "get_discography")
def test_scope_all_top_songs_come_first_and_seed_owned_keys(
    mock_get_discography,
    mock_build_top_songs,
    mock_archive,
    mock_ytmusic,
    tmp_path,
):
    yt = MagicMock()
    mock_ytmusic.return_value = yt
    mock_archive.return_value = set()

    top_entry = _make_top_entry("top1", "Top Hit")
    mock_build_top_songs.return_value = {
        "artist": "Artist",
        "browse_id": "UCartist",
        "entries": [top_entry],
        "excluded_counts": {},
        "totals": {
            "total_tracks": 1,
            "new_tracks": 1,
            "already_owned": 0,
            "excluded_counts": {},
            "est_bytes": top_entry["est_size"],
            "est_human": "2.74 MB",
            "est_time_seconds": 100,
        },
        "top_versions_skipped": 0,
    }

    mock_get_discography.return_value = {
        "name": "Artist",
        "subscribers": "1M",
        "releases": [
            {
                "title": "Album",
                "year": "2020",
                "type": "Album",
                "browseId": "MPREb_album",
            }
        ],
    }

    yt.get_album.return_value = {
        "tracks": [
            _make_album_track("album1", "Album Only", duration=200),
        ],
        "year": 2020,
    }

    catalog = artists_module.build_catalog(
        "/fake/headers.json",
        "UCartist",
        tmp_path,
        tmp_path / "archive",
        scope="all",
    )

    entries = catalog["entries"]
    assert entries[0]["video_id"] == "top1"
    assert entries[1]["video_id"] == "album1"
    assert catalog["totals"]["new_tracks"] == 2


@patch.object(artists_module, "YTMusic")
@patch.object(artists_module, "Archive")
@patch.object(artists_module, "build_top_songs")
@patch.object(artists_module, "get_discography")
def test_scope_all_does_not_duplicate_top_song_in_catalog(
    mock_get_discography,
    mock_build_top_songs,
    mock_archive,
    mock_ytmusic,
    tmp_path,
):
    yt = MagicMock()
    mock_ytmusic.return_value = yt
    mock_archive.return_value = set()

    top_entry = _make_top_entry("top1", "Shared Song")
    mock_build_top_songs.return_value = {
        "artist": "Artist",
        "browse_id": "UCartist",
        "entries": [top_entry],
        "excluded_counts": {},
        "totals": {
            "total_tracks": 1,
            "new_tracks": 1,
            "already_owned": 0,
            "excluded_counts": {},
            "est_bytes": top_entry["est_size"],
            "est_human": "2.74 MB",
            "est_time_seconds": 100,
        },
        "top_versions_skipped": 0,
    }

    mock_get_discography.return_value = {
        "name": "Artist",
        "subscribers": "1M",
        "releases": [
            {
                "title": "Album",
                "year": "2020",
                "type": "Album",
                "browseId": "MPREb_album",
            }
        ],
    }

    yt.get_album.return_value = {
        "tracks": [
            _make_album_track("album1", "Shared Song", duration=200),
            _make_album_track("album2", "Other Song", duration=200),
        ],
        "year": 2020,
    }

    catalog = artists_module.build_catalog(
        "/fake/headers.json",
        "UCartist",
        tmp_path,
        tmp_path / "archive",
        scope="all",
    )

    entries = catalog["entries"]
    video_ids = [e["video_id"] for e in entries]
    assert video_ids == ["top1", "album2"]
    assert catalog["totals"]["already_owned"] == 1
