"""Upgrade command: version selection and safe check-only behavior."""

from __future__ import annotations

import pathlib

import pytest

from packrat import upgrade


def test_compare_versions():
    assert upgrade.compare_versions("0.3.2", "0.3.1") > 0
    assert upgrade.compare_versions("0.3.1", "0.3.1") == 0
    assert upgrade.compare_versions("0.3.0", "0.3.1") < 0
    assert upgrade.compare_versions("0.3.1", "0.3.1.dev2") > 0


def test_latest_release_uses_wheel_and_checksums(monkeypatch):
    monkeypatch.setattr(
        upgrade,
        "_get_json",
        lambda url: {
            "tag_name": "v0.3.2",
            "draft": False,
            "prerelease": False,
            "assets": [
                {"name": "packrat-0.3.2-py3-none-any.whl", "browser_download_url": "https://example/w.whl"},
                {"name": "SHA256SUMS", "browser_download_url": "https://example/SHA256SUMS"},
            ],
        },
    )
    release = upgrade.latest_release()
    assert release.version == "0.3.2"
    assert release.wheel_name.endswith(".whl")
    assert release.checksums_url.endswith("SHA256SUMS")


def test_check_only_does_not_touch_install(monkeypatch, capsys):
    monkeypatch.setattr(
        upgrade,
        "latest_release",
        lambda include_prerelease=False: upgrade.Release(
            version="99.0.0",
            tag="v99.0.0",
            wheel_url="https://example/w.whl",
            wheel_name="packrat-99.0.0-py3-none-any.whl",
            checksums_url="https://example/SHA256SUMS",
        ),
    )
    assert upgrade.run_upgrade(check_only=True) == 0
    out = capsys.readouterr().out
    assert "update available: 99.0.0" in out


def test_check_reports_current(monkeypatch, capsys):
    monkeypatch.setattr(
        upgrade,
        "latest_release",
        lambda include_prerelease=False: upgrade.Release(
            version=upgrade.__version__,
            tag=f"v{upgrade.__version__}",
            wheel_url="https://example/w.whl",
            wheel_name="packrat.whl",
            checksums_url="https://example/SHA256SUMS",
        ),
    )
    assert upgrade.run_upgrade(check_only=True) == 0
    assert "up to date" in capsys.readouterr().out


def test_standard_install_refuses_source_checkout(monkeypatch, tmp_path):
    monkeypatch.setattr(upgrade, "INSTALL_ROOT", tmp_path / "install")
    monkeypatch.setattr(upgrade.sys, "prefix", str(tmp_path / "source" / ".venv"))
    with pytest.raises(upgrade.UpgradeError, match="standard installer layout"):
        upgrade.standard_install()


def test_standard_install_accepts_layout(monkeypatch, tmp_path):
    root = tmp_path / "install"
    venv = root / "venv"
    venv.mkdir(parents=True)
    monkeypatch.setattr(upgrade, "INSTALL_ROOT", root)
    monkeypatch.setattr(upgrade, "BIN_DIR", tmp_path / "bin")
    monkeypatch.setattr(upgrade.sys, "prefix", str(venv))
    got_root, got_bin = upgrade.standard_install()
    assert got_root == root.resolve()
    assert got_bin == (tmp_path / "bin").resolve()


def test_compare_versions_uses_pep440_ordering():
    assert upgrade.compare_versions("0.3.4.dev100", "0.3.4.dev99") > 0
    assert upgrade.compare_versions("0.3.4rc10", "0.3.4rc2") > 0
    assert upgrade.compare_versions("0.3.4", "0.3.4.post1") < 0


def test_latest_release_selects_matching_wheel_not_lexicographic(monkeypatch):
    monkeypatch.setattr(
        upgrade,
        "_get_json",
        lambda url: {
            "tag_name": "v0.3.10",
            "draft": False,
            "prerelease": False,
            "assets": [
                {"name": "packrat-0.3.9-py3-none-any.whl", "browser_download_url": "https://example/9.whl"},
                {"name": "packrat-0.3.10-py3-none-any.whl", "browser_download_url": "https://example/10.whl"},
                {"name": "SHA256SUMS", "browser_download_url": "https://example/SHA256SUMS"},
            ],
        },
    )
    release = upgrade.latest_release()
    assert release.wheel_name == "packrat-0.3.10-py3-none-any.whl"


def test_dry_run_does_not_require_standard_layout(monkeypatch, capsys):
    monkeypatch.setattr(
        upgrade,
        "latest_release",
        lambda include_prerelease=False: upgrade.Release(
            version="99.0.0", tag="v99.0.0",
            wheel_url="https://example/w.whl",
            wheel_name="packrat-99.0.0-py3-none-any.whl",
            checksums_url="https://example/SHA256SUMS",
        ),
    )
    assert upgrade.run_upgrade(dry_run=True) == 0
    assert "dry-run" in capsys.readouterr().out


def test_replace_install_rolls_back(monkeypatch, tmp_path):
    root = tmp_path / "install"
    venv = root / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "marker").write_text("old")
    monkeypatch.setattr(upgrade, "_install_wheel", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        upgrade._replace_install(tmp_path / "wheel.whl", root, tmp_path / "bin", "99.0.0")
    assert (venv / "marker").read_text() == "old"
    assert not (root / "venv.upgrade-backup").exists()
