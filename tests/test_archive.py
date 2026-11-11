"""Archive semantics characterization tests."""

from packrat.archive import Archive


def test_archive_loads_existing_ids(tmp_path):
    path = tmp_path / "archive.txt"
    path.write_text("abc123\nxyz789\n", encoding="utf-8")
    archive = Archive(path)
    assert "abc123" in archive
    assert "xyz789" in archive
    assert "missing" not in archive
    assert len(archive) == 2


def test_archive_add_and_save_are_atomic(tmp_path):
    path = tmp_path / "sub" / "archive.txt"
    archive = Archive(path)
    archive.add("new_id")
    archive.update(["another", "third"])
    archive.save()
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "new_id" in text
    assert "another" in text
    assert "third" in text
    # Reload sees saved state.
    reloaded = Archive(path)
    assert "new_id" in reloaded
    assert len(reloaded) == 3


def test_archive_add_is_idempotent(tmp_path):
    path = tmp_path / "archive.txt"
    archive = Archive(path)
    archive.add("same")
    archive.add("same")
    archive.save()
    assert len(Archive(path)) == 1


def test_archive_append_only_keeps_existing_ids(tmp_path):
    path = tmp_path / "archive.txt"
    archive = Archive(path)
    archive.add("first")
    archive.save()
    archive.add("second")
    archive.save()
    text = path.read_text(encoding="utf-8")
    assert text.count("first") == 1
    assert text.count("second") == 1
    assert archive.ids() == {"first", "second"}
