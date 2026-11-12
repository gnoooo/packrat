"""The repo installer stays syntactically valid and dry-run-safe."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "install.sh"


@pytest.mark.skipif(sys.platform != "linux", reason="installer is Linux-only")
def test_install_script_syntax():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


@pytest.mark.skipif(sys.platform != "linux", reason="installer is Linux-only")
def test_install_script_help():
    result = subprocess.run(
        [str(SCRIPT), "--help"], check=True, text=True, capture_output=True
    )
    assert "Usage: ./install.sh" in result.stdout
    assert "--git URL" in result.stdout


@pytest.mark.skipif(sys.platform != "linux", reason="installer is Linux-only")
@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="installer dry-run checks ffmpeg/ffprobe",
)
def test_install_script_dry_run_does_not_write(tmp_path):
    prefix = tmp_path / "prefix"
    bin_dir = tmp_path / "bin"
    result = subprocess.run(
        [
            str(SCRIPT), "--dry-run",
            "--prefix", str(prefix),
            "--bin-dir", str(bin_dir),
        ],
        check=True, text=True, capture_output=True,
    )
    assert "dry-run complete" in result.stderr
    assert not prefix.exists()
    assert not bin_dir.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="installer is Linux-only")
@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="installer dry-run checks ffmpeg/ffprobe",
)
def test_piped_install_uses_default_repo():
    result = subprocess.run(
        ["bash", "-s", "--", "--dry-run"],
        input=SCRIPT.read_text(encoding="utf-8"),
        text=True, capture_output=True,
    )
    assert result.returncode == 0
    assert "https://github.com/gnoooo/packrat.git" in result.stderr


@pytest.mark.skipif(sys.platform != "linux", reason="installer is Linux-only")
@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="installer dry-run checks ffmpeg/ffprobe",
)
def test_piped_install_dry_run_with_git():
    result = subprocess.run(
        [
            "bash", "-s", "--", "--dry-run",
            "--git", "https://example.com/packrat.git",
        ],
        input=SCRIPT.read_text(encoding="utf-8"),
        text=True, capture_output=True,
    )
    assert result.returncode == 0
    assert "dry-run complete" in result.stderr
