"""Headless progress rendering and download-one output contract."""

from __future__ import annotations

import io
import json

from packrat import cli, cli_progress


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


def test_console_progress_renders_gradient_bar_and_clears():
    stream = FakeTTY()
    progress = cli_progress.ConsoleProgress(
        stream, width=40, no_color=True, ascii_mode=True,
    )
    progress.start("Song", "Artist", total=2)
    progress.update(50, speed="1.0 MB/s", eta=3)
    progress.finish(["✓ done"])
    text = stream.getvalue()
    assert "Song" in text and "Artist" in text
    assert "50%" in text and "✓ done" in text
    assert "\x1b[38" not in text  # no colour escapes


def test_console_progress_is_silent_without_tty():
    stream = io.StringIO()
    progress = cli_progress.ConsoleProgress(stream)
    progress.start("Song", "Artist", total=1)
    progress.update(50)
    progress.finish(["✓ done"])
    text = stream.getvalue()
    assert "Song" in text and "✓ done" in text
    assert "%" not in text and "\r" not in text


def test_download_one_uses_progress_hooks_and_clean_summary(monkeypatch, tmp_path, capsys):
    from packrat.song_meta import SongMetadata

    downloaded = tmp_path / "A" / "Al" / "01 - Song.m4a"
    seen = {}

    class FakeYDL:
        def __init__(self, opts):
            seen["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def extract_info(self, _url, download=True):
            downloaded.parent.mkdir(parents=True, exist_ok=True)
            downloaded.write_bytes(b"x")
            seen["opts"]["progress_hooks"][0]({
                "status": "downloading", "downloaded_bytes": 50,
                "total_bytes": 100, "speed": 1024 * 1024, "eta": 2,
            })
            return {"filepath": str(downloaded), "duration": 200,
                    "acodec": "mp4a.40.2", "abr": 129}

    monkeypatch.setattr(cli, "resolve_song_metadata", lambda *_a, **_k: SongMetadata(
        video_id="abcdefghijk", title="Song", artist="A", album="Al", track_num=1,
    ))
    monkeypatch.setattr(cli, "_check_deno", lambda: (True, "2.9"))
    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYDL)
    monkeypatch.setattr(cli, "_final_filepath", lambda *_a: downloaded)
    monkeypatch.setattr(cli, "apply_song_metadata", lambda *_a, **_k: True)
    monkeypatch.setattr(cli, "verify_audio", lambda *_a, **_k: type(
        "C", (), {"ok": True, "reason": "", "duration": 200, "size": 1}
    )())
    args = cli.build_parser().parse_args([
        "download-one", "abcdefghijk", "--output", str(tmp_path),
    ])
    assert cli.cmd_download_one(args) == 0
    assert seen["opts"]["progress"] is False
    assert seen["opts"]["progress_hooks"]
    out = capsys.readouterr().out
    assert "ffprobe:" not in out
    assert "mutagen:" not in out
    assert "downloaded:" not in out
    assert "✓" in out
    assert str(downloaded) in out


def test_batch_creates_console_progress_when_no_callback(monkeypatch, tmp_path):
    from packrat import batch, cli_progress

    events = []

    class FakeProgress:
        def __call__(self, event):
            events.append(event["type"])

    monkeypatch.setattr(cli_progress, "console_progress", lambda: FakeProgress())
    monkeypatch.setattr(batch, "download_track", lambda *_a, **_k: type(
        "R", (), {"status": "downloaded", "error": None, "retryable": False,
                  "filepath": tmp_path / "x.m4a"}
    )())
    entry = {"video_id": "v1", "title": "T", "artist": "A", "album": "Al",
             "duration": 10, "status": "pending"}
    batch.run_batch_download([entry], tmp_path, delay=0)
    assert "start" in events
    assert "done" in events


def test_progress_line_fits_eighty_columns():
    stream = FakeTTY()
    progress = cli_progress.ConsoleProgress(
        stream, width=80, no_color=True, ascii_mode=True,
    )
    progress.start("A very long song title that exceeds the limit", "Artist", total=12)
    progress.done = 11
    progress.update(72, speed=1.2 * 1024 * 1024, eta=12)
    line = stream.getvalue().split("\r")[-1].replace("\x1b[K", "")
    assert len(line) <= 80


def test_progress_shows_waiting_and_retry():
    stream = FakeTTY()
    progress = cli_progress.ConsoleProgress(
        stream, width=80, no_color=True, ascii_mode=True,
    )
    progress.start(total=2)
    progress({"type": "track_start", "payload": {"title": "Song", "stage": "resolving"}})
    progress({"type": "delay", "payload": {"remaining": 6.2, "next": "Next"}})
    progress({"type": "track_progress", "payload": {"stage": "retry 1/3"}})
    text = stream.getvalue()
    assert "resolving" in text
    assert "waiting 6.2s" in text and "Next" in text
    assert "retry 1/3" in text


def test_progress_formats_numeric_batch_speed():
    stream = FakeTTY()
    progress = cli_progress.ConsoleProgress(
        stream, width=80, no_color=True, ascii_mode=True,
    )
    progress.start(total=1)
    progress({"type": "track_progress", "payload": {
        "percent": 10, "speed": 2 * 1024 * 1024,
    }})
    assert "2.0 MB/s" in stream.getvalue()


def test_download_one_does_not_archive_before_verification(monkeypatch, tmp_path):
    from packrat.song_meta import SongMetadata

    downloaded = tmp_path / "A" / "Al" / "01 - Song.m4a"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_bytes(b"x")
    archived = []

    class FakeArchive:
        def __init__(self, *_a, **_k):
            pass

        def add(self, video_id):
            archived.append(video_id)

        def save(self):
            pass

    class FakeYDL:
        def __init__(self, _opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def extract_info(self, _url, download=True):
            downloaded.write_bytes(b"x")
            return {"filepath": str(downloaded), "duration": 200}

    monkeypatch.setattr(cli, "resolve_song_metadata", lambda *_a, **_k: SongMetadata(
        video_id="abcdefghijk", title="Song", artist="A", album="Al", track_num=1,
    ))
    monkeypatch.setattr(cli, "_check_deno", lambda: (True, "2.9"))
    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYDL)
    monkeypatch.setattr(cli, "_final_filepath", lambda *_a: downloaded)
    monkeypatch.setattr(cli, "apply_song_metadata", lambda *_a, **_k: True)
    monkeypatch.setattr(cli, "verify_audio", lambda *_a, **_k: type(
        "C", (), {"ok": False, "reason": "corrupt", "duration": None, "size": 0}
    )())
    monkeypatch.setattr(cli, "Archive", FakeArchive)
    args = cli.build_parser().parse_args([
        "download-one", "abcdefghijk", "--output", str(tmp_path),
    ])
    assert cli.cmd_download_one(args) == 1
    assert archived == []
