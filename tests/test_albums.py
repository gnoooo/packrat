"""Smoke tests for discover-albums with mocked ytmusicapi shapes."""

from unittest.mock import MagicMock, patch

from packrat import albums as albums_module
from packrat import batch as batch_module


def _make_liked_track(video_id, title, artist):
    return {
        "videoId": video_id,
        "title": title,
        "artists": [{"name": artist, "id": "UC" + video_id}],
        "album": {"name": "Liked Album"},
        "duration_seconds": 200,
        "videoType": "MUSIC_VIDEO_TYPE_ATV",
    }


def _make_album_result(title, browse_id, year="2020"):
    return {
        "title": title,
        "browseId": browse_id,
        "year": year,
        "audioPlaylistId": f"OLAK5uy_{browse_id}",
    }


def _make_album_track(video_id, title, track_number=1, duration=200):
    return {
        "videoId": video_id,
        "title": title,
        "artists": [{"name": "Liked Artist", "id": "UCx"}],
        "trackNumber": track_number,
        "duration_seconds": duration,
        "videoType": "MUSIC_VIDEO_TYPE_ATV",
    }


@patch.object(albums_module, "YTMusic")
@patch.object(albums_module, "Archive")
def test_discover_albums_picks_standard_skips_live(
    mock_archive, mock_ytmusic, tmp_path
):
    yt = MagicMock()
    mock_ytmusic.return_value = yt
    mock_archive.return_value = set()

    yt.get_liked_songs.return_value = {
        "tracks": [_make_liked_track("liked1", "Hit", "Liked Artist")]
    }
    yt.get_library_artists.return_value = []

    yt.get_artist.return_value = {
        "albums": {
            "results": [
                _make_album_result("Album (Deluxe Edition)", "album_deluxe"),
                _make_album_result("Album", "album_standard"),
                _make_album_result("Live at Venue", "album_live"),
            ]
        },
        "singles": {"results": []},
    }

    def fake_get_album(browse_id):
        return {
            "title": "Album",
            "tracks": [
                _make_album_track("t1", "Track One", 1),
                _make_album_track("t2", "Track Two", 2),
            ],
        }

    yt.get_album.side_effect = fake_get_album

    result = albums_module.discover_albums(
        headers_path="dummy.json",
        output_root=tmp_path,
        archive_path=tmp_path / "archive",
        artist_top_k=1,
        albums_per_artist=1,
    )

    assert result["artists_considered"] == 1
    assert result["total_new_tracks"] == 2
    assert all("Live at Venue" not in a["album"] for a in result["albums"])
    assert (tmp_path / "playlists" / "albums.manifest.json").exists()


def test_download_albums_keeps_previously_downloaded_entries(tmp_path):
    """save_callback must not replace the manifest with the pending subset."""
    import json

    from packrat.manifest import Manifest

    playlists = tmp_path / "playlists"
    playlists.mkdir(parents=True)
    manifest = Manifest()
    manifest.entries = [
        {"video_id": "old", "title": "Old", "artist": "A", "album": "Al",
         "est_size": 1, "status": "downloaded", "path": "/x/old.m4a"},
        {"video_id": "new", "title": "New", "artist": "A", "album": "Al",
         "est_size": 1, "status": "pending", "path": None},
    ]
    manifest.write(playlists / "albums.manifest.json")

    def _batch(entries, **kwargs):
        entries[0]["status"] = "downloaded"
        entries[0]["path"] = "/x/new.m4a"
        kwargs["save_callback"](entries)
        return {
            "downloaded": 1, "archived": 0, "skipped_size": 0,
            "failed": 0, "stopped": False, "stop_reason": "completed",
        }

    with patch.object(batch_module, "run_batch_download", side_effect=_batch):
        result = albums_module.download_albums(
            output_root=tmp_path, archive_path=tmp_path / "archive",
        )

    assert "summary" in result
    saved = json.loads((playlists / "albums.manifest.json").read_text())
    assert [e["video_id"] for e in saved["entries"]] == ["old", "new"]
