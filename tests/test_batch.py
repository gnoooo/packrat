"""Focused batch tests: callback order and stop rule."""

from __future__ import annotations

import pathlib

from packrat.batch import run_batch_download
from packrat.download import DownloadResult


def _entries(n=3):
    return [
        {"video_id": f"v{i}", "title": f"T{i}", "artist": "A", "album": "Al",
         "duration": 10, "status": "pending"}
        for i in range(n)
    ]


def test_save_callback_called_after_each_track(tmp_path, monkeypatch):
    seen = []

    def _download(track, *_a, **_kw):
        return DownloadResult(status="downloaded", filepath=tmp_path / f"{track['video_id']}.m4a")

    monkeypatch.setattr("packrat.batch.download_track", _download)
    entries = _entries(3)
    run_batch_download(
        entries, output_root=tmp_path, delay=0, archive_path=None,
        save_callback=lambda current: seen.append(
            [e["status"] for e in entries]
        ),
    )
    assert len(seen) == 1
    assert seen[-1] == ["downloaded", "downloaded", "downloaded"]


def test_stop_after_three_consecutive_retryable_errors(tmp_path, monkeypatch):
    def _download(*_a, **_kw):
        return DownloadResult(status="failed", error="403", retryable=True)

    monkeypatch.setattr("packrat.batch.download_track", _download)
    summary = run_batch_download(
        _entries(5), output_root=tmp_path, delay=0, archive_path=None,
    )
    assert summary["failed"] == 3
    assert summary["stopped"] is True
    assert summary["stop_reason"] == "consecutive_errors"


def test_downloaded_entry_clears_a_stale_error(tmp_path, monkeypatch):
    entry = {
        "video_id": "v1", "title": "T", "artist": "A", "album": "Al",
        "duration": 10, "status": "failed", "error": "old failure",
    }

    def _download(*_a, **_kw):
        return DownloadResult(
            status="downloaded", filepath=tmp_path / "v1.m4a"
        )

    monkeypatch.setattr("packrat.batch.download_track", _download)
    run_batch_download(
        [entry], output_root=tmp_path, delay=0, archive_path=None,
    )
    assert entry["status"] == "downloaded"
    assert entry["error"] is None


def test_archived_entry_is_skipped_and_marked(tmp_path, monkeypatch):
    called = []

    def _download(*_a, **_kw):
        called.append(True)
        raise AssertionError("archived tracks must not be downloaded")

    class _Archive:
        def __contains__(self, video_id):
            return video_id == "v1"

        def add(self, _video_id):
            pass

        def save(self):
            pass

    monkeypatch.setattr("packrat.batch.download_track", _download)
    expected = tmp_path / "A" / "Al" / "T.m4a"
    expected.parent.mkdir(parents=True)
    expected.write_bytes(b"valid")
    monkeypatch.setattr("packrat.batch.verify_audio", lambda *_a, **_k: type(
        "C", (), {"ok": True, "size": 5}
    )())
    entry = {
        "video_id": "v1", "title": "T", "artist": "A", "album": "Al",
        "duration": 10, "status": "pending",
    }
    summary = run_batch_download(
        [entry], output_root=tmp_path, delay=0, archive=_Archive(),
    )
    assert called == []
    assert entry["status"] == "archived"
    assert entry["path"] == str(expected)
    assert summary["archived"] == 1


def test_archive_without_file_redownloads(tmp_path, monkeypatch):
    called = []

    def _download(*_a, **_kw):
        called.append(True)
        return type("R", (), {
            "status": "downloaded",
            "error": None,
            "filepath": tmp_path / "A" / "Al" / "T.m4a",
            "retryable": False,
        })()

    class _Archive:
        def __contains__(self, video_id):
            return video_id == "v1"

        def add(self, _video_id):
            pass

        def save(self):
            pass

    monkeypatch.setattr("packrat.batch.download_track", _download)
    entry = {
        "video_id": "v1", "title": "T", "artist": "A", "album": "Al",
        "duration": 10, "status": "pending",
    }
    summary = run_batch_download(
        [entry], output_root=tmp_path, delay=0, archive=_Archive(),
    )
    assert called == [True]
    assert entry["status"] == "downloaded"
    assert summary["archived"] == 0
