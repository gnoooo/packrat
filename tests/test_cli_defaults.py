"""Tests for --headers default resolution."""

from __future__ import annotations

import pathlib

from ytm_offline import cli


def test_headers_default_prefers_env(monkeypatch):
    monkeypatch.setenv("YTM_HEADERS", "/from/env.json")
    assert cli._headers_default() == "/from/env.json"


def test_headers_default_config_file(monkeypatch, tmp_path: pathlib.Path):
    monkeypatch.delenv("YTM_HEADERS", raising=False)
    cfg = tmp_path / "browser.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli, "HEADERS_CONFIG_PATH", str(cfg))
    assert cli._headers_default() == str(cfg)


def test_headers_default_none_when_missing(monkeypatch, tmp_path: pathlib.Path):
    monkeypatch.delenv("YTM_HEADERS", raising=False)
    monkeypatch.setattr(
        cli, "HEADERS_CONFIG_PATH", str(tmp_path / "missing.json")
    )
    assert cli._headers_default() is None
