"""Regression tests for the CLI uniformity fixes."""

from __future__ import annotations

import argparse
import logging
import pathlib

import pytest

from packrat import cli, cli_args, cli_compat
from packrat.manifest import Manifest


def _parse(argv):
    parser = cli.build_parser()
    raw = cli_compat.move_global_options_after_subcommand(parser, argv)
    return parser.parse_args(raw)


def _help(path=()):
    parser = cli.build_parser()
    node = parser
    for name in path:
        for action in node._actions:
            if isinstance(action, argparse._SubParsersAction):
                node = action.choices[name]
                break
    return node.format_help()


# 1. download-one is narrow
@pytest.mark.parametrize("flag", ["--dry-run", "--limit", "--yes", "--tui", "--archive", "--delay", "--max-size", "--headers"])
def test_download_one_rejects_unused_options(flag):
    with pytest.raises(SystemExit) as exc:
        _parse(["download-one", "v1", flag])
    assert exc.value.code == 2


# 2. download-manifest dry-run
def test_download_manifest_dry_run_stops(tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "playlists" / "m.manifest.json"
    manifest.parent.mkdir(parents=True)
    m = Manifest()
    m.entries = [{"video_id": "v1", "title": "T", "artist": "A", "album": "Al",
                  "duration": 1, "est_size": 1, "status": "pending"}]
    m.write(manifest)
    called = []
    monkeypatch.setattr("packrat.cli.run_batch_download",
                        lambda **_kw: called.append(True))
    rc = cli.main(["download-manifest", "--manifest", str(manifest), "--dry-run"])
    assert rc == 0
    assert not called
    assert "dry-run" in capsys.readouterr().out


# 3. failed-only retries failed entries
def test_failed_only_selects_failed_entries(tmp_path, monkeypatch):
    manifest = tmp_path / "playlists" / "m.manifest.json"
    manifest.parent.mkdir(parents=True)
    m = Manifest()
    m.entries = [
        {"video_id": "v1", "title": "T", "artist": "A", "album": "Al",
         "duration": 1, "est_size": 1, "status": "failed"},
        {"video_id": "v2", "title": "T", "artist": "A", "album": "Al",
         "duration": 1, "est_size": 1, "status": "pending"},
    ]
    m.write(manifest)
    seen = {}

    def _batch(entries, **_kw):
        seen["ids"] = [e["video_id"] for e in entries]
        return {"downloaded": 0, "failed": 0, "stopped": False}

    monkeypatch.setattr("packrat.cli.run_batch_download", _batch)
    rc = cli.main(["download-manifest", "--manifest", str(manifest),
                   "--failed-only", "--yes"])
    assert rc == 0
    assert seen["ids"] == ["v1"]


# 4. verbose is wired
def test_verbose_sets_debug_logging(monkeypatch):
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: setattr(logging, "_last", kw))
    monkeypatch.setattr("packrat.cli.cmd_auth", lambda _args: 0)
    cli.main(["auth", "-v"])
    assert logging._last["level"] == logging.DEBUG


# 5. artist duration filters are forwarded
def test_artist_min_duration_forwarded(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("packrat.cli.YTMusic", lambda *_a, **_k: object())
    monkeypatch.setattr("packrat.artists.search_artists",
                        lambda *_a, **_k: [{"name": "A", "browseId": "b"}])
    seen = {}

    def _catalog(*_a, **kw):
        seen.update(kw)
        return {"artist": "A", "label": "A", "browseId": "b",
                "payload": {"entries": []}, "entries": [],
                "totals": {"measured_s_per_track": None, "excluded_counts": {}},
                "manifest_path": str(tmp_path / "a.json")}

    monkeypatch.setattr("packrat.artists.build_catalog", _catalog)
    monkeypatch.setattr("packrat.artists.build_sections", lambda *_a, **_k: [])
    monkeypatch.setattr("packrat.artists.save_manifest", lambda *_a, **_k: None)
    monkeypatch.setattr("packrat.artists.summarize_entries",
                        lambda *_a, **_k: {"new_tracks": 0, "est_human": "0 B",
                                           "est_time_seconds": 0,
                                           "est_source": "default",
                                           "measured_s_per_track": 0})
    cli.main(["artist", "A", "--headers", "/tmp/x", "--dry-run",
              "--min-duration", "300"])
    assert seen["min_duration"] == 300
    assert seen["duration_window"] is True


# 6. discover taste is plan-only and narrow
def test_discover_taste_plan_only_and_rejects_yes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "packrat.discover_phase4.run_discover_phase4",
        lambda **_kw: (Manifest(), {"total": 0, "per_bucket": {}, "funnel": {},
                                    "estimated_size_human": "0 B",
                                    "skipped_by_size": 0, "top_artists": []}),
    )
    rc = cli.main(["discover", "taste", "--headers", "/tmp/x",
                   "--output", str(tmp_path)])
    assert rc == 0
    with pytest.raises(SystemExit):
        _parse(["discover", "taste", "--yes"])


# 7. migrate-layout own parser


def test_exclude_singles_shared_flag():
    assert "--exclude-singles" in _help(("artist",))
    assert "--exclude-singles" in _help(("discover", "albums"))


