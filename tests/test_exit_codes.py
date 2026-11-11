"""Exit-code semantics for worker results (item 1)."""

from __future__ import annotations

from unittest.mock import patch

from packrat import cli
from packrat.batch import exit_code_from_result, run_batch_download
from packrat.download import DownloadResult


def test_none_is_success():
    assert exit_code_from_result(None) == 0


def test_int_passthrough():
    assert exit_code_from_result(0) == 0
    assert exit_code_from_result(1) == 1
    assert exit_code_from_result(2) == 2


def test_dict_success_batch_shape():
    assert exit_code_from_result(
        {"downloaded": 3, "failed": 0, "stopped": False, "stop_reason": "completed"}
    ) == 0


def test_dict_failures():
    assert exit_code_from_result(
        {"downloaded": 1, "failed": 2, "stopped": False, "stop_reason": "completed"}
    ) == 1


def test_user_quit_clean_is_success():
    assert exit_code_from_result(
        {"downloaded": 2, "failed": 0, "stopped": True, "stop_reason": "user"}
    ) == 0


def test_user_quit_with_failures():
    assert exit_code_from_result(
        {"downloaded": 1, "failed": 1, "stopped": True, "stop_reason": "user"}
    ) == 1


def test_abort_by_stop_rule():
    assert exit_code_from_result(
        {"downloaded": 0, "failed": 3, "stopped": True,
         "stop_reason": "consecutive_errors"}
    ) == 1


def test_error_key_is_failure():
    assert exit_code_from_result({"error": "manifest not found"}) == 1


def test_nested_summary():
    assert exit_code_from_result(
        {"summary": {"downloaded": 4, "failed": 0, "stopped": False},
         "manifest_path": "x.json"}
    ) == 0


def test_real_run_aborts_after_three_consecutive_errors(tmp_path):
    """3 retryable failures in a row stop the batch; exit code is 1."""
    entries = [
        {"video_id": f"v{i}", "title": f"T{i}", "artist": "A", "album": "Al",
         "status": "pending"}
        for i in range(4)
    ]
    fail = DownloadResult(status="failed", error="403", retryable=True)
    with patch("packrat.batch.download_track", return_value=fail):
        summary = run_batch_download(
            entries, output_root=tmp_path, delay=0,
            archive_path=None, mode="test",
        )
    assert summary["failed"] == 3
    assert summary["stopped"] is True
    assert summary["stop_reason"] == "consecutive_errors"
    assert exit_code_from_result(summary) == 1


def test_real_run_clean_user_stop_is_success(tmp_path):
    """User quit before the first track, nothing failed -> exit 0."""
    entries = [
        {"video_id": "v1", "title": "T", "artist": "A", "album": "Al",
         "status": "pending"}
    ]
    ok = DownloadResult(status="downloaded", filepath=tmp_path / "f.m4a")
    with patch("packrat.batch.download_track", return_value=ok):
        with patch("packrat.control.Control") as ctrl:
            ctrl.return_value.should_stop.return_value = True
            summary = run_batch_download(
                entries, output_root=tmp_path, delay=0,
                archive_path=None, mode="test", control=ctrl,
            )
    assert summary["downloaded"] == 0
    assert summary["failed"] == 0
    assert summary["stopped"] is True
    assert summary["stop_reason"] == "user"
    assert exit_code_from_result(summary) == 0


def test_cli_manifest_exit_zero_on_clean_quit(
    tmp_path, monkeypatch, capsys
):
    """run_tui's dict no longer leaks to sys.exit; clean q -> 0."""
    import json
    import pathlib

    manifest = tmp_path / "playlists" / "queues.manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"entries": [{"video_id": "v1", "status": "pending"}]}),
        encoding="utf-8",
    )
    argv = [
        "download-manifest", "--manifest", str(manifest),
        "--yes", "--tui",
    ]
    args = cli.build_parser().parse_args(argv)
    batch_result = {
        "total": 1, "downloaded": 0, "archived": 0, "skipped_size": 0,
        "failed": 0, "skipped": 0, "failures": [],
        "stopped": True, "stop_reason": "user",
    }
    with patch("packrat.tui.run_tui", return_value=batch_result):
        assert cli.cmd_download_manifest(args) == 0
    out = capsys.readouterr().out
    assert "{" not in out  # no raw dict printed
    assert "downloaded 0" in out


def test_cli_manifest_exit_one_on_failures(
    tmp_path, capsys
):
    import json

    manifest = tmp_path / "playlists" / "queues.manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"entries": [{"video_id": "v1", "status": "pending"}]}),
        encoding="utf-8",
    )
    argv = ["download-manifest", "--manifest", str(manifest), "--yes", "--tui"]
    args = cli.build_parser().parse_args(argv)
    batch_result = {
        "total": 1, "downloaded": 0, "archived": 0, "skipped_size": 0,
        "failed": 1, "skipped": 0, "failures": ["v1"],
        "stopped": False, "stop_reason": "completed",
    }
    with patch("packrat.tui.run_tui", return_value=batch_result):
        assert cli.cmd_download_manifest(args) == 1
    out = capsys.readouterr().out
    assert "{" not in out
    assert "failed 1" in out
