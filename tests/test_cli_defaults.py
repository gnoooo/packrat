"""Tests for --headers default resolution."""

from __future__ import annotations

import pathlib

from packrat import cli_args


def test_headers_default_prefers_env(monkeypatch):
    monkeypatch.setenv("PACKRAT_HEADERS", "/from/env.json")
    assert cli_args._headers_default() == "/from/env.json"


def test_headers_default_config_file(monkeypatch, tmp_path: pathlib.Path):
    monkeypatch.delenv("PACKRAT_HEADERS", raising=False)
    cfg = tmp_path / "browser.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli_args, "HEADERS_CONFIG_PATH", str(cfg))
    monkeypatch.setattr(cli_args, "LEGACY_HEADERS_CONFIG_PATH", str(tmp_path / "old.json"))
    assert cli_args._headers_default() == str(cfg)


def test_headers_default_none_when_missing(monkeypatch, tmp_path: pathlib.Path):
    monkeypatch.delenv("PACKRAT_HEADERS", raising=False)
    monkeypatch.setattr(
        cli_args, "HEADERS_CONFIG_PATH", str(tmp_path / "missing.json")
    )
    monkeypatch.setattr(
        cli_args, "LEGACY_HEADERS_CONFIG_PATH", str(tmp_path / "old-missing.json")
    )
    assert cli_args._headers_default() is None
