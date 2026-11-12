"""Setup and YouTube-change diagnostics."""

from __future__ import annotations

import dataclasses
import importlib.metadata
import pathlib
import shutil
import sys

from packrat import cli_args, security


@dataclasses.dataclass(frozen=True)
class Check:
    status: str
    label: str
    detail: str


def _headers_path(headers: str | None) -> pathlib.Path:
    return pathlib.Path(
        headers or cli_args._headers_default() or cli_args.HEADERS_CONFIG_PATH
    ).expanduser()


def run_checks(headers: str | None = None, online: bool = False) -> list[Check]:
    checks: list[Check] = []

    def add(status: str, label: str, detail: str) -> None:
        checks.append(Check(status, label, detail))

    add("PASS", "python", sys.version.split()[0])
    for package in ("ytmusicapi", "yt-dlp", "mutagen"):
        try:
            add("PASS", package, importlib.metadata.version(package))
        except importlib.metadata.PackageNotFoundError:
            add("FAIL", package, "not installed")
    for tool in ("ffmpeg", "ffprobe", "deno"):
        path = shutil.which(tool)
        add("PASS" if path else "WARN", tool, path or "not found")

    path = _headers_path(headers)
    if path.exists():
        add("PASS", "login file", str(path))
        if not security.is_private(path):
            add("WARN", "login permissions", "chmod 600 this file")
    else:
        add("WARN", "login file", f"missing: {path}")

    if online:
        try:
            from ytmusicapi import YTMusic
            from packrat.download import estimate_track

            yt = YTMusic(str(path))
            liked = yt.get_liked_songs(limit=1)
            tracks = liked.get("tracks")
            if not isinstance(tracks, list):
                add("FAIL", "YouTube Music response", "get_liked_songs shape changed")
            else:
                add("PASS", "YouTube Music auth", f"{len(tracks)} liked track(s) visible")
                if tracks:
                    info = estimate_track(tracks[0]["videoId"])
                    if info:
                        add("PASS", "yt-dlp canary", f"resolved {tracks[0]['videoId']}")
                    else:
                        add("FAIL", "yt-dlp canary", "extraction returned no info")
        except Exception as exc:
            add("FAIL", "online canary", f"{exc.__class__.__name__}: {exc}")
    return checks
