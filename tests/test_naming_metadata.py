"""Naming, Unicode, collision and tag-writing regressions."""

from __future__ import annotations

import pathlib

from packrat import naming, normalize, song_meta


def test_clean_preserves_unicode_but_keeps_path_safe():
    assert naming.clean("宇多田ヒカル") == "宇多田ヒカル"
    assert naming.clean("AC/DC") == "AC_DC"
    assert naming.clean("The Hives") == "The_Hives"
    assert naming.clean("…") == "…"
    assert naming.clean("..") == "_"


def test_normalize_preserves_distinct_unicode_names():
    assert normalize.normalize("宇多田ヒカル")
    assert normalize.normalize("米津玄師")
    assert normalize.normalize("宇多田ヒカル") != normalize.normalize("米津玄師")
    assert normalize.normalize("AC/DC") == "acdc"


def test_unicode_track_path(tmp_path: pathlib.Path):
    path = naming.track_path(tmp_path, "宇多田ヒカル", "First Love", "Automatic", 1)
    assert path == tmp_path / "宇多田ヒカル" / "First_Love" / "01 - Automatic.m4a"


def test_collision_path_is_deterministic(tmp_path: pathlib.Path):
    original = tmp_path / "Artist" / "Singles" / "Intro.m4a"
    alternate = naming.collision_path(original, "abc123")
    assert alternate == tmp_path / "Artist" / "Singles" / "Intro [abc123].m4a"


def test_apply_song_metadata_writes_canonical_mp4_tags(monkeypatch, tmp_path):
    import mutagen
    import mutagen.mp4

    class FakeMP4:
        def __init__(self):
            self.tags = {}
            self.saved = False

        def save(self):
            self.saved = True

    fake = FakeMP4()
    monkeypatch.setattr(mutagen, "File", lambda *_a, **_k: fake)
    monkeypatch.setattr(mutagen.mp4, "MP4", FakeMP4)
    path = tmp_path / "song.m4a"
    path.write_bytes(b"fake")
    meta = song_meta.SongMetadata(
        video_id="v1",
        title="Path Of Most Resistance",
        artist="The Hives",
        album="The Hives Forever Forever The Hives",
        track_num=12,
        track_total=13,
        year=2025,
    )
    assert song_meta.apply_song_metadata(path, meta) is True
    assert fake.saved is True
    assert fake.tags["\xa9nam"] == ["Path Of Most Resistance"]
    assert fake.tags["\xa9ART"] == ["The Hives"]
    assert fake.tags["\xa9alb"] == ["The Hives Forever Forever The Hives"]
    assert fake.tags["trkn"] == [(12, 13)]
    assert fake.tags["\xa9day"] == ["2025"]


def test_apply_song_metadata_ignores_non_mp4(monkeypatch, tmp_path):
    import mutagen

    class FakeAudio:
        tags = {}

    monkeypatch.setattr(mutagen, "File", lambda *_a, **_k: FakeAudio())
    path = tmp_path / "song.webm"
    path.write_bytes(b"fake")
    assert song_meta.apply_song_metadata(
        path,
        song_meta.SongMetadata(video_id="v1", title="T", artist="A", album="Al"),
    ) is False


def test_apply_song_metadata_writes_album_artist_and_disc(monkeypatch, tmp_path):
    import mutagen
    import mutagen.mp4

    class FakeMP4:
        def __init__(self):
            self.tags = {}
            self.saved = False

        def save(self):
            self.saved = True

    fake = FakeMP4()
    monkeypatch.setattr(mutagen, "File", lambda *_a, **_k: fake)
    monkeypatch.setattr(mutagen.mp4, "MP4", FakeMP4)
    path = tmp_path / "song.m4a"
    path.write_bytes(b"x")
    meta = song_meta.SongMetadata(
        video_id="v1", title="T", artist="Track Artist", album="Album",
        album_artist="Album Artist", track_num=2, track_total=10, disc_num=1,
    )
    assert song_meta.apply_song_metadata(path, meta) is True
    assert fake.tags["aART"] == ["Album Artist"]
    assert fake.tags["disk"] == [(1, 0)]


def test_verbose_env_enables_ytdlp_verbose(monkeypatch):
    from packrat import download

    monkeypatch.setenv("PACKRAT_YTDLP_VERBOSE", "1")
    opts = download._build_extract_opts(None, None, None, download._YtdlpLogger())
    assert opts["verbose"] is True
    assert opts["quiet"] is False
