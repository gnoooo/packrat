"""Central locations for Packrat's private state and configuration."""

from __future__ import annotations

import os
import pathlib

CONFIG_DIR = pathlib.Path.home() / ".config" / "packrat"
LOGS_DIR = CONFIG_DIR / "logs"
ARCHIVE_PATH = CONFIG_DIR / "archive"
RUNS_PATH = CONFIG_DIR / "runs.jsonl"
PROFILE_PATH = CONFIG_DIR / "profile.json"
FAILED_DIR = CONFIG_DIR / "failed"


def ensure_config_dir() -> pathlib.Path:
    """Create the config directory with owner-only permissions when possible."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass
    return CONFIG_DIR


def ensure_private_dir(path: pathlib.Path) -> pathlib.Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path
