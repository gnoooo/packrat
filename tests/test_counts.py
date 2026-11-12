"""Tests for the count source of truth (disk owned keys, summary, runs)."""

from __future__ import annotations

import json
import pathlib

from packrat import artists, runs


def test_disk_owned_keys_parses_album_and_single_files(tmp_path: pathlib.Path):
    (tmp_path / "BABYMETAL" / "METAL RESISTANCE").mkdir(parents=True)
    (tmp_path / "BABYMETAL" / "Singles").mkdir(parents=True)
    (tmp_path / "BABYMETAL" / "METAL RESISTANCE" / "04 - GALAXY.m4a").write_text("a")
    (tmp_path / "BABYMETAL" / "Singles" / "Head_Bang!!.m4a").write_text("a")
    keys = artists.disk_owned_keys(tmp_path)
    assert artists._normalize("BABYMETAL"), True  # sanity
    assert (artists._normalize("BABYMETAL"), artists._normalize("GALAXY")) in keys
    assert (artists._normalize("BABYMETAL"), artists._normalize("Head Bang!!")) in keys


def test_disk_owned_keys_empty_root(tmp_path: pathlib.Path):
    assert artists.disk_owned_keys(tmp_path) == set()


def test_summarize_entries_duration_model():
    entries = [
        {"est_size": 1000, "duration": 120},
        {"est_size": 2000, "duration": 180},
    ]
    s = artists.summarize_entries(entries, delay=10.0)
    assert s["new_tracks"] == 2
    assert s["est_bytes"] == 3000
    assert s["est_time_seconds"] == 2 * 10 + 300
    assert s["est_source"] == "duration-model"
    assert s["measured_s_per_track"] is None


def test_summarize_entries_measured_overrides():
    s = artists.summarize_entries(
        [{"est_size": 1, "duration": 120}, {"est_size": 1, "duration": 180}],
        delay=10.0,
        measured=25.0,
    )
    assert s["est_time_seconds"] == 50
    assert s["est_source"] == "measured"
    assert s["measured_s_per_track"] == 25.0


def test_runs_record_and_read_back(tmp_path: pathlib.Path):
    runs.record_run(tmp_path, mode="artist:X", tracks=3, downloaded=2, seconds=40.0)
    assert runs.RUNS_PATH.exists()
    data = json.loads(runs.RUNS_PATH.read_text().splitlines()[-1])
    assert data["downloaded"] == 2 and data["seconds"] == 40.0
    # 40 s / 2 downloads = 20 s/track
    assert artists.runs.last_measured(tmp_path) == 20.0


def test_batch_appends_runs_real_batches(tmp_path, monkeypatch):
    # A real batch (artist or download-manifest mode) appends to runs.jsonl
    # at the end so future catalogs can estimate time from throughput.
    from packrat import batch
    from packrat.download import DownloadResult

    monkeypatch.setattr(
        batch,
        "download_track",
        lambda *a, **k: DownloadResult(
            status="downloaded", filepath=tmp_path / "a.m4a", error=None
        ),
    )
    entries = [
        {"video_id": "v1", "title": "T1", "artist": "A", "duration": 120},
        {"video_id": "v2", "title": "T2", "artist": "A", "duration": 120},
        {"video_id": "v3", "title": "T3", "artist": "A", "duration": 120},
    ]
    batch.run_batch_download(
        entries, output_root=tmp_path, mode="artist:Banda", delay=0.0
    )
    lines = runs.RUNS_PATH.read_text().splitlines()
    data = json.loads(lines[-1])
    assert data["mode"] == "artist:Banda" and data["tracks"] == 3
    assert data["downloaded"] == 3


def test_runs_last_measured_skips_empty_and_broken(tmp_path: pathlib.Path, monkeypatch):
    p = runs.RUNS_PATH
    p.parent.mkdir(parents=True)
    p.write_text(
        json.dumps({"ts": 1, "mode": "m", "tracks": 1, "downloaded": 0, "seconds": 9}) + "\n"
        + "not json\n"
        + json.dumps({"ts": 3, "mode": "m", "tracks": 2, "downloaded": 2, "seconds": 30}) + "\n"
    )
    assert runs.last_measured(tmp_path) == 15.0
    # Empty/missing config state -> None.
    monkeypatch.setattr(runs, "RUNS_PATH", tmp_path / "missing-runs.jsonl")
    assert runs.last_measured(tmp_path / "nowhere") is None
