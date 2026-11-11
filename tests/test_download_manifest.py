"""Tests for the download-manifest command."""

from __future__ import annotations

import json
import pathlib
from unittest.mock import patch

import pytest

from ytm_offline import cli


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
    assert "manifest not found" in capsys.readouterr().err


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
    assert "not downloading" in capsys.readouterr().out


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
        assert kwargs.get("archive_path") == ".ytm_offline_archive"
        assert kwargs.get("mode") == "manifest"
        return {
            "downloaded": 1,
            "archived": 0,
            "skipped_size": 0,
            "failed": 0,
            "stopped": False,
        }

    args = _args(tmp_path, manifest, yes=True)
    with patch("ytm_offline.cli.run_batch_download", side_effect=fake_run):
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
        "entries": [],
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
    with patch("ytm_offline.cli.artists_module.download_artist", side_effect=fake_download_artist):
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
        json.dumps({"command": "artist", "label": "Test", "entries": []}),
        encoding="utf-8",
    )

    args = _args(tmp_path, manifest, yes=True)
    with patch(
        "ytm_offline.cli.artists_module.download_artist",
        return_value={"error": "no pending tracks"},
    ):
        assert cli.cmd_download_manifest(args) == 1
    assert "no pending tracks" in capsys.readouterr().err
