"""Release checking and self-update for the standard installer layout."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import re
import tempfile
import urllib.request
from typing import Any

from packaging.version import InvalidVersion, Version

from packrat import __version__

REPO = "gnoooo/packrat"
API = "https://api.github.com"
DATA_HOME = pathlib.Path(
    os.environ.get("XDG_DATA_HOME", pathlib.Path.home() / ".local" / "share")
).expanduser()
BIN_HOME = pathlib.Path(
    os.environ.get("XDG_BIN_HOME", pathlib.Path.home() / ".local" / "bin")
).expanduser()
INSTALL_ROOT = DATA_HOME / "packrat"
BIN_DIR = BIN_HOME


class UpgradeError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class Release:
    version: str
    tag: str
    wheel_url: str
    wheel_name: str
    checksums_url: str
    requirements_url: str | None = None
    requirements_name: str = "requirements.lock"
    prerelease: bool = False


def _parse_version(value: str) -> Version:
    try:
        return Version(value.removeprefix("v"))
    except InvalidVersion as exc:
        raise UpgradeError(f"invalid release version: {value!r}") from exc


def compare_versions(a: str, b: str) -> int:
    aa, bb = _parse_version(a), _parse_version(b)
    return (aa > bb) - (aa < bb)


def _wheel_version(name: str) -> str:
    match = re.match(r"^packrat-(.+?)-py3-", name)
    return match.group(1) if match else ""


def _get_json(url: str) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"packrat/{__version__}",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


def latest_release(include_prerelease: bool = False) -> Release:
    if include_prerelease:
        releases = _get_json(f"{API}/repos/{REPO}/releases?per_page=20")
    else:
        releases = [_get_json(f"{API}/repos/{REPO}/releases/latest")]
    for data in releases:
        if data.get("draft"):
            continue
        tag = str(data.get("tag_name", ""))
        assets = {a.get("name"): a.get("browser_download_url") for a in data.get("assets", [])}
        wheels = [name for name in assets if name and name.endswith(".whl")]
        if not wheels or "SHA256SUMS" not in assets:
            continue
        target = _parse_version(tag.removeprefix("v"))
        parsed = []
        for name in wheels:
            version = _wheel_version(name)
            try:
                parsed.append((Version(version), name))
            except InvalidVersion:
                continue
        exact = [name for version, name in parsed if version == target]
        if exact:
            wheel_name = exact[0]
        elif parsed:
            wheel_name = max(parsed, key=lambda item: item[0])[1]
        else:
            raise UpgradeError(f"release {tag} has no PEP 440 wheel")
        return Release(
            version=tag.removeprefix("v"),
            tag=tag,
            wheel_url=assets[wheel_name],
            wheel_name=wheel_name,
            checksums_url=assets["SHA256SUMS"],
            requirements_url=assets.get("requirements.lock"),
            prerelease=bool(data.get("prerelease")),
        )
    raise UpgradeError("no usable release with a wheel and SHA256SUMS was found")


def standard_install() -> tuple[pathlib.Path, pathlib.Path]:
    prefix = pathlib.Path(sys.prefix).resolve()
    expected_venv = (INSTALL_ROOT / "venv").resolve()
    if prefix != expected_venv:
        raise UpgradeError(
            "self-update is only supported for the standard installer layout "
            f"({expected_venv}); current prefix is {prefix}"
        )
    return INSTALL_ROOT.resolve(), BIN_DIR.resolve()


def _download(url: str, target: pathlib.Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": f"packrat/{__version__}"})
    with urllib.request.urlopen(req, timeout=60) as response, target.open("wb") as out:
        shutil.copyfileobj(response, out)


def _expected_hash(checksums: pathlib.Path, wheel_name: str) -> str:
    for line in checksums.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == wheel_name:
            return parts[0]
    raise UpgradeError(f"wheel {wheel_name} not found in SHA256SUMS")


def _sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _attest(wheel: pathlib.Path, tag: str) -> None:
    gh = shutil.which("gh")
    if not gh:
        raise UpgradeError(
            "GitHub CLI (gh) is required to verify the release attestation; "
            "install gh or use the install script"
        )
    result = subprocess.run(
        [
            gh, "attestation", "verify", str(wheel), "--repo", REPO,
            "--signer-workflow", f"{REPO}/.github/workflows/release.yml",
            "--source-ref", f"refs/tags/{tag}",
        ],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise UpgradeError(
            "release attestation verification failed:\n"
            + (result.stderr or result.stdout).strip()
        )


def _install_wheel(
    wheel: pathlib.Path,
    venv: pathlib.Path,
    requirements: pathlib.Path | None = None,
) -> None:
    base_python = getattr(sys, "_base_executable", None) or sys.executable
    uv = shutil.which("uv")
    if uv:
        subprocess.run([uv, "venv", "--python", base_python, str(venv)], check=True)
        subprocess.run(
            [
                uv, "pip", "install", "--python", str(venv / "bin" / "python"),
                "--no-deps", str(wheel),
            ],
            check=True,
        )
        if requirements is not None:
            subprocess.run(
                [
                    uv, "pip", "install", "--python", str(venv / "bin" / "python"),
                    "--require-hashes", "-r", str(requirements),
                ],
                check=True,
            )
    else:
        subprocess.run([base_python, "-m", "venv", str(venv)], check=True)
        subprocess.run(
            [str(venv / "bin" / "python"), "-m", "pip", "install", "--no-deps", str(wheel)],
            check=True,
        )
        if requirements is not None:
            subprocess.run(
                [
                    str(venv / "bin" / "python"), "-m", "pip", "install",
                    "--require-hashes", "-r", str(requirements),
                ],
                check=True,
            )


def _replace_install(
    wheel: pathlib.Path,
    root: pathlib.Path,
    bin_dir: pathlib.Path,
    target_version: str,
    requirements: pathlib.Path | None = None,
) -> None:
    venv = root / "venv"
    backup = root / "venv.upgrade-backup"
    if backup.exists():
        if not venv.exists():
            backup.rename(venv)
        else:
            raise UpgradeError(
                f"stale upgrade backup exists: {backup}; inspect it before retrying"
            )
    if venv.exists():
        venv.rename(backup)
    try:
        _install_wheel(wheel, venv, requirements)
        out = subprocess.run(
            [str(venv / "bin" / "packrat"), "--version"],
            check=True, text=True, capture_output=True,
        ).stdout.strip()
        got = out.split()[-1] if out else ""
        if _parse_version(got) != _parse_version(target_version):
            raise UpgradeError(
                f"new install reports {got or 'no version'}, expected {target_version}"
            )
        link = bin_dir / "packrat"
        link.parent.mkdir(parents=True, exist_ok=True)
        tmp_link = bin_dir / ".packrat.upgrade-link"
        if tmp_link.exists() or tmp_link.is_symlink():
            tmp_link.unlink()
        tmp_link.symlink_to(venv / "bin" / "packrat")
        os.replace(tmp_link, link)
    except BaseException:
        shutil.rmtree(venv, ignore_errors=True)
        if backup.exists():
            backup.rename(venv)
        raise
    else:
        shutil.rmtree(backup, ignore_errors=True)


def _locked(path: pathlib.Path):
    import fcntl

    fh = path.open("w")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        raise UpgradeError("another Packrat upgrade is already running")
    return fh


def run_upgrade(
    *,
    check_only: bool = False,
    include_prerelease: bool = False,
    yes: bool = False,
    dry_run: bool = False,
) -> int:
    release = latest_release(include_prerelease)
    print(f"current: {__version__}")
    print(f"latest:  {release.version} ({release.tag})")
    if compare_versions(release.version, __version__) <= 0:
        print("Packrat is up to date.")
        return 0
    if check_only:
        print(f"update available: {release.version}")
        return 0

    if dry_run:
        print(
            f"dry-run: would download {release.wheel_name} and replace "
            f"{INSTALL_ROOT / 'venv'}"
        )
        return 0
    root, bin_dir = standard_install()
    if not yes:
        if not sys.stdin.isatty():
            raise UpgradeError("pass --yes to upgrade without an interactive terminal")
        answer = input(f"Upgrade Packrat to {release.version}? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Not upgrading.")
            return 2

    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="packrat-upgrade-") as tmp:
        tmpdir = pathlib.Path(tmp)
        wheel = tmpdir / release.wheel_name
        checksums = tmpdir / "SHA256SUMS"
        _download(release.wheel_url, wheel)
        _download(release.checksums_url, checksums)
        requirements = None
        if release.requirements_url:
            requirements = tmpdir / release.requirements_name
            _download(release.requirements_url, requirements)
        expected = _expected_hash(checksums, release.wheel_name)
        actual = _sha256(wheel)
        if actual != expected:
            raise UpgradeError(f"wheel checksum mismatch: {actual} != {expected}")
        print(f"checksum: {actual}")
        if requirements is not None:
            req_hash = _expected_hash(checksums, release.requirements_name)
            if _sha256(requirements) != req_hash:
                raise UpgradeError("requirements.lock checksum mismatch")
        _attest(wheel, release.tag)
        if requirements is not None:
            _attest(requirements, release.tag)
        print("attestation: verified")

        lock = _locked(root / ".install.lock")
        try:
            _replace_install(wheel, root, bin_dir, release.version, requirements)
        finally:
            lock.close()
    print(f"upgraded: {__version__} -> {release.version}")
    print("restart Packrat to use the new version")
    return 0
