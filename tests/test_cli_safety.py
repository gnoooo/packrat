"""Safety tests for the new CLI: no accidental downloads, no network."""

from __future__ import annotations

import argparse
import pathlib

import pytest

from packrat import cli, cli_compat
from packrat.manifest import Manifest


def _args(argv):
    parser = cli.build_parser()
    raw = cli_compat.move_global_options_after_subcommand(parser, argv)
    return parser.parse_args(raw)


def _manifest(tmp_path: pathlib.Path) -> Manifest:
    m = Manifest()
    m.entries = [
        {
            "video_id": "v1",
            "title": "T",
            "artist": "A",
            "album": "Al",
            "duration": 100,
            "est_size": 1000,
            "status": "pending",
            "path": None,
            "error": None,
        }
    ]
    return m


# ---------------------------------------------------------------------------
# b) No TTY / no --yes must not download; --dry-run must not download
# ---------------------------------------------------------------------------

def test_discover_sections_no_tty_no_yes_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(
        "packrat.discover.run_discover",
        lambda **_kw: (_manifest(tmp_path), {
            "total": 1, "per_source": {"home": 1},
            "estimated_size_human": "1 KB", "skipped_by_size": 0,
            "top_artists": [("A", 1)],
        }),
    )
    called = []
    monkeypatch.setattr(
        "packrat.discover.download_pending",
        lambda **_kw: called.append(True),
    )
    rc = cli.cmd_discover(_args([
        "discover", "sections", "--headers", "/tmp/x",
        "--output", str(tmp_path), "--source", "home",
    ]))
    assert rc == 2
    assert not called
    assert "total tracks: 1" in capsys.readouterr().out


