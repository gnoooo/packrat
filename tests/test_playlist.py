"""Golden tests for m3u8 generation."""

from ytm_offline.playlist import write_m3u8


def test_write_m3u8_produces_extinf_and_relative_paths(tmp_path):
    playlist_path = tmp_path / "lists" / "test.m3u8"
    track_file = tmp_path / "music" / "Artist" / "Album" / "01 - Song.m4a"
    track_file.parent.mkdir(parents=True)
    track_file.write_text("audio", encoding="utf-8")
    write_m3u8(
        playlist_path,
        [
            {
                "path": str(track_file),
                "duration": 184,
                "title": "Song",
                "artist": "Artist",
            }
        ],
    )
    text = playlist_path.read_text(encoding="utf-8")
    assert text.startswith("#EXTM3U\n")
    assert "#EXTINF:184,Artist - Song\n" in text
    assert "../music/Artist/Album/01 - Song.m4a" in text
