"""Global test safety: no real network access is allowed in the suite."""

from __future__ import annotations

import pathlib
import socket

import pytest


@pytest.fixture(autouse=True, scope="session")
def _dummy_headers_file():
    """Tests pass --headers /tmp/x; make it a harmless empty JSON file."""
    path = pathlib.Path("/tmp/x")
    if not path.exists():
        path.write_text("{}", encoding="utf-8")


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Fail loudly if a test tries to open a real socket connection."""

    def _blocked(*_args, **_kwargs):
        raise AssertionError("network access is blocked in tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


@pytest.fixture(autouse=True)
def isolate_user_state(tmp_path, monkeypatch):
    """Keep config/state writes inside pytest's temp directory."""
    from packrat import logging_config, paths, runs, tui

    config = tmp_path / "config"
    monkeypatch.setattr(paths, "CONFIG_DIR", config)
    monkeypatch.setattr(paths, "LOGS_DIR", config / "logs")
    monkeypatch.setattr(paths, "ARCHIVE_PATH", config / "archive")
    monkeypatch.setattr(paths, "RUNS_PATH", config / "runs.jsonl")
    monkeypatch.setattr(paths, "PROFILE_PATH", config / "profile.json")
    monkeypatch.setattr(paths, "FAILED_DIR", config / "failed")
    monkeypatch.setattr(runs, "RUNS_PATH", config / "runs.jsonl")
    monkeypatch.setattr(logging_config, "LOG_DIR", config / "logs")
    monkeypatch.setattr(tui, "LOG_DIR", config / "logs")
    return config
