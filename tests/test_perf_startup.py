"""Startup/download initialization should not re-extract every track."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from packrat import artists as artists_module
from packrat import cli
from packrat import sync as sync_module


class _EmptyArchive:
    def __init__(self, *_a, **_k):
        pass

    def __contains__(self, _item):
        return False


def test_sync_dry_run_does_not_ytdlp_extract_each_track(tmp_path, monkeypatch):
    tracks = [
        {"video_id": "v1", "title": "T", "artist": "A", "album": "Al",
         "duration": 100},
        {"video_id": "v2", "title": "T2", "artist": "A", "album": "Al",
         "duration": 200},
    ]
    monkeypatch.setattr(sync_module, "resolve_tracks", lambda *_a: tracks)
    monkeypatch.setattr(sync_module, "Archive", _EmptyArchive)

    def _must_not_run(*_a, **_k):
        raise AssertionError("sync preflight must not call estimate_track")

    monkeypatch.setattr(sync_module, "estimate_track", _must_not_run)
    rc = cli.main([
        "sync", "--source", "liked", "--headers", "/tmp/x",
        "--output", str(tmp_path), "--dry-run",
    ])
    assert rc == 0


@patch.object(artists_module, "YTMusic")
@patch.object(artists_module, "Archive")
@patch.object(artists_module, "build_top_songs")
@patch.object(artists_module, "get_discography")
def test_artist_noninteractive_skips_excluded_tracklists(
    mock_get_discography, mock_build_top_songs, mock_archive, mock_ytmusic,
    tmp_path,
):
    mock_ytmusic.return_value = MagicMock()
    mock_archive.return_value = set()
    mock_build_top_songs.return_value = {
        "artist": "Artist", "browse_id": "UCartist", "entries": [],
        "excluded_counts": {},
        "totals": {"total_tracks": 0, "new_tracks": 0, "already_owned": 0,
                   "excluded_counts": {}, "est_bytes": 0,
                   "est_human": "0.0 B", "est_time_seconds": 0},
        "top_versions_skipped": 0,
    }
    mock_get_discography.return_value = {
        "name": "Artist", "subscribers": "", "releases": [
            {"title": "Album", "year": "2020", "type": "Album",
             "browseId": "MPREb_album"},
            {"title": "Live at Wembley", "year": "2021", "type": "Live",
             "browseId": "MPREb_live"},
        ],
    }
    yt = mock_ytmusic.return_value

    def _album(browse_id):
        assert browse_id == "MPREb_album"
        return {"tracks": [{"videoId": "a1", "title": "Song",
                            "duration_seconds": 180, "trackNumber": 1}],
                "year": 2020}

    yt.get_album.side_effect = _album
    catalog = artists_module.build_catalog(
        "/fake/headers.json", "UCartist", tmp_path, tmp_path / "archive",
        scope="all", pause=0, resolve_excluded=False,
    )
    assert [e["video_id"] for e in catalog["entries"]] == ["a1"]
    live = next(r for r in catalog["payload"]["releases"] if r["browse_id"] == "MPREb_live")
    assert live["status"] == "excluded"
    assert live.get("resolved") is False
