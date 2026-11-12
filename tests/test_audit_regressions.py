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


def test_retry_classifier_includes_503_and_timeouts():
    from packrat.download import _is_retryable

    assert _is_retryable(Exception("HTTP Error 503: Service Unavailable"))
    assert _is_retryable(Exception("Read timed out."))
    assert _is_retryable(Exception("HTTP Error 403: Forbidden"))
    assert not _is_retryable(Exception("HTTP Error 404: Not Found"))


def test_verbose_reordering_keeps_option_values():
    from packrat import cli_compat

    parser = cli.build_parser()
    assert cli_compat.move_global_options_after_subcommand(
        parser, ["-v", "--output", "/tmp/x", "sync"]
    ) == ["sync", "-v", "--output", "/tmp/x"]
    assert cli_compat.move_global_options_after_subcommand(
        parser, ["--output", "sync", "sync"]
    ) == ["sync", "--output", "sync"]


def test_sync_non_tty_requires_yes(tmp_path, monkeypatch):
    headers = tmp_path / "headers.json"
    headers.write_text("{}")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    rc = cli.main([
        "sync", "--source", "liked", "--headers", str(headers),
        "--output", str(tmp_path), "--archive", str(tmp_path / "archive"),
    ])
    assert rc == 2


def test_invalid_manifest_env_does_not_break_doctor(monkeypatch):
    monkeypatch.setenv("PACKRAT_MANIFEST_FORMAT", "bogus")
    monkeypatch.setattr("packrat.cli.cmd_doctor", lambda _args: 0)
    assert cli.main(["doctor"]) == 0


def test_visual_options_none_does_not_reset_theme():
    from packrat import tui

    tui.set_visual_options("sunset", True, "basic")
    tui.set_visual_options(palette=None, no_color=None)
    assert tui._visual_state["theme"] == "sunset"
    assert tui._visual_state["no_color"] is True
    assert tui._visual_state["palette"] == "basic"


def test_tui_q_at_done_quits_immediately():
    from packrat import tui
    from packrat.events import State

    class Screen:
        def getch(self):
            return ord("q")

    state = State()
    state.apply(type("E", (), {"type": "done", "payload": {"reason": "finished"}})())
    control = tui.Control()
    cont, *_rest, action = tui._handle_keys(Screen(), control, state, True, 0, False)
    assert cont is True
    assert action == "quit"


def test_paused_wait_stops_immediately():
    from packrat.control import Control

    control = Control()
    control.pause()
    control.request_stop()
    start = __import__("time").monotonic()
    control.wait_while_paused()
    assert __import__("time").monotonic() - start < 0.5


def test_delay_does_not_overwrite_paused_status():
    from packrat.events import Event, State

    state = State()
    state.apply(Event("status", {"status": "PAUSED"}))
    state.apply(Event("delay", {"remaining": 3.0, "paused": True}))
    assert state.status == "PAUSED"


def test_headless_sync_writes_log(tmp_path, monkeypatch):
    from packrat import logging_config

    headers = tmp_path / "headers.json"
    headers.write_text("{}")
    monkeypatch.setattr("packrat.cli.sync_module.run_sync", lambda _args: 0)
    assert cli.main([
        "sync", "--source", "liked", "--headers", str(headers),
        "--output", str(tmp_path), "--archive", str(tmp_path / "arc"),
        "--dry-run",
    ]) == 0
    logs = list(logging_config.LOG_DIR.glob("packrat-*.log"))
    assert logs


def test_worker_error_is_stopped_not_done():
    from packrat import tui
    from packrat.events import State

    state = State()
    control = tui.Control()
    container = {}
    tui._worker_target(lambda: {"error": "boom"}, {}, state, control, container)
    assert state.status == "STOPPED"


def test_basic_palette_uses_low_color_tier():
    from packrat import tui_theme

    assert tui_theme.select_tier(True, 256, False, "basic") == tui_theme.TIER_BMID


def test_cookie_redaction_masks_all_pairs():
    from packrat.logging_config import redact

    out = redact("Cookie: PREF=x; LOGIN_INFO=secret")
    assert "secret" not in out
    assert out.startswith("Cookie: <redacted>")


def test_release_keys_are_album_aware():
    from packrat.artists import release_owned_key

    assert release_owned_key("A", "Album One", "Intro") != release_owned_key(
        "A", "Album Two", "Intro"
    )


def test_disk_owned_keys_scan_non_m4a(tmp_path):
    from packrat.artists import disk_owned_keys

    (tmp_path / "A" / "Al").mkdir(parents=True)
    (tmp_path / "A" / "Al" / "01 - Song.webm").write_bytes(b"x")
    assert disk_owned_keys(tmp_path)


def test_artist_failed_only_includes_skipped(tmp_path):
    from packrat import artist_playlists

    payload = {"command": "artist", "label": "A", "entries": [
        {"video_id": "v1", "title": "T", "artist": "A", "album": "Al",
         "duration": 100, "est_size": 1, "status": "skipped",
         "path": None, "error": None},
    ]}
    manifest_path = tmp_path / "playlists" / "artist-A.manifest.json"
    manifest_path.parent.mkdir(parents=True)
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


def test_manifest_add_dedupes_video_id():
    m = manifest.Manifest()
    m.add("v1", "Old", "A", "Al", 1, "pending")
    m.add("v1", "New", "A", "Al", 1, "failed")
    assert len(m.entries) == 1
    assert m.entries[0]["title"] == "New"
    assert m.entries[0]["status"] == "failed"


def test_sync_marks_albumless_track_as_single():
    track = sync._normalize_track({
        "videoId": "v1", "title": "Single", "artists": [{"name": "A"}],
    }, 1)
    assert track["release_type"] == "single"


def test_artist_aggregate_playlist_preserves_old_downloads(tmp_path):
    from packrat import artist_playlists

    old = tmp_path / "A" / "Al" / "01 - Old.m4a"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"x")
    manifest_path = tmp_path / "playlists" / "artist-A.manifest.json"
    manifest_path.parent.mkdir(parents=True)
    payload = {
        "command": "artist", "label": "A",
        "entries": [{
            "video_id": "new", "title": "New", "artist": "A", "album": "Al",
            "track_num": 2, "duration": 100, "est_size": 1,
            "status": "pending", "path": None, "error": None,
        }],
        "playlist_entries": [
            {"video_id": "old", "title": "Old", "artist": "A", "album": "Al",
             "track_num": 1, "duration": 100, "status": "downloaded",
             "path": str(old)},
            {"video_id": "new", "title": "New", "artist": "A", "album": "Al",
             "track_num": 2, "duration": 100, "status": "pending", "path": None},
        ],
    }
    manifest_path.write_text(json.dumps(payload))

    def _batch(entries, **kw):
        for entry in entries:
            entry["status"] = "downloaded"
            entry["path"] = str(tmp_path / "A" / "Al" / "02 - New.m4a")
        kw["save_callback"](entries)
        return {"downloaded": 1, "archived": 0, "skipped_size": 0,
                "failed": 0, "stopped": False}

    with patch("packrat.artist_playlists.run_batch_download", side_effect=_batch):
        artist_playlists.download_artist(
            "A", tmp_path, str(tmp_path / "archive"),
            payload=payload, manifest_path=manifest_path,
        )
    text = (tmp_path / "playlists" / "artists" / "A" / "artist-all.m3u8").read_text()
    assert "Old" in text and "New" in text


def test_restore_palette_only_touched_slots():
    import io
    from packrat import tui_theme

    stream = io.StringIO()
    tui_theme.restore_palette(stream, slots=[7, 3])
    assert "104;3;7" in stream.getvalue()
