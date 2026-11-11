"""Characterization tests for artist owned-key seeding and top/catalog dedupe."""

from unittest.mock import MagicMock, patch

import pytest

from packrat import artists as artists_module


def _make_top_entry(video_id, title, artist="Artist", duration=180):
    return {
        "video_id": video_id,
        "title": title,
        "artist": artist,
        "album": "Top songs",
        "track_num": None,
        "duration": duration,
        "est_size": duration * 16000,
        "status": "pending",
        "path": None,
        "error": None,
        "source": "top:Artist",
        "release_year": 9999,
        "release_type": "top",
        "popularity": 0,
    }


def _make_album_track(video_id, title, duration=180):
    return {
        "videoId": video_id,
        "title": title,
        "artists": [{"name": "Artist", "id": "UCartist"}],
        "duration_seconds": duration,
        "trackNumber": 1,
    }


@patch.object(artists_module, "YTMusic")
@patch.object(artists_module, "Archive")
@patch.object(artists_module, "build_top_songs")
@patch.object(artists_module, "get_discography")
def test_scope_all_top_songs_come_first_and_seed_owned_keys(
    mock_get_discography,
    mock_build_top_songs,
    mock_archive,
    mock_ytmusic,
    tmp_path,
):
    yt = MagicMock()
    mock_ytmusic.return_value = yt
    mock_archive.return_value = set()

    top_entry = _make_top_entry("top1", "Top Hit")
    mock_build_top_songs.return_value = {
        "artist": "Artist",
        "browse_id": "UCartist",
        "entries": [top_entry],
        "excluded_counts": {},
        "totals": {
            "total_tracks": 1,
            "new_tracks": 1,
            "already_owned": 0,
            "excluded_counts": {},
            "est_bytes": top_entry["est_size"],
            "est_human": "2.74 MB",
            "est_time_seconds": 100,
        },
        "top_versions_skipped": 0,
    }

    mock_get_discography.return_value = {
        "name": "Artist",
        "subscribers": "1M",
        "releases": [
            {
                "title": "Album",
                "year": "2020",
                "type": "Album",
                "browseId": "MPREb_album",
            }
        ],
    }

    yt.get_album.return_value = {
        "tracks": [
            _make_album_track("album1", "Album Only", duration=200),
        ],
        "year": 2020,
    }

    catalog = artists_module.build_catalog(
        "/fake/headers.json",
        "UCartist",
        tmp_path,
        tmp_path / "archive",
        scope="all",
    )

    entries = catalog["entries"]
    assert entries[0]["video_id"] == "top1"
    assert entries[1]["video_id"] == "album1"
    assert catalog["totals"]["new_tracks"] == 2


@patch.object(artists_module, "YTMusic")
@patch.object(artists_module, "Archive")
@patch.object(artists_module, "build_top_songs")
@patch.object(artists_module, "get_discography")
def test_scope_all_does_not_duplicate_top_song_in_catalog(
    mock_get_discography,
    mock_build_top_songs,
    mock_archive,
    mock_ytmusic,
    tmp_path,
):
    yt = MagicMock()
    mock_ytmusic.return_value = yt
    mock_archive.return_value = set()

    top_entry = _make_top_entry("top1", "Shared Song")
    mock_build_top_songs.return_value = {
        "artist": "Artist",
        "browse_id": "UCartist",
        "entries": [top_entry],
        "excluded_counts": {},
        "totals": {
            "total_tracks": 1,
            "new_tracks": 1,
            "already_owned": 0,
            "excluded_counts": {},
            "est_bytes": top_entry["est_size"],
            "est_human": "2.74 MB",
            "est_time_seconds": 100,
        },
        "top_versions_skipped": 0,
    }

    mock_get_discography.return_value = {
        "name": "Artist",
        "subscribers": "1M",
        "releases": [
            {
                "title": "Album",
                "year": "2020",
                "type": "Album",
                "browseId": "MPREb_album",
            }
        ],
    }

    yt.get_album.return_value = {
        "tracks": [
            _make_album_track("album1", "Shared Song", duration=200),
            _make_album_track("album2", "Other Song", duration=200),
        ],
        "year": 2020,
    }

    catalog = artists_module.build_catalog(
        "/fake/headers.json",
        "UCartist",
        tmp_path,
        tmp_path / "archive",
        scope="all",
    )

    entries = catalog["entries"]
    video_ids = [e["video_id"] for e in entries]
    assert video_ids == ["top1", "album2"]
    assert catalog["totals"]["already_owned"] == 1


