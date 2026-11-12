"""Credential, integrity, and manifest-source behavior."""

from __future__ import annotations

import pathlib

import pytest

from packrat import cli, security
from packrat.integrity import verify_audio
from packrat.logging_config import redact


def test_secure_file_sets_private_mode(tmp_path):
    path = tmp_path / "browser.json"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o644)
    assert not security.is_private(path)
    security.secure_file(path)
    assert security.is_private(path)


def test_remove_credentials(tmp_path):
    path = tmp_path / "browser.json"
    path.write_text("{}", encoding="utf-8")
    assert security.remove_credentials(path)
    assert not path.exists()
    assert not security.remove_credentials(path)


def test_logout_deletes_login_file(tmp_path, capsys):
    path = tmp_path / "browser.json"
    path.write_text("{}", encoding="utf-8")
    assert cli.main(["logout", "--file", str(path)]) == 0
    assert not path.exists()
    assert "Removed login file" in capsys.readouterr().out


def test_redact_json_headers():
    text = '{"Cookie": "SECRET", "Authorization": "Bearer TOKEN"}'
    out = redact(text)
    assert "SECRET" not in out
    assert "TOKEN" not in out
    assert '"Cookie"' in out


def test_verify_audio_missing_and_empty(tmp_path):
    assert not verify_audio(tmp_path / "missing.m4a").ok
    empty = tmp_path / "empty.m4a"
    empty.write_bytes(b"")
    check = verify_audio(empty)
    assert not check.ok and "empty" in check.reason


def test_verify_audio_valid_via_mutagen(monkeypatch, tmp_path):
    path = tmp_path / "song.m4a"
    path.write_bytes(b"x" * 100)

    class Info:
        length = 180.0

    class Audio:
        info = Info()

    monkeypatch.setattr("mutagen.File", lambda _path: Audio())
    check = verify_audio(path)
    assert check.ok and check.duration == 180.0


def test_download_track_skips_valid_existing_file(monkeypatch, tmp_path):
    from packrat import download

    track = {"video_id": "v1", "title": "Song", "artist": "A", "album": "Al"}
    target = download.track_path(tmp_path, "A", "Al", "Song", None)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")
    monkeypatch.setattr(download, "verify_audio", lambda *_a, **_k: type(
        "C", (), {"ok": True, "size": 1}
    )())

    class NoYDL:
        def __init__(self, *_a, **_k):
            raise AssertionError("yt-dlp must not be called for an existing file")

    monkeypatch.setattr(download.yt_dlp, "YoutubeDL", NoYDL)
    result = download.download_track(track, tmp_path)
    assert result.status == "downloaded"
    assert result.filepath == target


def test_sync_uses_manifest_as_source(monkeypatch, tmp_path, capsys):
    from packrat import sync
    from packrat.manifest import Manifest

    track = {"video_id": "v1", "title": "Song", "artist": "A", "album": "Al",
             "duration": 100}
    monkeypatch.setattr(sync, "resolve_tracks", lambda *_a: [track])
    monkeypatch.setattr(sync, "verify_audio", lambda *_a, **_k: type(
        "C", (), {"ok": True, "size": 100}
    )())

    class EmptyArchive:
        def __init__(self, *_a, **_k):
            pass

        def __contains__(self, _item):
            return False

    monkeypatch.setattr(sync, "Archive", EmptyArchive)
    manifest_path = tmp_path / "playlists" / "liked.manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest = Manifest()
    manifest.add("v1", "Song", "A", "Al", 100, "downloaded",
                 path=str(tmp_path / "A" / "Al" / "Song.m4a"))
    manifest.write(manifest_path)

    rc = cli.main([
        "sync", "--source", "liked", "--headers", "/tmp/x",
        "--output", str(tmp_path), "--archive", str(tmp_path / "arc"),
        "--dry-run",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "pending tracks: 0" in out


def test_batch_skips_manifest_downloaded_entry(monkeypatch, tmp_path):
    from packrat import batch

    path = tmp_path / "song.m4a"
    path.write_bytes(b"x")
    monkeypatch.setattr(batch, "verify_audio", lambda *_a, **_k: type(
        "C", (), {"ok": True, "size": 1}
    )())
    monkeypatch.setattr(batch, "download_track", lambda *_a, **_k: (
        _ for _ in ()
    ).throw(AssertionError("must not download")))
    entry = {"video_id": "v1", "title": "Song", "artist": "A", "album": "Al",
             "status": "downloaded", "path": str(path)}
    result = batch.run_batch_download([entry], tmp_path, delay=0)
    assert result["downloaded"] == 1
    assert entry["status"] == "downloaded"


def test_doctor_offline_runs(capsys):
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "python" in out and "login file" in out


def test_export_metadata_from_manifest(tmp_path, capsys):
    from packrat.manifest import Manifest

    manifest_path = tmp_path / "plan.manifest.json"
    manifest = Manifest()
    manifest.add("v1", "Song", "Artist", "Album", 123, "downloaded",
                 path=str(tmp_path / "Song.m4a"))
    manifest.write(manifest_path)
    target = tmp_path / "metadata.json"
    rc = cli.main([
        "export-metadata", "--manifest", str(manifest_path),
        "--file", str(target), "--format", "json",
    ])
    assert rc == 0
    import json
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["tracks"][0]["video_id"] == "v1"
    assert data["tracks"][0]["artist"] == "Artist"