# 19. discover taste passes the required family_cap argument
def test_taste_passes_family_cap(tmp_path, monkeypatch):
    captured = {}

    def _run(**kwargs):
        captured.update(kwargs)
        return Manifest(), {
            "total": 0, "per_bucket": {}, "funnel": {},
            "estimated_size_human": "0.0 B", "skipped_by_size": 0,
            "top_artists": [],
        }

    monkeypatch.setattr(
        "packrat.discover_phase4.run_discover_phase4", _run
    )
    rc = cli.main([
        "discover", "taste", "--headers", "/tmp/x",
        "--output", str(tmp_path), "--family-cap", "7",
    ])
    assert rc == 0
    assert captured["family_cap"] == 7

    rc = cli.main([
        "discover", "taste", "--headers", "/tmp/x",
        "--output", str(tmp_path),
    ])
    assert rc == 0
    assert captured["family_cap"] == 0


# 20. download-one must hand yt-dlp a parsed browser tuple
def test_download_one_parses_cookie_spec(tmp_path, monkeypatch):
    downloaded = tmp_path / "song.m4a"
    downloaded.write_bytes(b"x")
    captured = {}

    class FakeYDL:
        def __init__(self, opts):
            captured.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def extract_info(self, _url, download=True):
            return {"filepath": str(downloaded)}

    monkeypatch.setattr(cli, "_check_deno", lambda: (True, "2.9.5"))
    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYDL)
    monkeypatch.setattr(cli, "_final_filepath", lambda *_a: downloaded)
    monkeypatch.setattr(cli, "_verify_with_ffprobe", lambda _p: {})
    monkeypatch.setattr(cli, "_verify_with_mutagen", lambda _p: {})

    args = _parse([
        "download-one", "abcdefghijk",
        "--cookies-from-browser", "firefox:default-release",
        "--output", str(tmp_path),
    ])
    assert cli.cmd_download_one(args) == 0
    assert captured["cookiesfrombrowser"] == (
        "firefox", "default-release", None, None
    )


# 21. --limit caps downloads after the archive check, not the API fetch
class _EmptyArchive:
    def __init__(self, *_a, **_k):
        pass

    def __contains__(self, _item):
        return False


def test_sync_limit_fetches_the_full_source(tmp_path, monkeypatch):
    tracks = [
        {"video_id": f"v{i}", "title": f"T{i}", "artist": "A", "album": "Al",
         "duration": 100}
        for i in range(5)
    ]
    seen = {}
    captured = {}

    def _resolve(source, headers, limit=None):
        seen["limit"] = limit
        return tracks

    def _batch(entries, **_kw):
        captured["n"] = len(entries)
        return {
            "downloaded": 0, "archived": 0, "skipped_size": 0,
            "failed": 0, "stopped": False, "stop_reason": "completed",
        }

    monkeypatch.setattr("packrat.sync.resolve_tracks", _resolve)
    monkeypatch.setattr("packrat.sync.Archive", _EmptyArchive)
    monkeypatch.setattr(
        "packrat.sync.estimate_track", lambda *_a, **_k: {"filesize": 1000}
    )
    monkeypatch.setattr("packrat.sync.run_batch_download", _batch)

    rc = cli.main([
        "sync", "--source", "liked", "--headers", "/tmp/x",
        "--output", str(tmp_path), "--archive", str(tmp_path / "arc"),
        "--limit", "2",
    ])
    assert rc == 0
    assert seen["limit"] is None
    assert captured["n"] == 2


# 22. json+csv is a format, never a manifest filename extension
def test_sync_json_csv_keeps_json_manifest_name(tmp_path, monkeypatch):
    monkeypatch.setenv("PACKRAT_MANIFEST_FORMAT", "json+csv")
    tracks = [
        {"video_id": "v1", "title": "T", "artist": "A", "album": "Al",
         "duration": 100}
    ]
    monkeypatch.setattr("packrat.sync.resolve_tracks", lambda *_a: tracks)
    monkeypatch.setattr("packrat.sync.Archive", _EmptyArchive)
    monkeypatch.setattr(
        "packrat.sync.estimate_track", lambda *_a, **_k: {"filesize": 1000}
    )

    rc = cli.main([
        "sync", "--source", "liked", "--headers", "/tmp/x",
        "--output", str(tmp_path), "--archive", str(tmp_path / "arc"),
        "--dry-run",
    ])
    assert rc == 0
    playlists = tmp_path / "playlists"
    assert (playlists / "liked.manifest.json").exists()
    assert (playlists / "liked.manifest.csv").exists()
    assert not list(playlists.glob("*json+csv*"))


# 23. a manifest outside playlists/ cannot infer the output root
def test_download_manifest_outside_playlists_requires_output(tmp_path, capsys):
    import json

    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps({"entries": [{"video_id": "v1", "status": "pending"}]}),
        encoding="utf-8",
    )
    args = _parse([
        "download-manifest", "--manifest", str(manifest), "--yes",
    ])
    assert cli.cmd_download_manifest(args) == 2
    assert "--output is required" in capsys.readouterr().err


def test_keyerror_is_not_reported_as_auth_failure():
    from packrat import cli

    message = cli._friendly_error(KeyError("header"))
    assert "authentication" not in message.lower()
    assert "YouTube Music response" in message or "header" in message
