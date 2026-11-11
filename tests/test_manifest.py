"""Characterization tests for Manifest read/write and schema versioning."""

from __future__ import annotations

import json
import pathlib

import pytest

from packrat.manifest import Manifest, SCHEMA_VERSION


def test_manifest_writes_version(tmp_path: pathlib.Path) -> None:
    manifest = Manifest()
    manifest.add("vid1", "Title", "Artist", "Album", 1234, "pending")
    path = tmp_path / "test.manifest.json"
    manifest.write(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == SCHEMA_VERSION
    assert data["total_est_size"] == 1234


def test_manifest_loads_old_version_as_v1(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "old.manifest.json"
    path.write_text(
        json.dumps({"entries": [{"video_id": "v1", "status": "pending"}]}),
        encoding="utf-8",
    )
    manifest = Manifest.load(path)
    assert manifest.version == 1
    assert manifest.entries == [{"video_id": "v1", "status": "pending"}]


def test_manifest_load_rewrites_unknown_status(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "bad.manifest.json"
    path.write_text(
        json.dumps(
            {"entries": [{"video_id": "v2", "status": "totally_bogus"}]}
        ),
        encoding="utf-8",
    )
    manifest = Manifest.load(path)
    assert manifest.entries[0]["status"] == "failed"
    assert "totally_bogus" in manifest.entries[0]["error"]


def test_manifest_load_rejects_future_version(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "future.manifest.json"
    path.write_text(
        json.dumps({"version": 99, "entries": []}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        Manifest.load(path)
