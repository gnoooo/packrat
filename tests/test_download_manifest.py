"""Tests for the download-manifest command."""

from __future__ import annotations

import json
import pathlib
from unittest.mock import patch

import pytest

from packrat import cli


def _args(tmp_path: pathlib.Path, manifest: pathlib.Path, **kwargs: object):
    argv = ["download-manifest", "--manifest", str(manifest)]
    for key, value in kwargs.items():
        if value is None:
            continue
        flag = f"--{key.replace('_', '-')}"
        if isinstance(value, bool):
            if value:
                argv.append(flag)
        else:
            argv.extend([flag, str(value)])
    return cli.build_parser().parse_args(argv)


def test_manifest_missing(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture):
    missing = tmp_path / "nope.manifest.json"
    args = _args(tmp_path, missing)
    assert cli.cmd_download_manifest(args) == 1
    assert "Saved plan not found" in capsys.readouterr().err


def test_generic_prompt_declines(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
):
    manifest = tmp_path / "playlists" / "queues.manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"entries": [{"video_id": "v1", "status": "pending"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    args = _args(tmp_path, manifest)
    assert cli.cmd_download_manifest(args) == 2
    assert "Not downloading" in capsys.readouterr().out


def test_generic_runs_batch_download(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
):
    manifest = tmp_path / "playlists" / "albums.manifest.json"
    manifest.parent.mkdir(parents=True)
    entries = [
        {
            "video_id": "v1",
            "title": "Song",
            "artist": "Artist",
            "album": "Album",
            "est_size": 16000,
            "status": "pending",
        }
    ]
    manifest.write_text(json.dumps({"entries": entries}), encoding="utf-8")

    def fake_run(entries_arg, **kwargs):
        from packrat import paths
        assert kwargs.get("archive_path") == str(paths.ARCHIVE_PATH)
        assert kwargs.get("mode") == "manifest"
        return {
            "downloaded": 1,
            "archived": 0,
            "skipped_size": 0,
            "failed": 0,
            "stopped": False,
        }

    args = _args(tmp_path, manifest, yes=True)
    with patch("packrat.cli.run_batch_download", side_effect=fake_run):
        assert cli.cmd_download_manifest(args) == 0
    out = capsys.readouterr().out
    assert "pending tracks: 1" in out
    assert "downloaded 1" in out


def test_artist_manifest_routes_to_download_artist(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
):
    manifest = tmp_path / "playlists" / "artist-Test.manifest.json"
    manifest.parent.mkdir(parents=True)
    payload = {
        "command": "artist",
        "label": "Test",
        "artist": "Test Artist",
        "entries": [
            {"video_id": "v1", "title": "T", "artist": "A", "album": "Al",
             "duration": 100, "est_size": 1, "status": "pending",
             "path": None, "error": None},
        ],
    }
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    def fake_download_artist(**kwargs):
        assert kwargs["label"] == "Test"
        assert kwargs["output_root"] == tmp_path
        return {
            "summary": {
                "downloaded": 2,
                "archived": 0,
                "skipped_size": 0,
                "failed": 0,
                "stopped": False,
            },
            "manifest_path": str(manifest),
            "m3u8_dir": str(tmp_path / "playlists" / "artists" / "Test"),
        }

    args = _args(tmp_path, manifest, yes=True)
    with patch("packrat.cli.artists_module.download_artist", side_effect=fake_download_artist):
        assert cli.cmd_download_manifest(args) == 0
    out = capsys.readouterr().out
    assert "downloaded 2" in out


def test_artist_manifest_reports_error(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
):
    manifest = tmp_path / "playlists" / "artist-Test.manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"command": "artist", "label": "Test", "entries": [
            {"video_id": "v1", "title": "T", "artist": "A", "album": "Al",
             "duration": 100, "est_size": 1, "status": "pending",
             "path": None, "error": None},
        ]}),
        encoding="utf-8",
    )

    args = _args(tmp_path, manifest, yes=True)
    with patch(
        "packrat.cli.artists_module.download_artist",
        return_value={"error": "no pending tracks"},
    ):
        assert cli.cmd_download_manifest(args) == 1
    assert "no pending tracks" in capsys.readouterr().err


def test_artist_download_saves_to_the_given_manifest_path(tmp_path):
    from packrat import artist_playlists

    manifest = tmp_path / "playlists" / "custom-name.manifest.json"
    manifest.parent.mkdir(parents=True)
    payload = {
        "command": "artist",
        "label": "Test",
        "entries": [
            {"video_id": "v1", "title": "T", "artist": "A", "album": "Al",
             "duration": 100, "est_size": 1, "status": "pending",
             "path": None, "error": None},
        ],
    }
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    def _batch(entries, **kwargs):
        entries[0]["status"] = "downloaded"
        entries[0]["path"] = str(tmp_path / "A" / "Al" / "01 - T.m4a")
        kwargs["save_callback"](entries)
        return {
            "downloaded": 1, "archived": 0, "skipped_size": 0,
            "failed": 0, "stopped": False, "stop_reason": "completed",
        }

    with patch.object(artist_playlists, "run_batch_download", side_effect=_batch):
        result = artist_playlists.download_artist(
            "Test", tmp_path, str(tmp_path / "archive"),
            payload=payload, manifest_path=manifest,
        )

    assert result["manifest_path"] == str(manifest)
    saved = json.loads(manifest.read_text())
    assert saved["entries"][0]["status"] == "downloaded"


def test_resume_without_manifest_uses_newest_plan(tmp_path, monkeypatch, capsys):
    import os

    playlists = tmp_path / "playlists"
    playlists.mkdir()
    old = playlists / "old.manifest.json"
    new = playlists / "new.manifest.json"
    for path, video_id in ((old, "old"), (new, "new")):
        path.write_text(json.dumps({"entries": [
            {"video_id": video_id, "title": "T", "artist": "A",
             "album": "Al", "est_size": 1, "status": "pending"},
        ]}), encoding="utf-8")
    os.utime(old, (1, 1))
    os.utime(new, (2, 2))

    captured = {}

    def _batch(entries, **_kwargs):
        captured["ids"] = [e["video_id"] for e in entries]
        return {
            "downloaded": 0, "archived": 0, "skipped_size": 0,
            "failed": 0, "stopped": False, "stop_reason": "completed",
        }

    monkeypatch.setattr("packrat.cli.run_batch_download", _batch)
    args = cli.build_parser().parse_args(
        ["resume", "--output", str(tmp_path), "--yes"]
    )
    assert cli.cmd_download_manifest(args) == 0
    assert captured["ids"] == ["new"]
    assert "Using saved plan" in capsys.readouterr().out


def test_resume_without_plans_explains(tmp_path, capsys):
    args = cli.build_parser().parse_args(
        ["resume", "--output", str(tmp_path)]
    )
    assert cli.cmd_download_manifest(args) == 1
    assert "No saved plan found" in capsys.readouterr().err
