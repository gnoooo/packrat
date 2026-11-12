"""Unified --limit: max NEW tracks after dedupe/selection."""

from __future__ import annotations

import json
import pathlib

import pytest

from packrat import cli, cli_compat
from packrat.manifest import Manifest


def _args(argv):
    parser = cli.build_parser()
    raw = cli_compat.move_global_options_after_subcommand(parser, argv)
    args = parser.parse_args(raw)
    return args


class _EmptyArchive:
    def __init__(self, *_a, **_k):
        pass

    def __contains__(self, _item):
        return False


def test_sync_limit_caps_batch_after_dedupe(tmp_path, monkeypatch):
    tracks = [
        {"video_id": f"v{i}", "title": f"T{i}", "artist": "A", "album": "Al",
         "duration": 100}
        for i in range(5)
    ]
    monkeypatch.setattr("packrat.sync.resolve_tracks", lambda *_a: tracks)
    monkeypatch.setattr("packrat.sync.Archive", _EmptyArchive)
    monkeypatch.setattr(
        "packrat.sync.estimate_track", lambda *_a, **_k: {"filesize": 1000}
    )
    captured = {}

    def _batch(entries, **_kw):
        captured["n"] = len(entries)
        return {"downloaded": 0, "archived": 0, "skipped_size": 0,
                "failed": 0, "stopped": False, "stop_reason": "completed"}

    monkeypatch.setattr("packrat.sync.run_batch_download", _batch)
    rc = cli.main([
        "sync", "--source", "liked", "--headers", "/tmp/x",
        "--output", str(tmp_path), "--archive", str(tmp_path / "arc"),
        "--limit", "2", "--yes",
    ])
    assert rc == 0
    assert captured["n"] == 2


def test_download_manifest_limit_caps_batch(tmp_path, monkeypatch):
    manifest = tmp_path / "playlists" / "m.manifest.json"
    manifest.parent.mkdir(parents=True)
    m = Manifest()
    m.entries = [
        {"video_id": f"v{i}", "title": f"T{i}", "artist": "A", "album": "Al",
         "duration": 100, "est_size": 1000, "status": "pending"}
        for i in range(4)
    ]
    m.write(manifest)
    captured = {}

    def _batch(entries, **_kw):
        captured["n"] = len(entries)
        return {
            "downloaded": 0, "archived": 0, "skipped_size": 0,
            "failed": 0, "stopped": False, "stop_reason": "completed",
        }

    monkeypatch.setattr("packrat.cli.run_batch_download", _batch)
    rc = cli.main([
        "download-manifest", "--manifest", str(manifest), "--yes",
        "--output", str(tmp_path), "--limit", "2",
    ])
    assert rc == 0
    assert captured["n"] == 2


def test_discover_limit_passed_to_download(tmp_path, monkeypatch):
    captured = {}
    manifest = Manifest()
    manifest.entries = [
        {"video_id": f"v{i}", "title": f"T{i}", "artist": "A", "album": "Al",
         "duration": 100, "est_size": 1000, "status": "pending"}
        for i in range(5)
    ]
    monkeypatch.setattr(
        "packrat.discover.run_discover",
        lambda **_kw: (manifest, {
            "total": 5, "per_source": {"home": 5},
            "estimated_size_human": "1 KB", "skipped_by_size": 0,
            "top_artists": [("A", 5)],
        }),
    )
    def _download(**kw):
        captured["limit"] = kw.get("limit")
        return 0

    monkeypatch.setattr("packrat.discover.download_pending", _download)
    rc = cli.main([
        "discover", "sections", "--headers", "/tmp/x",
        "--output", str(tmp_path), "--source", "home",
        "--yes", "--limit", "2",
    ])
    assert rc == 0
    assert captured["limit"] == 2