def test_discover_sections_dry_run_does_not_download_or_touch_archive(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(
        "packrat.discover.run_discover",
        lambda **_kw: (_manifest(tmp_path), {
            "total": 1, "per_source": {"home": 1},
            "estimated_size_human": "1 KB", "skipped_by_size": 0,
            "top_artists": [("A", 1)],
        }),
    )
    called = []
    monkeypatch.setattr(
        "packrat.discover.download_pending",
        lambda **_kw: called.append(True),
    )
    archive = tmp_path / "archive"
    rc = cli.cmd_discover(_args([
        "discover", "sections", "--headers", "/tmp/x",
        "--output", str(tmp_path), "--source", "home",
        "--archive", str(archive), "--dry-run",
    ]))
    assert rc == 0
    assert not called
    assert not archive.exists()


def test_discover_sections_yes_downloads(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(
        "packrat.discover.run_discover",
        lambda **_kw: (_manifest(tmp_path), {
            "total": 1, "per_source": {"home": 1},
            "estimated_size_human": "1 KB", "skipped_by_size": 0,
            "top_artists": [("A", 1)],
        }),
    )
    called = []
    monkeypatch.setattr(
        "packrat.discover.download_pending",
        lambda **_kw: called.append(True) or 0,
    )
    rc = cli.cmd_discover(_args([
        "discover", "sections", "--headers", "/tmp/x",
        "--output", str(tmp_path), "--source", "home", "--yes",
    ]))
    assert rc == 0
    assert called


def test_discover_sections_tty_prompt_yes_downloads(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "y")
    monkeypatch.setattr(
        "packrat.discover.run_discover",
        lambda **_kw: (_manifest(tmp_path), {
            "total": 1, "per_source": {"home": 1},
            "estimated_size_human": "1 KB", "skipped_by_size": 0,
            "top_artists": [("A", 1)],
        }),
    )
    called = []
    monkeypatch.setattr(
        "packrat.discover.download_pending",
        lambda **_kw: called.append(True) or 0,
    )
    rc = cli.cmd_discover(_args([
        "discover", "sections", "--headers", "/tmp/x",
        "--output", str(tmp_path), "--source", "home",
    ]))
    assert rc == 0
    assert called


def test_discover_sections_tty_prompt_enter_is_safe(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "")
    monkeypatch.setattr(
        "packrat.discover.run_discover",
        lambda **_kw: (_manifest(tmp_path), {
            "total": 1, "per_source": {"home": 1},
            "estimated_size_human": "1 KB", "skipped_by_size": 0,
            "top_artists": [("A", 1)],
        }),
    )
    called = []
    monkeypatch.setattr(
        "packrat.discover.download_pending",
        lambda **_kw: called.append(True) or 0,
    )
    rc = cli.cmd_discover(_args([
        "discover", "sections", "--headers", "/tmp/x",
        "--output", str(tmp_path), "--source", "home",
    ]))
    assert rc == 2
    assert not called


def test_discover_sections_tty_prompt_no_downloads(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")
    monkeypatch.setattr(
        "packrat.discover.run_discover",
        lambda **_kw: (_manifest(tmp_path), {
            "total": 1, "per_source": {"home": 1},
            "estimated_size_human": "1 KB", "skipped_by_size": 0,
            "top_artists": [("A", 1)],
        }),
    )
    called = []
    monkeypatch.setattr(
        "packrat.discover.download_pending",
        lambda **_kw: called.append(True) or 0,
    )
    rc = cli.cmd_discover(_args([
        "discover", "sections", "--headers", "/tmp/x",
        "--output", str(tmp_path), "--source", "home",
    ]))
    assert rc == 2
    assert not called


def _queues_result(tmp_path):
    return {
        "manifest_path": str(tmp_path / "queues.json"),
        "seeds_used": 1,
        "total_tracks": 1,
        "total_est_size_human": "1 KB",
        "queues": [{"seed": "S", "tracks": 1, "unique_artists": 1, "est_size": 1000}],
    }


def _albums_result(tmp_path):
    return {
        "manifest_path": str(tmp_path / "albums.json"),
        "artists_considered": 1,
        "total_new_tracks": 1,
        "total_already_owned": 0,
        "total_skipped_size": 0,
        "total_est_size_human": "1 KB",
        "albums": [{"artist": "A", "album": "Al", "tracks": 1, "already_owned": 0, "est_size": 1000}],
    }


def _radio_result():
    return {
        "seed": "S", "seed_video_id": "v1", "total_tracks": 1,
        "total_est_size_human": "1 KB", "tracks": ["A - T (100s)"],
    }


@pytest.mark.parametrize("sub", ["queues", "albums"])
def test_discover_plan_commands_no_tty_no_yes_exit_2(tmp_path, monkeypatch, sub):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    if sub == "queues":
        monkeypatch.setattr(
            "packrat.queues.discover_queues",
            lambda **_kw: _queues_result(tmp_path),
        )
        download = "packrat.queues.download_queues"
    else:
        monkeypatch.setattr(
            "packrat.albums.discover_albums",
            lambda **_kw: _albums_result(tmp_path),
        )
        download = "packrat.albums.download_albums"
    called = []
    monkeypatch.setattr(download, lambda **_kw: called.append(True))
    rc = cli.main([
        "discover", sub, "--headers", "/tmp/x",
        "--output", str(tmp_path), "--archive", str(tmp_path / "arc"),
    ])
    assert rc == 2
    assert not called


def test_discover_queues_dry_run_does_not_download(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(
        "packrat.queues.discover_queues",
        lambda **_kw: _queues_result(tmp_path),
    )
    called = []
    monkeypatch.setattr(
        "packrat.queues.download_queues",
        lambda **_kw: called.append(True),
    )
    archive = tmp_path / "arc"
    rc = cli.main([
        "discover", "queues", "--headers", "/tmp/x",
        "--output", str(tmp_path), "--archive", str(archive), "--dry-run",
    ])
    assert rc == 0
    assert not called
    assert not archive.exists()


def test_discover_radio_no_tty_no_yes_exits_2(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(
        "packrat.queues.build_radio",
        lambda **_kw: _radio_result(),
    )
    rc = cli.main([
        "discover", "radio", "v1", "--headers", "/tmp/x",
        "--output", str(tmp_path), "--archive", str(tmp_path / "arc"),
    ])
    assert rc == 2


def test_discover_radio_dry_run_does_not_download(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    calls = []
    monkeypatch.setattr(
        "packrat.queues.build_radio",
        lambda **kw: calls.append(kw) or _radio_result(),
    )
    archive = tmp_path / "arc"
    rc = cli.main([
        "discover", "radio", "v1", "--headers", "/tmp/x",
        "--output", str(tmp_path), "--archive", str(archive), "--dry-run",
    ])
    assert rc == 0
    assert all(c["dry_run"] for c in calls)
    assert not archive.exists()


def test_artist_no_tty_no_yes_exits_2(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("packrat.cli.YTMusic", lambda *_a, **_k: object())
    monkeypatch.setattr(
        "packrat.artists.search_artists",
        lambda *_a, **_k: [{"name": "A", "browseId": "b"}],
    )
    catalog = {
        "artist": "A", "label": "A", "browseId": "b",
        "payload": {"entries": []}, "entries": [],
        "totals": {"measured_s_per_track": None, "excluded_counts": {}},
        "manifest_path": str(tmp_path / "artist.json"),
    }
    monkeypatch.setattr("packrat.artists.build_catalog", lambda *_a, **_k: catalog)
    monkeypatch.setattr("packrat.artists.build_sections", lambda *_a, **_k: [])
    monkeypatch.setattr("packrat.artists.save_manifest", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "packrat.artists.summarize_entries",
        lambda *_a, **_k: {
            "new_tracks": 0, "est_human": "0 B", "est_time_seconds": 0,
            "est_source": "default", "measured_s_per_track": 0,
        },
    )
    called = []
    monkeypatch.setattr(
        "packrat.artists.download_artist",
        lambda **_kw: called.append(True),
    )
    rc = cli.main([
        "artist", "A", "--headers", "/tmp/x",
        "--output", str(tmp_path), "--archive", str(tmp_path / "arc"),
    ])
    assert rc == 2
    assert not called


# ---------------------------------------------------------------------------
# c) Help/version/ui/hidden commands work without auth; invalid env is clear
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ["-h"], ["--version"],
    ["retry", "--help"], ["discover-phase4", "--help"],
])
def test_no_auth_needed_for_help_version_ui_and_hidden(argv, monkeypatch):
    monkeypatch.setenv("PACKRAT_HEADERS", "/definitely/missing.json")
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 0






def test_invalid_env_exits_2_naming_variable(monkeypatch, capsys):
    monkeypatch.setenv("PACKRAT_MANIFEST_FORMAT", "xml")
    with pytest.raises(SystemExit) as exc:
        cli.main(["sync", "--source", "liked", "--headers", "/tmp/x"])
    assert exc.value.code == 2
    assert "PACKRAT_MANIFEST_FORMAT" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# d) Legacy command names emit exactly one deprecation line on stderr
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("old,new_prefix", [
    ("retry", ["download-manifest", "--failed-only"]),
    ("discover-phase4", ["discover", "taste"]),
    ("discover-queues", ["discover", "queues"]),
    ("discover-albums", ["discover", "albums"]),
    ("radio", ["discover", "radio"]),
])
def test_legacy_command_rewrite_one_stderr_line(old, new_prefix, capsys):
    out = cli_compat.rewrite_legacy_argv([old, "--manifest", "m.json"])
    assert out[:len(new_prefix)] == new_prefix
    captured = capsys.readouterr()
    assert captured.out == ""
    assert len([line for line in captured.err.splitlines() if line.strip()]) == 1


# ---------------------------------------------------------------------------
# e) Visible help only
# ---------------------------------------------------------------------------

def test_visible_help_has_no_legacy_commands(capsys):
    with pytest.raises(SystemExit):
        cli.main(["-h"])
    out = capsys.readouterr().out
    import re
    for name in ("retry", "discover-phase4", "discover-queues",
                 "discover-albums"):
        assert not re.search(rf"^    {re.escape(name)}\b", out, re.M)
    assert "discover" in out




def test_archive_warning_requires_confirmation_even_with_yes(tmp_path, monkeypatch):
    output = tmp_path / "music"
    output.mkdir()
    (output / "song.m4a").write_bytes(b"x")
    archive = tmp_path / "new_archive"
    args = _args([
        "artist", "X", "--headers", "/tmp/x",
        "--output", str(output), "--archive", str(archive), "--yes",
    ])
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli._check_archive_safety(args, output) is False


def test_force_new_archive_bypasses_warning(tmp_path):
    output = tmp_path / "music"
    output.mkdir()
    (output / "song.m4a").write_bytes(b"x")
    args = _args([
        "sync", "--source", "liked", "--headers", "/tmp/x",
        "--output", str(output), "--archive", str(tmp_path / "arc"),
        "--force-new-archive",
    ])
    assert cli._check_archive_safety(args, output) is True


def test_artist_accepts_one_positional_only():
    with pytest.raises(SystemExit) as exc:
        _args(["artist", "A", "B"])
    assert exc.value.code == 2


def test_artist_also_adds_extra_targets():
    args = _args(["artist", "A", "--also", "B", "--also", "C"])
    assert args.target == "A"
    assert args.also == ["B", "C"]


def test_print_paths_are_absolute(tmp_path, capsys):
    cli._print_paths(tmp_path, "arc")
    out = capsys.readouterr().out
    assert f"output folder: {tmp_path.resolve()}" in out
    assert "download history:" in out


def test_auth_api_failure_is_clean_not_a_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "packrat.sync.resolve_tracks",
        lambda *_a: (_ for _ in ()).throw(KeyError("twoColumnBrowseResultsRenderer")),
    )
    rc = cli.main([
        "sync", "--source", "liked", "--headers", "/tmp/x",
        "--output", str(tmp_path), "--dry-run",
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "unexpected YouTube Music response" in err
    assert "Traceback" not in err


def test_sync_dry_run_prints_manifest_recap(tmp_path, monkeypatch, capsys):
    tracks = [
        {"video_id": "v1", "title": "T", "artist": "A", "album": "Al",
         "duration": 100}
    ]
    monkeypatch.setattr("packrat.sync.resolve_tracks", lambda *_a: tracks)
    monkeypatch.setattr("packrat.sync.Archive", lambda *_a, **_k: type(
        "A", (), {"__contains__": lambda self, _item: False}
    )())
    monkeypatch.setattr(
        "packrat.sync.estimate_track", lambda *_a, **_k: {"filesize": 1000}
    )
    rc = cli.main([
        "sync", "--source", "liked", "--headers", "/tmp/x",
        "--output", str(tmp_path), "--dry-run",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "pending tracks: 1" in out
    assert "manifest:" in out
    assert "dry-run: manifest written" in out
