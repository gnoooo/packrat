"""Tests for the Singles-folder layout behavior change."""

from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch

from packrat import artists as artists_module
from packrat.naming import track_path


def test_single_release_type_goes_to_singles_without_track_number():
    p = track_path(
        pathlib.Path("music"), "Artist", "Great Single", "New Song",
        track_num=1, release_type="single",
    )
    assert p == pathlib.Path("music/Artist/Singles/New_Song.m4a")


def test_no_album_goes_to_singles():
    p = track_path(pathlib.Path("music"), "Artist", "", "Orphan Song")
    assert p == pathlib.Path("music/Artist/Singles/Orphan_Song.m4a")


def test_unknown_album_falls_back_to_singles():
    p = track_path(
        pathlib.Path("music"), "Artist", "Unknown Album", "Orphan Song",
    )
    assert p == pathlib.Path("music/Artist/Singles/Orphan_Song.m4a")


def test_album_layout_unchanged():
    p = track_path(pathlib.Path("music"), "Artist", "Album", "Title", 3)
    assert p == pathlib.Path("music/Artist/Album/03 - Title.m4a")


def test_ep_stays_album():
    p = track_path(
        pathlib.Path("music"), "Artist", "My EP", "Song",
        track_num=2, release_type="ep",
    )
    assert p == pathlib.Path("music/Artist/My_EP/02 - Song.m4a")


def test_singles_dir_custom(tmp_path: pathlib.Path):
    p = track_path(
        tmp_path, "Artist", "A Single", "Song", release_type="single",
        singles_dir="singles_old",
    )
    assert p == tmp_path / "Artist" / "singles_old" / "Song.m4a"




def _single_release(title: str, year: str, browse: str, section: str = "singles", type_: str = "Single") -> dict:
    return {"title": title, "year": year, "type": type_, "browseId": browse, "_section": section}


def _album_track(video_id: str, title: str, track_number: int = 1) -> dict:
    return {
        "videoId": video_id,
        "title": title,
        "artists": [{"name": "Artist", "id": "UCartist"}],
        "duration_seconds": 180,
        "trackNumber": track_number,
    }


@patch.object(artists_module, "YTMusic")
@patch.object(artists_module, "Archive")
def test_single_release_entries_marked_single(mock_archive, mock_ytmusic, tmp_path):
    mock_ytmusic.return_value = MagicMock()
    mock_archive.return_value = set()

    yt = mock_ytmusic.return_value
    yt.get_album.return_value = {
        "title": "Song Title - Single",
        "tracks": [_album_track("s1", "Song Title"), _album_track("s2", "B Side", 2)],
        "year": 2024,
    }
    with patch.object(
        artists_module, "get_discography",
        return_value={"name": "Artist", "subscribers": "",
                      "releases": [_single_release("Song Title", "2024", "MPREb_s")]},
    ):
        result = artists_module.build_catalog(
            "/fake/headers.json", "UCartist", tmp_path, tmp_path / "archive",
            scope="catalog",
        )
    singles = [e for e in result["entries"] if e["release_type"] == "single"]
    assert len(singles) == 2
    assert all(e["singles_dir"] == "Singles" for e in singles)
    # Routing drops the track number when placing singles.
    for e in singles:
        p = track_path(
            tmp_path, e["artist"], e["album"], e["title"],
            e["track_num"], release_type=e["release_type"],
        )
        assert p == tmp_path / "Artist" / "Singles" / p.name
        assert " - " not in p.name


@patch.object(artists_module, "YTMusic")
@patch.object(artists_module, "Archive")
def test_ep_release_keeps_album_kind(mock_archive, mock_ytmusic, tmp_path):
    mock_ytmusic.return_value = MagicMock()
    mock_archive.return_value = set()

    yt = mock_ytmusic.return_value
    yt.get_album.return_value = {
        "title": "Cool EP",
        "tracks": [_album_track("e1", "EP Song")],
        "year": 2023,
    }
    with patch.object(
        artists_module, "get_discography",
        return_value={"name": "Artist", "subscribers": "",
                      "releases": [_single_release("Cool EP", "2023", "MPREb_ep", section="albums", type_="EP")]},
    ):
        result = artists_module.build_catalog(
            "/fake/headers.json", "UCartist", tmp_path, tmp_path / "archive",
            scope="catalog",
        )
    entries = result["entries"]
    assert all(e["release_type"] != "single" for e in entries)


@patch.object(artists_module, "YTMusic")
@patch.object(artists_module, "Archive")
def test_old_layout_single_not_redownloaded(mock_archive, mock_ytmusic, tmp_path):
    mock_ytmusic.return_value = MagicMock()
    mock_archive.return_value = set()

    yt = mock_ytmusic.return_value
    yt.get_album.return_value = {
        "title": "Old Single",
        "tracks": [_album_track("old1", "Stale Song")],
        "year": 2020,
    }
    # Old layout file on disk: music/<Artist>/<SingleTitle>/01 - stale song.m4a
    legacy_path = track_path(
        tmp_path, "Artist", "Old Single", "Stale Song", track_num=1,
    )
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text("x")
    with patch.object(
        artists_module, "get_discography",
        return_value={"name": "Artist", "subscribers": "",
                      "releases": [_single_release("Old Single", "2020", "MPREb_o")]},
    ):
        result = artists_module.build_catalog(
            "/fake/headers.json", "UCartist", tmp_path, tmp_path / "archive",
            scope="catalog",
        )
    assert not any(e["release_type"] == "single" for e in result["entries"])
    assert result["totals"]["already_owned"] == 1


@patch.object(artists_module, "YTMusic")
@patch.object(artists_module, "Archive")
def test_archive_video_id_skips_old_layout_single(mock_archive, mock_ytmusic, tmp_path):
    mock_ytmusic.return_value = MagicMock()
    mock_archive.return_value = {"old1"}  # archived in any layout

    yt = mock_ytmusic.return_value
    yt.get_album.return_value = {
        "title": "Archived Single",
        "tracks": [_album_track("old1", "Archived Song")],
        "year": 2020,
    }
    with patch.object(
        artists_module, "get_discography",
        return_value={"name": "Artist", "subscribers": "",
                      "releases": [_single_release("Archived Single", "2020", "MPREb_a")]},
    ):
        result = artists_module.build_catalog(
            "/fake/headers.json", "UCartist", tmp_path, tmp_path / "archive",
            scope="catalog",
        )
    assert not any(e["release_type"] == "single" for e in result["entries"])
