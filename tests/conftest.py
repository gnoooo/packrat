"""Global test safety: no real network access is allowed in the suite."""

from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Fail loudly if a test tries to open a real socket connection."""

    def _blocked(*_args, **_kwargs):
        raise AssertionError("network access is blocked in tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
