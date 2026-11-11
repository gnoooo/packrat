"""Regression tests for the CLI uniformity fixes."""

from __future__ import annotations

import argparse
import logging
import pathlib

import pytest

from ytm_offline import cli, cli_args, cli_compat
from ytm_offline.manifest import Manifest


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
    monkeypatch.setattr("ytm_offline.cli.run_batch_download",
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

    monkeypatch.setattr("ytm_offline.cli.run_batch_download", _batch)
    rc = cli.main(["download-manifest", "--manifest", str(manifest),
                   "--failed-only", "--yes"])
    assert rc == 0
    assert seen["ids"] == ["v1"]


# 4. verbose is wired
def test_verbose_sets_debug_logging(monkeypatch):
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: setattr(logging, "_last", kw))
    monkeypatch.setattr("ytm_offline.cli.cmd_auth", lambda _args: 0)
    cli.main(["auth", "-v"])
    assert logging._last["level"] == logging.DEBUG


# 5. artist duration filters are forwarded
def test_artist_min_duration_forwarded(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("ytm_offline.cli.YTMusic", lambda *_a, **_k: object())
    monkeypatch.setattr("ytm_offline.artists.search_artists",
                        lambda *_a, **_k: [{"name": "A", "browseId": "b"}])
    seen = {}

    def _catalog(*_a, **kw):
        seen.update(kw)
        return {"artist": "A", "label": "A", "browseId": "b",
                "payload": {"entries": []}, "entries": [],
                "totals": {"measured_s_per_track": None, "excluded_counts": {}},
                "manifest_path": str(tmp_path / "a.json")}

    monkeypatch.setattr("ytm_offline.artists.build_catalog", _catalog)
    monkeypatch.setattr("ytm_offline.artists.build_sections", lambda *_a, **_k: [])
    monkeypatch.setattr("ytm_offline.artists.save_manifest", lambda *_a, **_k: None)
    monkeypatch.setattr("ytm_offline.artists.summarize_entries",
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
        "ytm_offline.discover_phase4.run_discover_phase4",
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
def test_migrate_layout_help_and_narrow_options():
    text = _help(("migrate-layout",))
    assert "--undo" in text
    assert "print the move plan" in text
    with pytest.raises(SystemExit):
        _parse(["migrate-layout", "--archive", "x"])


# 8. --sources rename, --source hidden alias
def test_sources_rename_and_alias():
    assert _parse(["discover", "sections", "--sources", "home"]).source == "home"
    assert _parse(["discover", "sections", "--source", "home"]).source == "home"


# 9. taste per-album rename, old hidden
def test_taste_per_album_rename():
    assert _parse(["discover", "taste", "--per-album", "4"]).per_container == 4
    assert _parse(["discover", "taste", "--per-container", "4"]).per_container == 4


# 10. deprecated aliases hidden from help
def test_deprecated_aliases_hidden():
    taste = _help(("discover", "taste"))
    queues = _help(("discover", "queues"))
    assert "--quota-deep" not in taste
    assert "--per-queue-limit" not in queues


# 11. radio limit help is standard
def test_radio_limit_help_is_standard():
    text = _help(("discover", "radio"))
    assert "max new tracks to download this run" in text
    assert "max tracks in the radio" not in text


# 12. sync rejects --yes; shared help is neutral
def test_sync_has_no_yes_and_neutral_help():
    with pytest.raises(SystemExit):
        _parse(["sync", "--source", "liked", "--yes"])
    assert "--yes" not in _help(("sync",))
    assert "skip confirmation prompts" in _help(("artist",))


# 13. narrow parents
def test_narrow_parents():
    with pytest.raises(SystemExit):
        _parse(["auth", "--cookies", "c.txt"])
    with pytest.raises(SystemExit):
        _parse(["library", "--cookies", "c.txt"])
    with pytest.raises(SystemExit):
        _parse(["download-one", "v1", "--headers", "h.json"])
    with pytest.raises(SystemExit):
        _parse(["download-manifest", "--manifest", "m.json", "--headers", "h.json"])
    with pytest.raises(SystemExit):
        _parse(["discover", "sections", "--tui"])


# 14. invalid headers exit 2 naming YTM_HEADERS
def test_invalid_headers_exit_2(monkeypatch, capsys):
    monkeypatch.setenv("YTM_HEADERS", "/definitely/missing.json")
    rc = cli.main(["sync", "--source", "liked"])
    assert rc == 2
    assert "YTM_HEADERS" in capsys.readouterr().err


# 15. taste --quota applies to buckets
def test_taste_quota_applies(monkeypatch):
    monkeypatch.setattr("ytm_offline.cli.cmd_discover_phase4", lambda args: (
        setattr(args, "_seen", (args.quota_deep, args.quota_adjacent)) or 0
    ))
    args = _parse(["discover", "taste", "--headers", "/tmp/x", "--quota", "7"])
    cli_compat.adapt_discover_taste(args)
    assert args.quota_deep == 7
    assert args.quota_adjacent == 7


# 17. theme validation
def test_theme_validation():
    with pytest.raises(SystemExit):
        _parse(["auth", "--theme", "nope"])


# 18. cookies-from-browser help has syntax
def test_cookies_help_has_syntax():
    assert "firefox[:profile]" in _help(("sync",))


def test_exclude_singles_shared_flag():
    assert "--exclude-singles" in _help(("artist",))
    assert "--exclude-singles" in _help(("discover", "albums"))
