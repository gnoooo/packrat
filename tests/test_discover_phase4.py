"""Focused tests for phase-4 scoring and helpers."""

from __future__ import annotations

import json
import pathlib

from ytm_offline import discover_phase4 as p4


def test_official_music_filter():
    assert p4._is_official_music({"videoType": "MUSIC_VIDEO_TYPE_ATV"})
    assert not p4._is_official_music({"videoType": "MUSIC_VIDEO_TYPE_UGC"})


def test_artist_id_map_and_collaborators():
    tracks = [
        {"artists": [{"name": "A", "id": "a1"}]},
        {"artists": [{"name": "A", "id": "a1"}, {"name": "B", "id": "b1"}]},
    ]
    assert p4._artist_id_map(tracks) == {"A": "a1"}
    assert p4._collaborator_ids(tracks, {"A"}) == {"B": "b1"}


def test_score_components_penalizes_phase3_bubble():
    profile = {"artist_ids": {"A": "a1"}, "tags": [{"name": "rock"}]}
    track = {"video_id": "v1", "artist": "A", "title": "T"}
    comps = p4._score_components(track, profile, {"v1"}, {"v1": {"rock"}})
    assert comps["profile_artist"] == 1
    assert comps["tag_overlap"] == 1
    assert comps["bubble_penalty"] == -1


def test_load_manifest_ids(tmp_path: pathlib.Path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"entries": [
        {"video_id": "v1"}, {"video_id": "v2"}, {"video_id": None},
    ]}), encoding="utf-8")
    assert p4._load_manifest_ids(path) == {"v1", "v2"}
