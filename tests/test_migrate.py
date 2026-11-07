"""Tests for the migrate-layout command (run on tmp copies only)."""

from __future__ import annotations

import contextlib
import io
import json
import pathlib

from ytm_offline import cli, migrate
from ytm_offline.naming import track_path


def _touch(path: pathlib.Path) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("audio", encoding="utf-8")
    return path


def _entry(video_id: str, title: str, path: pathlib.Path, **extra) -> dict:
    entry = {
        "video_id": video_id,
        "title": title,
        "artist": "Artist",
        "album": "Old Single",
        "track_num": 1,
        "duration": 180,
        "est_size": 16000,
        "status": "downloaded",
        "path": str(path),
        "error": None,
        "source": "artist:Artist",
        "release_year": extra.pop("release_year", 2020),
        "release_type": "single",
    }
    entry.update(extra)
    return entry


def _make_music(root: pathlib.Path, entries: list[dict], *, command: str = "artist") -> None:
    playlists_dir = root / "playlists"
    playlists_dir.mkdir(parents=True, exist_ok=True)
    if command == "artist":
        payload = {"command": "artist", "label": "Test", "entries": entries}
        manifest = playlists_dir / "artist-Test.manifest.json"
    else:
        payload = {"entries": entries}
        manifest = playlists_dir / "generic.manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")


def _output() -> tuple[contextlib._GeneratorContextManager, io.StringIO]:
    buf = io.StringIO()
    return contextlib.redirect_stdout(buf), buf


def test_migrate_moves_legacy_single(tmp_path: pathlib.Path):
    root = tmp_path / "music"
    old = _touch(track_path(root, "Artist", "Old Single", "First Song", 1))
    _make_music(root, [_entry("v1", "First Song", old)])

    with _output()[0]:
        rc = migrate.run_migrate(root, yes=True)
    assert rc == 0

    new = root / "Artist" / "Singles" / "First_Song.m4a"
    assert new.exists()
    assert not old.exists()
    assert not old.parent.exists()

    data = json.loads((root / "playlists" / "artist-Test.manifest.json").read_text())
    assert data["entries"][0]["path"] == str(new)
    assert (root / "playlists" / "migrate-layout.log.json").exists()


def test_migrate_dry_run_does_nothing(tmp_path: pathlib.Path):
    root = tmp_path / "music"
    old = _touch(track_path(root, "Artist", "Old Single", "First Song", 1))
    _make_music(root, [_entry("v1", "First Song", old)])

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = migrate.run_migrate(root, dry_run=True)
    assert rc == 0
    assert "singles to move: 1" in buf.getvalue()
    assert old.exists()
    assert not (root / "playlists" / "migrate-layout.log.json").exists()


def test_migrate_is_idempotent(tmp_path: pathlib.Path):
    root = tmp_path / "music"
    old = _touch(track_path(root, "Artist", "Old Single", "First Song", 1))
    _make_music(root, [_entry("v1", "First Song", old)])
    with _output()[0]:
        migrate.run_migrate(root, yes=True)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = migrate.run_migrate(root, yes=True)
    assert rc == 0
    assert "singles to move: 0" in buf.getvalue()


def test_migrate_reports_ambiguous_folder(tmp_path: pathlib.Path):
    root = tmp_path / "music"
    old = _touch(track_path(root, "Artist", "Old Single", "First Song", 1))
    _make_music(root, [_entry("v1", "First Song", old)])
    mystery = _touch(root / "Artist" / "Mystery" / "Unknown_Track.m4a")

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        migrate.run_migrate(root, yes=True)
    out = buf.getvalue()
    assert "ambiguous folders (not referenced by manifests): 1" in out
    assert str(mystery.parent) in out
    assert mystery.exists()


def test_migrate_leaves_albums_alone(tmp_path: pathlib.Path):
    root = tmp_path / "music"
    album_file = _touch(track_path(root, "Artist", "Great Album", "Album Song", 5))
    _make_music(
        root,
        [_entry("v1", "Album Song", album_file, album="Great Album",
                release_type="album", release_year=2019)],
    )

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = migrate.run_migrate(root, yes=True)
    assert rc == 0
    assert "singles to move: 0" in buf.getvalue()
    assert album_file.exists()


def test_migrate_collision_never_overwrites(tmp_path: pathlib.Path):
    root = tmp_path / "music"
    old = _touch(track_path(root, "Artist", "Old Single", "Same Name", 1))
    _make_music(root, [_entry("v1", "Same Name", old)])
    target = root / "Artist" / "Singles"
    target.mkdir(parents=True)
    (target / "Same_Name.m4a").write_text("existing", encoding="utf-8")

    with _output()[0]:
        rc = migrate.run_migrate(root, yes=True)
    assert rc == 0

    moved = [p for p in target.iterdir() if p.name != "Same_Name.m4a"]
    assert len(moved) == 1
    assert (target / "Same_Name.m4a").read_text() == "existing"


def test_migrate_undo_restores(tmp_path: pathlib.Path):
    root = tmp_path / "music"
    old = _touch(track_path(root, "Artist", "Old Single", "First Song", 1))
    _make_music(root, [_entry("v1", "First Song", old)])

    with _output()[0]:
        migrate.run_migrate(root, yes=True)
    moved = root / "Artist" / "Singles" / "First_Song.m4a"
    assert moved.exists()

    _, buf = _output()
    with _output()[0]:
        rc = migrate.run_migrate(root, undo=True)
    assert rc == 0
    assert old.exists()
    assert not moved.exists()

    data = json.loads((root / "playlists" / "artist-Test.manifest.json").read_text())
    assert data["entries"][0]["path"] == str(old)
    log = json.loads((root / "playlists" / "migrate-layout.log.json").read_text())
    assert log["runs"] == []


def test_cli_migrate_layout_subparser():
    args = cli.build_parser().parse_args(
        ["migrate-layout", "--dry-run", "--output", "somewhere"]
    )
    assert args.command == "migrate-layout"
    assert args.dry_run is True
    assert args.output == "somewhere"
