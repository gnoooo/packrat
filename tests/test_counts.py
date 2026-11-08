"""Tests for the count source of truth (disk owned keys, summary, runs)."""

from __future__ import annotations

import json
import pathlib

from ytm_offline import artists, runs


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
    paths = [tmp_path / "playlists" / "runs.jsonl"]
    assert paths[0].exists()
    data = json.loads(paths[0].read_text().splitlines()[-1])
    assert data["downloaded"] == 2 and data["seconds"] == 40.0
    # 40 s / 2 downloads = 20 s/track
    assert artists.runs.last_measured(tmp_path) == 20.0


def test_runs_last_measured_skips_empty_and_broken(tmp_path: pathlib.Path):
    p = tmp_path / "playlists" / "runs.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text(
        json.dumps({"ts": 1, "mode": "m", "tracks": 1, "downloaded": 0, "seconds": 9}) + "\n"
        + "not json\n"
        + json.dumps({"ts": 3, "mode": "m", "tracks": 2, "downloaded": 2, "seconds": 30}) + "\n"
    )
    assert runs.last_measured(tmp_path) == 15.0
    # Empty file / missing file -> None.
    assert runs.last_measured(tmp_path / "nowhere") is None
