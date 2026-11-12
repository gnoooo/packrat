"""Regression tests for archive, resume, integrity and locking fixes."""

from __future__ import annotations

import json
import pathlib
from unittest.mock import patch

import pytest

from packrat import cli, cli_args, integrity, manifest, playlist, sync


def _audio(monkeypatch, duration=180.0):
    class Info:
        length = duration

    class Audio:
        info = Info()

    monkeypatch.setattr("mutagen.File", lambda _p: Audio())


def test_integrity_rejects_short_or_wrong_duration(monkeypatch, tmp_path):
    path = tmp_path / "song.m4a"
    path.write_bytes(b"x" * 100)
    _audio(monkeypatch, 180.0)
    assert integrity.verify_audio(path, expected_size=1000).ok is False
    assert integrity.verify_audio(path, expected_duration=100.0).ok is False
    assert integrity.verify_audio(path, expected_size=100, expected_duration=180.0).ok is True


def test_archive_fallback_uses_previous_default(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_args.paths, "ARCHIVE_PATH", tmp_path / "new" / "archive")
    monkeypatch.setattr(cli_args, "LEGACY_ARCHIVE_NAMES", (".packrat_archive",))
    monkeypatch.chdir(tmp_path)
    old = tmp_path / ".packrat_archive"
    old.write_text("v1\n")
    assert cli_args._archive_default() == str(old)
    (tmp_path / "new").mkdir()
    (tmp_path / "new" / "archive").write_text("v2\n")
    assert cli_args._archive_default() == str(tmp_path / "new" / "archive")


def test_artist_manifest_dry_run_does_not_download(tmp_path, capsys):
    manifest_path = tmp_path / "playlists" / "artist-A.manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps({
        "command": "artist", "label": "A", "entries": [{
            "video_id": "v1", "title": "T", "artist": "A", "album": "Al",
            "duration": 100, "est_size": 1, "status": "pending",
            "path": None, "error": None,
        }],
    }))
    args = cli.build_parser().parse_args([
        "download-manifest", "--manifest", str(manifest_path), "--dry-run",
    ])
    with patch("packrat.cli.artists_module.download_artist") as download:
        assert cli.cmd_download_manifest(args) == 0
    download.assert_not_called()
    assert "dry-run" in capsys.readouterr().out


def test_artist_manifest_failed_only_selects_failed(tmp_path):
    from packrat import artist_playlists

    manifest_path = tmp_path / "playlists" / "artist-A.manifest.json"
    manifest_path.parent.mkdir(parents=True)
    payload = {
        "command": "artist", "label": "A", "entries": [
            {"video_id": "v1", "title": "T", "artist": "A", "album": "Al",
             "duration": 100, "est_size": 1, "status": "failed",
             "path": None, "error": "x"},
            {"video_id": "v2", "title": "T2", "artist": "A", "album": "Al",
             "duration": 100, "est_size": 1, "status": "pending",
             "path": None, "error": None},
        ],
    }
    manifest_path.write_text(json.dumps(payload))
    seen = []
    with patch("packrat.artist_playlists.run_batch_download",
               side_effect=lambda entries, **kw: seen.extend(e["video_id"] for e in entries) or {
                   "downloaded": 0, "archived": 0, "skipped_size": 0,
                   "failed": 0, "stopped": False,
               }):
        artist_playlists.download_artist(
            "A", tmp_path, str(tmp_path / "archive"),
            payload=payload, manifest_path=manifest_path, failed_only=True,
        )
    assert seen == ["v1"]


def test_playlist_write_is_atomic(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(playlist, "atomic_write_text", lambda *a, **k: calls.append((a, k)))
    playlist.write_m3u8(tmp_path / "x.m3u8", [])
    assert calls


def test_csv_sidecar_handles_heterogeneous_entries(tmp_path):
    m = manifest.Manifest()
    m.entries = [
        {"video_id": "v1", "title": "T", "status": "downloaded"},
        {"video_id": "v2", "title": "T2", "status": "pending", "source": "x"},
    ]
    target = tmp_path / "m.json"
    m.write(target, "json+csv")
    text = (tmp_path / "m.csv").read_text()
    assert "source" in text and "v1" in text and "v2" in text


def test_run_lock_rejects_concurrent_state_command():
    with cli._run_lock():
        with pytest.raises(RuntimeError, match="another Packrat run"):
            with cli._run_lock():
                pass


def test_download_one_updates_archive(monkeypatch, tmp_path):
    from packrat import paths
    from packrat.song_meta import SongMetadata

    downloaded = tmp_path / "The_Hives" / "Album" / "01 - Song.m4a"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_bytes(b"x")
    metadata = SongMetadata(
        video_id="SGw5bKLV-tY", title="Song", artist="The Hives",
        album="Album", track_num=1, release_type="album",
    )

    class FakeYDL:
        def __init__(self, *_a, **_k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def extract_info(self, *_a, **_k):
            return {"filepath": str(downloaded)}

    monkeypatch.setattr("packrat.cli.resolve_song_metadata", lambda *_a, **_k: metadata)
    monkeypatch.setattr("packrat.cli._check_deno", lambda: (True, "2.9"))
    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYDL)
    monkeypatch.setattr("packrat.cli._final_filepath", lambda *_a: downloaded)
    monkeypatch.setattr("packrat.cli.apply_song_metadata", lambda *_a, **_k: True)
    monkeypatch.setattr("packrat.cli._verify_with_ffprobe", lambda _p: {})
    monkeypatch.setattr("packrat.cli._verify_with_mutagen", lambda _p: {})

    args = cli.build_parser().parse_args([
        "download-one", "SGw5bKLV-tY", "--output", str(tmp_path),
    ])
    assert cli.cmd_download_one(args) == 0
    archive = paths.ARCHIVE_PATH.read_text()
    assert "SGw5bKLV-tY" in archive