def test_artist_limit_passed_to_download(tmp_path, monkeypatch):
    monkeypatch.setattr("packrat.cli.YTMusic", lambda *_a, **_k: object())
    monkeypatch.setattr(
        "packrat.artists.search_artists",
        lambda *_a, **_k: [{"name": "A", "browseId": "b"}],
    )
    catalog = {
        "artist": "A", "label": "A", "browseId": "b",
        "payload": {"entries": [
            {"video_id": "v1", "status": "pending"},
            {"video_id": "v2", "status": "pending"},
        ]},
        "entries": [
            {"video_id": "v1", "status": "pending"},
            {"video_id": "v2", "status": "pending"},
        ],
        "totals": {"measured_s_per_track": None, "excluded_counts": {}},
        "manifest_path": str(tmp_path / "artist.json"),
    }
    monkeypatch.setattr("packrat.artists.build_catalog", lambda *_a, **_k: catalog)
    monkeypatch.setattr("packrat.artists.build_sections", lambda *_a, **_k: [])
    monkeypatch.setattr("packrat.artists.save_manifest", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "packrat.artists.summarize_entries",
        lambda *_a, **_k: {
            "new_tracks": 2, "est_human": "1 KB", "est_time_seconds": 60,
            "est_source": "default", "measured_s_per_track": 0,
        },
    )
    captured = {}
    def _download(**kw):
        captured["limit"] = kw.get("limit")
        return {
            "manifest_path": "m", "m3u8_dir": "d",
            "summary": {"downloaded": 0, "failed": 0, "stopped": False},
        }

    monkeypatch.setattr("packrat.artists.download_artist", _download)
    rc = cli.main([
        "artist", "A", "--headers", "/tmp/x", "--yes",
        "--output", str(tmp_path), "--archive", str(tmp_path / "arc"),
        "--limit", "1",
    ])
    assert rc == 0
    assert captured["limit"] == 1




def test_radio_limit_caps_after_request(tmp_path, monkeypatch):
    captured = {}

    def _radio(**kw):
        captured["limit"] = kw.get("limit")
        return {
            "seed": "S", "seed_video_id": "v1", "total_tracks": 2,
            "requested_tracks": 100, "total_est_size_human": "1 KB",
            "tracks": ["A - T"],
        }

    monkeypatch.setattr("packrat.queues.build_radio", _radio)
    rc = cli.main([
        "discover", "radio", "v1", "--headers", "/tmp/x",
        "--output", str(tmp_path), "--dry-run", "--limit", "2",
    ])
    assert rc == 0
    assert captured["limit"] == 2


