"""Pure-function tests for the artist catalog feature."""

from __future__ import annotations

import os
import pathlib

from packrat import artists
from packrat.naming import track_path


def _release(title: str, year: str, type_: str, browse: str, section: str = "albums") -> dict:
    return {
        "title": title,
        "year": year,
        "type": type_,
        "browseId": browse,
        "_section": section,
    }


# --- parse helpers ---


def test_parse_channel_id():
    assert artists.parse_channel_id("UC0C-w0YjGpqDXGB8IHb662A") == "UC0C-w0YjGpqDXGB8IHb662A"
    assert (
        artists.parse_channel_id("https://music.youtube.com/channel/UCshort_id_1234567890")
        is None
    )
    url = "https://www.youtube.com/channel/UC0C-w0YjGpqDXGB8IHb662A"
    assert artists.parse_channel_id(url) == "UC0C-w0YjGpqDXGB8IHb662A"
    assert artists.parse_channel_id("Daft Punk") is None


def test_parse_subscribers():
    assert artists.parse_subscribers("3.86M subscribers") == 3_860_000
    assert artists.parse_subscribers("1,234 subscribers") == 1234
    assert artists.parse_subscribers("") == -1


def test_parse_duration():
    assert artists._parse_duration("3:41") == 221
    assert artists._parse_duration("1:03:05") == 3785
    assert artists._parse_duration("") is None


# --- release-type filtering ---


def test_filter_releases_defaults():
    releases = [
        _release("Discovery", "2001", "Album", "MPREb_a"),
        _release("Alive 2007 (Live)", "2007", "Album", "MPREb_b"),  # live
        _release("Musique Vol. 1", "2006", "Album", "MPREb_c"),  # compilation? no keyword
        _release("Greatest Hits", "2008", "Album", "MPREb_d"),  # compilation
        _release("Get Lucky (Remixes)", "2013", "Single", "MPREb_e"),  # remix
        _release("Something About Us", "2002", "Single", "MPREb_f"),
    ]
    kept, counts = artists.filter_releases(releases)
    titles = [r["title"] for r in kept]
    assert "Discovery" in titles
    assert "Alive 2007 (Live)" not in titles
    assert "Greatest Hits" not in titles
    assert "Get Lucky (Remixes)" not in titles
    assert counts["live"] == 1
    assert counts["compilation"] == 1
    assert counts["remix"] == 1
    assert all("excluded_rule" in r for r in releases if r not in kept)


def test_filter_releases_albums_only_and_no_singles():
    releases = [
        _release("Discovery", "2001", "Album", "MPREb_a"),
        _release("Alive 1999", "1999", "EP", "MPREb_b"),
        _release("Get Lucky", "2013", "Single", "MPREb_c", section="singles"),
    ]
    kept, counts = artists.filter_releases(releases, albums_only=True)
    assert [r["title"] for r in kept] == ["Discovery"]
    assert counts["albums_only"] == 2

    releases2 = [
        _release("Discovery", "2001", "Album", "MPREb_a"),
        _release("Alive 1999", "1999", "EP", "MPREb_b"),
        _release("Get Lucky", "2013", "Single", "MPREb_c", section="singles"),
    ]
    kept2, counts2 = artists.filter_releases(releases2, no_singles=True)
    assert [r["title"] for r in kept2] == ["Discovery", "Alive 1999"]
    assert counts2["no_singles"] == 1


def test_filter_releases_include_flags_reinstate():
    releases = [
        _release("Alive 2007", "2007", "Album", "MPREb_b"),
        _release("Greatest Hits", "2008", "Album", "MPREb_d"),
        _release("Get Lucky (Remixes)", "2013", "Single", "MPREb_e"),
    ]
    kept, counts = artists.filter_releases(
        releases, include_live=True, include_compilations=True, include_remixes=True
    )
    assert len(kept) == 3
    assert counts["live"] == 0 and counts["compilation"] == 0 and counts["remix"] == 0


# --- edition dedupe ---


def test_dedupe_editions_prefers_standard():
    releases = [
        _release("Discovery (Deluxe Edition)", "2001", "Album", "MPREb_deluxe"),
        _release("Discovery", "2001", "Album", "MPREb_std"),
        _release("Discovery (Clean Version)", "2001", "Album", "MPREb_clean"),
    ]
    kept, dropped = artists.dedupe_editions(releases)
    assert len(kept) == 1
    assert kept[0]["browseId"] == "MPREb_std"
    assert len(dropped) == 2
    assert all("duplicate edition of Discovery" in reason for _, reason in dropped)


def test_dedupe_editions_keeps_onlyedition():
    releases = [_release("Tron: Legacy (Bonus Edition)", "2010", "Album", "MPREb_t")]
    kept, dropped = artists.dedupe_editions(releases)
    assert len(kept) == 1 and dropped == []


# --- naming / ordering ---


def test_track_path_naming(tmp_path: pathlib.Path):
    p = track_path(tmp_path, "Daft Punk", "Discovery", "One More Time", track_num=1)
    os.makedirs(p.parent, exist_ok=True)
    expected = tmp_path / "Daft_Punk" / "Discovery" / "01 - One_More_Time.m4a"
    assert p == expected  # NN - title, restricted-name dirs
    track = {
        "video_id": "abc",
        "title": "One More Time",
        "artist": "Daft Punk",
        "track_num": 1,
    }
    release = {"title": "Discovery"}
    assert not p.exists()
    assert not artists.owned_file_exists(tmp_path, track, release)
    p.touch()
    assert artists.owned_file_exists(tmp_path, track, release)


def test_artist_all_entries_orders_by_year_track():
    entries = [
        {"release_year": 2001, "track_num": 2, "album": "A"},
        {"release_year": 1997, "track_num": 5, "album": "B"},
        {"release_year": 2001, "track_num": 1, "album": "A"},
        {"release_year": None, "track_num": 3, "album": "C"},
    ]
    ordered = artists.artist_all_entries(entries)
    albums_years = [(e["album"], e["track_num"]) for e in ordered]
    assert albums_years[0][0] == "B"
    assert (albums_years[1][1], albums_years[2][1]) == (1, 2)


def test_release_kind_sections():
    assert artists.release_kind({"type": "Album", "_section": "albums"}) == "album"
    assert artists.release_kind({"type": "", "_section": "singles"}) == "single"
    assert artists.release_kind({"type": "Single", "_section": "singles"}) == "single"
    assert artists.release_kind({"type": "EP", "_section": "albums"}) == "ep"


def test_owned_file_exists_legacy_single_layout(tmp_path: pathlib.Path):
    """Old single folders (<Artist>/<SingleTitle>/NN - title.m4a) count."""
    legacy = track_path(
        tmp_path, "Artist", "Old Single", "Song", track_num=1
    )
    legacy.parent.mkdir(parents=True)
    legacy.touch()
    track = {"video_id": "v1", "title": "Song", "artist": "Artist",
             "track_num": 1}
    assert artists.owned_file_exists(
        tmp_path, track, {"title": "Old Single", "kind": "single"}
    )