def test_dedupe_editions_numbered_variants_are_separate_releases():
    # [DAY 1] vs [DAY 2] (and Part/Vol/Disc/any number) are different
    # releases, not edition duplicates.
    releases = [
        {"title": "Legend – Metal Galaxy [DAY 1]", "browseId": "b1", "year": "2020"},
        {"title": "Legend – Metal Galaxy [DAY 2]", "browseId": "b2", "year": "2020"},
        {"title": "Saga Part 1", "browseId": "b3", "year": "2019"},
        {"title": "Saga Part 2", "browseId": "b4", "year": "2019"},
        {"title": "Anthology Vol. 2", "browseId": "b5", "year": "2018"},
        {"title": "Anthology", "browseId": "b6", "year": "2018"},
        {"title": "Album (Deluxe)", "browseId": "b7", "year": "2017"},
        {"title": "Album", "browseId": "b8", "year": "2017"},
    ]
    kept, dropped = artists_module.dedupe_editions(releases)
    kept_ids = {r["browseId"] for r in kept}
    assert {"b1", "b2", "b3", "b4", "b5", "b6", "b8"} <= kept_ids
    dropped_ids = {r["browseId"] for r, _ in dropped}
    # Deluxe remains an edition duplicate; [DAY n]/Part/Vol stay.
    assert dropped_ids == {"b7"}


def test_dedupe_editions_numbered_parenthetical_false_positive():
    # An edition-word suffix containing a number ("2011 Remaster") still
    # lists as its own release by the number rule.
    kept, dropped = artists_module.dedupe_editions(
        [
            {"title": "Attack", "browseId": "a1", "year": "1972"},
            {"title": "Attack (2011 Remaster)", "browseId": "a2", "year": "2011"},
        ]
    )
    assert len(kept) == 2 and not dropped


class _FakeAlbumYT:
    def get_album(self, browseId):
        return {
            "year": "2024",
            "tracks": [{
                "videoId": "v1",
                "title": "FRDP",
                "trackNumber": 1,
                "artists": [{"name": "Sheena Ringo"}, {"name": "ATARASHII GAKKO!"}],
                "duration_seconds": 183,
            }],
        }


def test_release_tracks_use_catalog_artist_not_featured_artist():
    tracks = artists_module.resolve_release_tracks(
        _FakeAlbumYT(), {"browseId": "album"}, "ATARASHII GAKKO!",
    )
    assert tracks and tracks[0]["artist"] == "ATARASHII GAKKO!"


class _FakeTopYT:
    def get_artist(self, browse_id):
        return {
            "name": "ATARASHII GAKKO!",
            "songs": {"results": [{
                "videoId": "v1",
                "title": "FRDP",
                "artists": [{"name": "Sheena Ringo"}, {"name": "ATARASHII GAKKO!"}],
                "album": {"name": "Carnival"},
                "duration_seconds": 183,
            }]},
        }


def test_top_songs_use_catalog_artist_not_featured_artist():
    top = artists_module.get_top_songs(_FakeTopYT(), "artist-id")
    assert top["tracks"][0]["artist"] == "ATARASHII GAKKO!"


def test_entry_for_forces_catalog_artist():
    entry = artists_module._entry_for(
        {"video_id": "v1", "title": "FRDP", "artist": "Sheena Ringo",
         "duration_seconds": 183},
        {"title": "Carnival", "kind": "album", "year": "2024"},
        "ATARASHII GAKKO!",
    )
    assert entry["artist"] == "ATARASHII GAKKO!"
    assert entry["album"] == "Carnival"


def test_channel_parser_skips_headerless_carousels():
    from gettext import NullTranslations
    from ytmusicapi.parsers.i18n import Parser

    parser = Parser(NullTranslations())
    # A video carousel without the header that ytmusicapi 1.12.3 assumes.
    results = [{"musicCarouselShelfRenderer": {"contents": []}}]
    assert parser.parse_channel_contents(results) == {}