def test_artist_select_skips_picker_and_requires_yes(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("packrat.cli.YTMusic", lambda *_a, **_k: object())
    monkeypatch.setattr(
        "packrat.artists.search_artists",
        lambda *_a, **_k: [{"name": "BABYMETAL", "browseId": "b"}],
    )
    catalog = {
        "artist": "BABYMETAL", "label": "BABYMETAL", "browseId": "b",
        "payload": {"entries": [{"video_id": "v1", "status": "pending"}]},
        "entries": [{"video_id": "v1", "status": "pending"}],
        "totals": {"measured_s_per_track": None, "excluded_counts": {}},
        "manifest_path": str(tmp_path / "artist.json"),
    }
    monkeypatch.setattr("packrat.artists.build_catalog", lambda *_a, **_k: catalog)
    monkeypatch.setattr("packrat.artists.build_sections", lambda *_a, **_k: {"A": [], "S": []})
    captured = {}
    monkeypatch.setattr(
        "packrat.artists.parse_selection",
        lambda expr: captured.setdefault("expr", expr) or [],
    )
    monkeypatch.setattr(
        "packrat.artists.apply_selection",
        lambda catalog, *_a, **_k: catalog,
    )
    monkeypatch.setattr("packrat.artists.save_manifest", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "packrat.artists.summarize_entries",
        lambda *_a, **_k: {
            "new_tracks": 1, "est_human": "1 KB", "est_time_seconds": 60,
            "est_source": "default", "measured_s_per_track": 0,
        },
    )
    called = []
    monkeypatch.setattr(
        "packrat.artists.download_artist",
        lambda **_kw: called.append(True) or {
            "manifest_path": "m", "m3u8_dir": "d",
            "summary": {"downloaded": 0, "failed": 0, "stopped": False},
        },
    )
    rc = cli.main([
        "artist", "BABYMETAL", "--headers", "/tmp/x",
        "--output", str(tmp_path), "--archive", str(tmp_path / "arc"),
        "--select", "A[1]", "--yes",
    ])
    assert rc == 0
    assert captured["expr"] == "A[1]"
    assert called


def test_artist_select_no_tty_no_yes_exits_2(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("packrat.cli.YTMusic", lambda *_a, **_k: object())
    monkeypatch.setattr(
        "packrat.artists.search_artists",
        lambda *_a, **_k: [{"name": "BABYMETAL", "browseId": "b"}],
    )
    catalog = {
        "artist": "BABYMETAL", "label": "BABYMETAL", "browseId": "b",
        "payload": {"entries": []}, "entries": [],
        "totals": {"measured_s_per_track": None, "excluded_counts": {}},
        "manifest_path": str(tmp_path / "artist.json"),
    }
    monkeypatch.setattr("packrat.artists.build_catalog", lambda *_a, **_k: catalog)
    monkeypatch.setattr("packrat.artists.build_sections", lambda *_a, **_k: {"A": [], "S": []})
    monkeypatch.setattr("packrat.artists.parse_selection", lambda _expr: [])
    monkeypatch.setattr("packrat.artists.apply_selection", lambda catalog, *_a, **_k: catalog)
    monkeypatch.setattr("packrat.artists.save_manifest", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "packrat.artists.summarize_entries",
        lambda *_a, **_k: {
            "new_tracks": 0, "est_human": "0 B", "est_time_seconds": 0,
            "est_source": "default", "measured_s_per_track": 0,
        },
    )
    called = []
    monkeypatch.setattr("packrat.artists.download_artist", lambda **_kw: called.append(True))
    rc = cli.main([
        "artist", "BABYMETAL", "--headers", "/tmp/x",
        "--output", str(tmp_path), "--archive", str(tmp_path / "arc"),
        "--select", "A[1]",
    ])
    assert rc == 2
    assert not called


def test_artist_scope_top_songs_removed_from_cli():
    with pytest.raises(SystemExit) as exc:
        _args(["artist", "BABYMETAL", "--scope", "top-songs"])
    assert exc.value.code == 2


@pytest.mark.parametrize("sub,plan,download", [
    ("queues", "packrat.queues.discover_queues", "packrat.queues.download_queues"),
    ("albums", "packrat.albums.discover_albums", "packrat.albums.download_albums"),
])
def test_discover_plan_download_limit(tmp_path, monkeypatch, sub, plan, download):
    captured = {}
    if sub == "queues":
        result = {
            "manifest_path": "m", "seeds_used": 1, "total_tracks": 3,
            "total_est_size_human": "1 KB",
            "queues": [{"seed": "S", "tracks": 3, "unique_artists": 1,
                        "est_size": 1000}],
        }
    else:
        result = {
            "manifest_path": "m", "artists_considered": 1,
            "total_new_tracks": 3, "total_already_owned": 0,
            "total_skipped_size": 0, "total_est_size_human": "1 KB",
            "albums": [{"artist": "A", "album": "Al", "tracks": 3,
                        "already_owned": 0, "est_size": 1000}],
        }
    monkeypatch.setattr(plan, lambda **_kw: result)

    def _download(**kw):
        captured["limit"] = kw.get("limit")
        return {"manifest_path": "m", "summary": {"downloaded": 0, "failed": 0,
                "stopped": False}}

    monkeypatch.setattr(download, _download)
    rc = cli.main([
        "discover", sub, "--headers", "/tmp/x", "--yes",
        "--output", str(tmp_path), "--archive", str(tmp_path / "arc"),
        "--limit", "2",
    ])
    assert rc == 0
    assert captured["limit"] == 2


def test_artist_limit_is_global_across_catalogs(tmp_path, monkeypatch):
    monkeypatch.setattr("packrat.cli.YTMusic", lambda *_a, **_k: object())
    monkeypatch.setattr(
        "packrat.artists.search_artists",
        lambda _yt, spec: [{"name": spec, "browseId": f"b{spec}"}],
    )

    def _catalog(_headers, browse_id, *_a, **_k):
        name = browse_id[1:]
        entries = [
            {"video_id": f"{name}{i}", "status": "pending"}
            for i in range(1 if name == "A" else 2)
        ]
        return {
            "artist": name, "label": name, "browseId": browse_id,
            "payload": {"entries": entries},
            "entries": entries,
            "totals": {"measured_s_per_track": None, "excluded_counts": {}},
            "manifest_path": str(tmp_path / f"artist-{name}.json"),
        }

    monkeypatch.setattr("packrat.artists.build_catalog", _catalog)
    monkeypatch.setattr("packrat.artists.build_sections", lambda *_a, **_k: [])
    monkeypatch.setattr("packrat.artists.save_manifest", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "packrat.artists.summarize_entries",
        lambda *_a, **_k: {
            "new_tracks": 1, "est_human": "1 KB", "est_time_seconds": 60,
            "est_source": "default", "measured_s_per_track": 0,
        },
    )
    calls = []

    def _download(**kw):
        calls.append((kw["label"], kw["limit"]))
        return {
            "manifest_path": "m", "m3u8_dir": "d",
            "summary": {"downloaded": 1, "failed": 0, "stopped": False},
        }

    monkeypatch.setattr("packrat.artists.download_artist", _download)
    rc = cli.main([
        "artist", "A", "--also", "B", "--headers", "/tmp/x", "--yes",
        "--output", str(tmp_path), "--archive", str(tmp_path / "arc"),
        "--limit", "1",
    ])
    assert rc == 0
    assert calls == [("A", 1)]


def test_sync_limit_applies_after_archive_filtering(tmp_path, monkeypatch):
    tracks = [
        {"video_id": f"v{i}", "title": f"T{i}", "artist": "A", "album": "Al",
         "duration": 100}
        for i in range(5)
    ]
    monkeypatch.setattr("packrat.sync.resolve_tracks", lambda *_a: tracks)

    class _Archive:
        def __init__(self, *_a, **_k):
            pass

        def __contains__(self, video_id):
            return video_id == "v0"

    monkeypatch.setattr("packrat.sync.Archive", _Archive)
    archived_file = tmp_path / "A" / "Al" / "T0.m4a"
    archived_file.parent.mkdir(parents=True)
    archived_file.write_bytes(b"x")
    monkeypatch.setattr(
        "packrat.sync.verify_audio", lambda *_a, **_k: type(
            "C", (), {"ok": True, "size": 1}
        )()
    )
    monkeypatch.setattr(
        "packrat.sync.estimate_track", lambda *_a, **_k: {"filesize": 1000}
    )
    captured = {}

    def _batch(entries, **_kw):
        captured["ids"] = [e["video_id"] for e in entries]
        return {
            "downloaded": 0, "archived": 0, "skipped_size": 0,
            "failed": 0, "stopped": False, "stop_reason": "completed",
        }

    monkeypatch.setattr("packrat.sync.run_batch_download", _batch)
    rc = cli.main([
        "sync", "--source", "liked", "--headers", "/tmp/x",
        "--output", str(tmp_path), "--archive", str(tmp_path / "arc"),
        "--limit", "2", "--yes", "--force-new-archive",
    ])
    assert rc == 0
    assert captured["ids"] == ["v1", "v2"]
