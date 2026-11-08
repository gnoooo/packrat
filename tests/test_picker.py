"""Tests for the A/S release picker (grammar, sections, selection)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ytm_offline import artists


def test_parse_selection_grammar():
    assert artists.parse_selection("A[1-3],S[2]") == [
        {"letter": "A", "spec": [(1, 3)], "exclude": False},
        {"letter": "S", "spec": [(2, 2)], "exclude": False},
    ]
    assert artists.parse_selection("A[3-]") == [
        {"letter": "A", "spec": [(3, None)], "exclude": False},
    ]
    assert artists.parse_selection("A[1,3]") == [
        {"letter": "A", "spec": [(1, 1), (3, 3)], "exclude": False},
    ]
    assert artists.parse_selection("-S[4]") == [
        {"letter": "S", "spec": [(4, 4)], "exclude": True},
    ]
    assert artists.parse_selection("s") == [
        {"letter": "S", "spec": None, "exclude": False},
    ]
    assert artists.parse_selection("all") == [
        {"letter": "A", "spec": None, "exclude": False},
        {"letter": "S", "spec": None, "exclude": False},
    ]


def test_parse_selection_rejects_unknown_sections():
    for bad in ("T", "T[1]", "L", "R", "D", "X[2]"):
        with pytest.raises(ValueError) as exc:
            artists.parse_selection(bad)
        letter = bad[0].upper()
        assert str(exc.value) == f"unknown section '{letter}' (valid: A, S)"
    with pytest.raises(ValueError):
        artists.parse_selection("")


def _meta(
    row: int,
    title: str,
    *,
    year: str = "2020",
    mtype: str = "album",
    section: str = "A",
    status: str = "kept",
    tracks: int = 10,
    new: int = 3,
    tags: list[str] | None = None,
    bid: str | None = None,
    reason: str | None = None,
) -> dict:
    meta = {
        "row": row,
        "title": title,
        "year": year,
        "type": mtype,
        "section": section,
        "browse_id": bid or f"MPREb_{row}",
        "status": status,
    }
    if status == "kept":
        meta["tracks"] = tracks
        meta["new_tracks"] = new
    else:
        meta["new_tracks"] = new
        meta["tracks"] = tracks
        if reason:
            meta["reason"] = reason
    if tags:
        meta["tags"] = tags
    return meta


def _catalog(releases: list[dict], entries: list[dict] | None = None) -> dict:
    entries = entries or []
    payload = {"artist": "Artist", "entries": entries, "releases": releases, "totals": {}}
    return {
        "manifest_path": "x.manifest.json",
        "payload": payload,
        "artist": "Artist",
        "subscribers": "",
        "label": "Artist",
        "totals": {},
        "entries": entries,
        "_resolve_cache": {},
    }


def _sections(catalog: dict) -> dict:
    rows = artists.build_sections(catalog)
    assert set(rows) == {"A", "S"}
    return rows


def test_section_assignment_from_releases():
    releases = [
        _meta(0, "An Album", mtype="album", section="A", new=2),
        _meta(1, "A Single", mtype="single", section="S", new=1),
        _meta(2, "An EP", mtype="ep", section="S", new=1, tags=["live"], status="excluded"),
        _meta(3, "Old Live Boot", mtype="album", section="A", status="excluded", new=0,
              tags=["live"], reason="live"),
        _meta(4, "Remix Single", mtype="single", section="S", status="excluded", new=0,
              tags=["remix"], reason="remix"),
        _meta(5, "An Album (Deluxe)", mtype="album", section="A", status="excluded", new=0,
              tags=["dup of An Album"], reason="dup of An Album"),
    ]
    catalog = _catalog(releases)
    rows = _sections(catalog)
    a_titles = [r["meta"]["title"] for r in rows["A"]]
    s_titles = [r["meta"]["title"] for r in rows["S"]]
    assert a_titles == ["An Album", "An Album (Deluxe)", "Old Live Boot"]
    assert s_titles == ["A Single", "An EP", "Remix Single"]
    # New releases numbered from 1 per section, newest year first.
    assert [r["n"] for r in rows["A"]] == [1, 2, 3]
    assert [r["n"] for r in rows["S"]] == [1, 2, 3]


def test_section_numbering_newest_first_then_title():
    releases = [
        _meta(0, "Beta", year="2019"),
        _meta(1, "Alpha", year="2019"),
        _meta(2, "Gamma", year="2024"),
    ]
    rows = artists.build_sections(_catalog(releases))
    titles = [r["meta"]["title"] for r in rows["A"]]
    assert titles == ["Gamma", "Alpha", "Beta"]
    assert [r["n"] for r in rows["A"]] == [1, 2, 3]


def test_section_membership_first_section_wins(monkeypatch):
    yt = MagicMock()
    rel = {"title": "Dual", "year": "2020", "browseId": "MPREb_dual"}
    yt.get_artist.return_value = {
        "name": "Artist",
        "subscribers": "1M",
        "albums": {"results": [rel]},
        "singles": {"browseId": "MPAD", "params": "p",
                    "results": [{**rel, "type": "Single"}]},
    }
    yt.get_artist_albums.return_value = [{**rel, "type": "Single"}]
    disc = artists.get_discography(yt, "UCartist")
    rows = [r for r in disc["releases"] if r["browseId"] == "MPREb_dual"]
    assert len(rows) == 1
    assert rows[0]["_section"] == "albums"


def test_default_selection_skips_tagged_and_zero_new():
    releases = [
        _meta(0, "Good Album", new=2),
        _meta(1, "Nothing New", new=0),
        _meta(2, "Tagged Single", mtype="single", section="S", status="excluded",
              new=1, tags=["live"], reason="live"),
        _meta(3, "Live Boot", section="A", status="excluded", new=0, tags=["live"]),
    ]
    rows = artists.build_sections(_catalog(releases))
    assert rows["A"][0]["selected"] is True   # Good Album
    assert rows["A"][1]["selected"] is False  # 0 new
    assert rows["S"][0]["selected"] is False  # tagged
    assert rows["A"][2]["selected"] is False  # tagged


def test_all_rule_includes_tagged_and_minus_excludes():
    releases = [
        _meta(0, "Good Album", new=2),
        _meta(1, "Nothing New", new=0),
        _meta(2, "Tagged Single", mtype="single", section="S", status="excluded",
              new=0, tags=["live"]),
    ]
    catalog = _catalog(releases)
    rows = _sections(catalog)
    picked = artists.chosen_rows(rows, artists.parse_selection("all"))
    assert [r["meta"]["title"] for r in picked] == [
        "Good Album", "Nothing New", "Tagged Single",
    ]
    # all minus one: default zeros out and the tagged single is removable.
    picked = artists.chosen_rows(rows, artists.parse_selection("A[*],S[*],-S[1]"))
    assert [r["meta"]["title"] for r in picked] == ["Good Album", "Nothing New"]
    # Out-of-range silently dropped.
    picked = artists.chosen_rows(rows, artists.parse_selection("A[1-9]"))
    assert [r["meta"]["title"] for r in picked] == ["Good Album", "Nothing New"]


def _kept_entries() -> list[dict]:
    return [
        {"video_id": "a1", "title": "T1", "artist": "Artist", "duration": 100,
         "release_row": 0, "release_type": "album"},
        {"video_id": "a2", "title": "T2", "artist": "Artist", "duration": 100,
         "release_row": 0, "release_type": "album"},
        {"video_id": "s1", "title": "S1", "artist": "Artist", "duration": 100,
         "release_row": 1, "release_type": "single"},
    ]


def _kept_fixture() -> tuple[dict, dict]:
    releases = [
        _meta(0, "An Album", new=2),
        _meta(1, "A Single", mtype="single", section="S", new=1),
    ]
    catalog = _catalog(releases, _kept_entries())
    return catalog, _sections(catalog)


def test_apply_selection_keeps_subset(tmp_path):
    catalog, rows = _kept_fixture()
    out = artists.apply_selection(
        catalog, rows, artists.parse_selection("A[1]"),
        headers_path="/nonexistent", output_root=tmp_path,
        archive_path=tmp_path / "archive",
    )
    assert out is not None
    assert [e["video_id"] for e in out["entries"]] == ["a1", "a2"]
    assert out["totals"]["new_tracks"] == 2
    assert out["payload"]["entries"] == out["entries"]
    assert out["payload"]["releases"][0]["selected"] is True
    assert out["payload"]["releases"][1]["selected"] is False


def test_apply_selection_default_when_no_tokens(tmp_path):
    catalog, rows = _kept_fixture()
    out = artists.apply_selection(
        catalog, rows, [],
        headers_path="/nonexistent", output_root=tmp_path,
        archive_path=tmp_path / "archive",
    )
    assert out is not None
    assert [e["video_id"] for e in out["entries"]] == ["a1", "a2", "s1"]


def test_apply_selection_none_rows_returns_none(tmp_path):
    catalog, rows = _kept_fixture()
    assert artists.apply_selection(
        catalog, rows, artists.parse_selection("A[9]"),
        headers_path="/nonexistent", output_root=tmp_path,
        archive_path=tmp_path / "archive",
    ) is None


def test_apply_selection_tagged_row_uses_run_cache(tmp_path):
    # Tagged releases resolve from _resolve_cache (no network, no headers).
    releases = [
        _meta(0, "Good Album", new=0),
        _meta(1, "Live Boot", section="A", status="excluded", new=0,
              tags=["live"], reason="live", tracks=1),
    ]
    cache = {releases[1]["browse_id"]: [
        {"video_id": "l1", "title": "Live Track", "artist": "Artist",
         "duration_seconds": 120, "track_num": 1},
    ]}
    catalog = _catalog(releases)
    catalog["_resolve_cache"] = cache
    rows = _sections(catalog)
    out = artists.apply_selection(
        catalog, rows, artists.parse_selection("A[2]"),
        headers_path="/MISSING-must-not-be-touched", output_root=tmp_path,
        archive_path=tmp_path / "archive",
    )
    assert out is not None
    assert [e["video_id"] for e in out["entries"]] == ["l1"]
    assert out["entries"][0]["release_type"] == "album"  # live boot is an album kind


def test_print_sections_smoke(capsys):
    catalog, rows = _kept_fixture()
    artists.print_sections(rows, width=100)
    out = capsys.readouterr().out
    assert "A. Albums" in out
    assert "S. Singles & EPs" in out
