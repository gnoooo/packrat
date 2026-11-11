"""Focused batch tests: callback order and stop rule."""

from __future__ import annotations

import pathlib

from ytm_offline.batch import run_batch_download
from ytm_offline.download import DownloadResult


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

    monkeypatch.setattr("ytm_offline.batch.download_track", _download)
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

    monkeypatch.setattr("ytm_offline.batch.download_track", _download)
    summary = run_batch_download(
        _entries(5), output_root=tmp_path, delay=0, archive_path=None,
    )
    assert summary["failed"] == 3
    assert summary["stopped"] is True
    assert summary["stop_reason"] == "consecutive_errors"
