"""Smoke tests for discover-queues with mocked ytmusicapi shapes."""

import pathlib
from unittest.mock import MagicMock, patch

from ytm_offline import queues as queues_module


def _make_track(video_id, title, artist, duration=180, vtype="MUSIC_VIDEO_TYPE_ATV"):
    return {
        "videoId": video_id,
        "title": title,
        "artists": [{"name": artist, "id": "UC" + video_id}],
        "album": {"name": "Album"},
        "duration_seconds": duration,
        "videoType": vtype,
        "length": f"{duration // 60}:{duration % 60:02d}",
    }


@patch.object(queues_module, "YTMusic")
@patch.object(queues_module, "Archive")
def test_discover_queues_builds_manifest(mock_archive, mock_ytmusic, tmp_path):
    yt = MagicMock()
    mock_ytmusic.return_value = yt
    mock_archive.return_value = set()

    yt.get_liked_songs.return_value = {
        "tracks": [
            _make_track("seed1", "Seed One", "Artist A"),
            _make_track("seed2", "Seed Two", "Artist B"),
        ]
    }

    yt.get_watch_playlist.return_value = {
        "tracks": [
            _make_track("t1", "Queue Track 1", "Artist C"),
            _make_track("t2", "Queue Track 2", "Artist D"),
            # UGC should be filtered out.
            _make_track("t3", "UGC Track", "Artist E", vtype="MUSIC_VIDEO_TYPE_UGC"),
            # Duplicate key should be skipped.
            _make_track("t1", "Queue Track 1", "Artist C"),
        ]
    }

    result = queues_module.discover_queues(
        headers_path="dummy.json",
        output_root=tmp_path,
        archive_path=tmp_path / "archive",
        seed_count=2,
        total_cap=10,
    )

    assert result["total_tracks"] >= 2
    assert result["seeds_used"] >= 1
    assert (tmp_path / "playlists" / "queues.manifest.json").exists()

    manifest = (tmp_path / "playlists" / "queues.manifest.json").read_text()
    # UGC track should be filtered out.
    assert "UGC Track" not in manifest

    m3u8_files = list((tmp_path / "playlists" / "queues").rglob("*.m3u8"))
    assert len(m3u8_files) >= result["seeds_used"]
