"""Tests for the interactive release picker (grammar + selection apply)."""

from __future__ import annotations

import pytest

from ytm_offline import artists


def test_parse_selection_grammar():
    assert artists.parse_selection("A[1-3],S[2],T") == [
        ("A", [1, 2, 3]), ("S", [2]), ("T", None)]
    assert artists.parse_selection("a2,r[1]") == [("A", [2]), ("R", [1])]
    assert artists.parse_selection("D[1,3,5]") == [("D", [1, 3, 5])]


def test_parse_selection_rejects_garbage():
    for bad in ("Z[1]", "A[3-1", "A[x]", "3", "TQ"):
        with pytest.raises(ValueError):
            artists.parse_selection(bad)


def _fake_catalog():
    payload = {
        "artist": "Artist",
        "entries": [
            {
                "video_id": "t1", "title": "Big Song", "artist": "Artist",
                "duration": 200, "est_size": 5000, "release_type": "top",
                "release_year": 9999, "release_row": None,
            },
            {
                "video_id": "a1", "title": "Album Track", "artist": "Artist",
                "duration": 180, "est_size": 4000, "release_type": "album",
                "release_year": 2020, "release_row": 0,
            },
            {
                "video_id": "a2", "title": "Album Track 2", "artist": "Artist",
                "duration": 190, "est_size": 4000, "release_type": "album",
                "release_year": 2020, "release_row": 0,
            },
        ],
        "releases": [
            {
                "row": 0, "title": "Album", "year": 2020, "type": "album",
                "browse_id": "MPREb_a", "status": "kept", "tracks": 2,
                "new_tracks": 2,
            },
            {
                "row": 1, "title": "Old Live", "year": 2019, "type": "album",
                "browse_id": "MPREb_l", "status": "excluded", "reason": "live",
            },
        ],
        "totals": {},
    }
    return {
        "manifest_path": "x.manifest.json",
        "payload": payload,
        "artist": "Artist",
        "subscribers": "1M",
        "label": "Artist",
        "totals": {"releases_kept": 1, "excluded_counts": {"live": 1}},
        "entries": payload["entries"],
    }


def _sections(catalog):
    rows = artists.build_sections(catalog)
    assert [r["kind"] for r in rows["T"]] == ["track"]
    assert len(rows["A"]) == 1 and len(rows["L"]) == 1
    return rows


def test_build_sections_and_rows(tmp_path):
    catalog = _fake_catalog()
    rows = _sections(catalog)
    picked = artists.chosen_rows(rows, [("A", [1]), ("T", [1])])
    kinds = [r["kind"] for r in picked]
    assert kinds == ["release", "track"]


def test_apply_selection_keeps_subset(tmp_path):
    catalog = _fake_catalog()
    rows = _sections(catalog)
    out = artists.apply_selection(
        catalog, rows, [("A", [1])],
        headers_path="/nonexistent-should-not-be-needed-when-no-skipped-selected",
        output_root=tmp_path,
        archive_path=tmp_path / "archive",
    )
    assert out is not None
    assert [e["video_id"] for e in out["entries"]] == ["a1", "a2"]
    assert out["totals"]["new_tracks"] == 2
    assert out["payload"]["entries"] == out["entries"]
    assert out["payload"]["releases"][0]["selected"] is True
    assert out["payload"]["releases"][1]["selected"] is False


def test_apply_selection_deselects_all(tmp_path):
    catalog = _fake_catalog()
    rows = _sections(catalog)
    assert artists.apply_selection(
        catalog, rows, [("T", [9])], headers_path="/nope",
        output_root=tmp_path, archive_path=tmp_path / "archive",
    ) is None


def test_print_sections_smoke(capsys):
    catalog = _fake_catalog()
    rows = artists.build_sections(catalog)
    artists.print_sections(rows, width=100)
    out = capsys.readouterr().out
    assert "A. albums (1)" in out
    assert "T. top songs (1)" in out
    assert "L. live (skipped) (1)" in out
    assert "Duplicate" not in out  # reason text comes from meta, none here
