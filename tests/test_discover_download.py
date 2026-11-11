"""discover.download_pending: playlist must include earlier downloads."""

from __future__ import annotations

import pathlib

from packrat import discover
from packrat.manifest import Manifest


def test_playlist_keeps_previously_downloaded_tracks(tmp_path: pathlib.Path, monkeypatch):
    old_path = tmp_path / "A" / "Al" / "01 - Old.m4a"
    new_path = tmp_path / "A" / "Al" / "02 - New.m4a"
    manifest = Manifest()
    manifest.entries = [
        {"video_id": "old", "title": "Old", "artist": "A", "album": "Al",
         "duration": 100, "est_size": 1, "status": "downloaded",
         "path": str(old_path), "error": None},
        {"video_id": "new", "title": "New", "artist": "A", "album": "Al",
         "duration": 100, "est_size": 1, "status": "pending",
         "path": None, "error": None},
    ]

    def _batch(entries, **kwargs):
        entries[0]["status"] = "downloaded"
        entries[0]["path"] = str(new_path)
        kwargs["save_callback"](entries)
        return {
            "downloaded": 1, "archived": 0, "skipped_size": 0,
            "failed": 0, "stopped": False, "stop_reason": "completed",
        }

    monkeypatch.setattr("packrat.discover.run_batch_download", _batch)
    rc = discover.download_pending(
        manifest, tmp_path, None, None, None,
        str(tmp_path / "archive"), 0.0,
    )
    assert rc == 0
    playlist = (tmp_path / "playlists" / "discover.m3u8").read_text()
    assert "Old" in playlist
    assert "New" in playlist


def test_resolve_album_tracks_prefers_album_artist():
    from packrat import discover

    class FakeYT:
        def get_album(self, browseId):
            return {
                "artists": [{"name": "ATARASHII GAKKO!"}],
                "tracks": [{
                    "videoId": "v1", "title": "FRDP",
                    "artists": [{"name": "Sheena Ringo"},
                                {"name": "ATARASHII GAKKO!"}],
                }],
            }

    tracks = discover._resolve_album_tracks(FakeYT(), "album", "Carnival", "test", 10)
    assert tracks[0]["artists"][0]["name"] == "ATARASHII GAKKO!"
