"""Stable run reports for TTY, cron and non-TTY output."""

from __future__ import annotations

from packrat import cli


def test_run_report_lists_failures_and_stop_reason(capsys):
    result = {
        "downloaded": 3,
        "archived": 1,
        "skipped_size": 0,
        "skipped": 1,
        "failed": 2,
        "stopped": True,
        "stop_reason": "consecutive_errors",
        "failure_details": [
            {"title": "Bad Song", "error": "HTTP Error 403"},
            {"title": "Other", "error": "timed out"},
        ],
    }
    cli.print_result_summary(result)
    out = capsys.readouterr().out
    assert "downloaded 3" in out
    assert "failed 2" in out
    assert "3 consecutive retryable errors" in out
    assert "Bad Song: HTTP Error 403" in out
    assert "download-manifest --failed-only" in out
    assert "stopped False" not in out


def test_run_report_clean_is_one_line(capsys):
    cli.print_result_summary({
        "downloaded": 2, "archived": 0, "skipped_size": 0,
        "failed": 0, "stopped": False,
    })
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert lines[0] == "downloaded 2 · archived 0 · skipped-size 0 · failed 0"


def test_auth_failure_points_to_packrat_login(monkeypatch, capsys):
    class FakeYT:
        def get_library_playlists(self, limit=1):
            raise RuntimeError("expired")

        def get_liked_songs(self, limit=1):
            raise RuntimeError("expired")

    monkeypatch.setattr(cli, "load_ytmusic", lambda _path: FakeYT())
    args = cli.build_parser().parse_args(["auth", "--headers", "/tmp/custom.json"])
    assert cli.cmd_auth(args) == 1
    err = capsys.readouterr().err
    assert "packrat login --file /tmp/custom.json" in err
    assert "ytmusicapi browser" not in err


def test_sync_prints_headless_failure_report(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli.sync_module, "run_sync", lambda _args: {
        "downloaded": 1, "archived": 0, "skipped_size": 0, "failed": 1,
        "stopped": False, "stop_reason": "completed",
        "failure_details": [{"title": "Bad", "error": "HTTP Error 403"}],
    })
    args = cli.build_parser().parse_args([
        "sync", "--output", str(tmp_path), "--archive", str(tmp_path / "arc"),
        "--force-new-archive", "--yes",
    ])
    assert cli.cmd_sync(args) == 1
    out = capsys.readouterr().out
    assert "failed 1" in out
    assert "Bad: HTTP Error 403" in out
