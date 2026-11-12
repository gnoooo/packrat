"""Release version consistency checker tests."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).parents[1]
CHECK = ROOT / "dev" / "check_version.py"


def _run(root: pathlib.Path, *args: str):
    env = dict(os.environ, PACKRAT_ROOT=str(root))
    return subprocess.run(
        [sys.executable, str(CHECK), *args],
        text=True, capture_output=True, env=env,
    )


def _project(tmp_path: pathlib.Path, project_version: str, lock_version: str):
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "packrat"\nversion = "{project_version}"\n'
    )
    (tmp_path / "uv.lock").write_text(
        f'[[package]]\nname = "packrat"\nversion = "{lock_version}"\n'
    )


def test_versions_match(tmp_path):
    _project(tmp_path, "1.2.3", "1.2.3")
    result = _run(tmp_path, "v1.2.3")
    assert result.returncode == 0, result.stderr


def test_lock_mismatch_fails(tmp_path):
    _project(tmp_path, "1.2.3", "1.2.2")
    result = _run(tmp_path)
    assert result.returncode != 0
    assert "uv.lock" in result.stderr


def test_stable_tag_mismatch_fails(tmp_path):
    _project(tmp_path, "1.2.3", "1.2.3")
    result = _run(tmp_path, "v1.2.4")
    assert result.returncode != 0


def test_prerelease_tag_matches_base(tmp_path):
    _project(tmp_path, "1.2.3", "1.2.3")
    result = _run(tmp_path, "v1.2.3.dev4+abc")
    assert result.returncode == 0, result.stderr


def test_pre_push_hook_syntax():
    result = subprocess.run(
        ["bash", "-n", str(ROOT / "dev" / "hooks" / "pre-push")],
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
