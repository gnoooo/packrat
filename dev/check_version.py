#!/usr/bin/env python3
"""Validate pyproject/uv.lock/tag version consistency before pushing a tag."""

from __future__ import annotations

import os
import pathlib
import re
import sys
import tomllib

ROOT = pathlib.Path(
    os.environ.get("PACKRAT_ROOT", pathlib.Path(__file__).resolve().parents[1])
).resolve()


def fail(message: str) -> None:
    print(f"release-check: {message}", file=sys.stderr)
    raise SystemExit(1)


def read_project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def read_lock_version() -> str:
    lock = ROOT / "uv.lock"
    if not lock.exists():
        fail("uv.lock is missing")
    with lock.open("rb") as handle:
        data = tomllib.load(handle)
    for package in data.get("package", []):
        if package.get("name") == "packrat":
            return str(package.get("version", ""))
    fail("uv.lock has no packrat package")
    return ""


def check_tag(tag: str, base: str) -> None:
    version = tag.removeprefix("v")
    if not re.match(r"^\d+\.\d+\.\d+", version):
        fail(f"tag {tag!r} is not a PEP 440-style version")
    if version == base:
        return
    prerelease = re.match(
        rf"^{re.escape(base)}(\.dev\d+.*|rc\d+.*|a\d+.*|b\d+.*|-.*)$",
        version,
    )
    if not prerelease:
        fail(f"tag {tag!r} does not match pyproject version {base!r}")


def main(argv: list[str]) -> int:
    base = read_project_version()
    locked = read_lock_version()
    if base != locked:
        fail(f"pyproject version {base!r} != uv.lock packrat {locked!r}")
    if len(argv) > 1:
        fail("usage: check_version.py [TAG]")
    if len(argv) == 1:
        check_tag(argv[0], base)
    print(f"release-check: {base} is consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
