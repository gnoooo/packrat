"""Batch sync implementation."""

from __future__ import annotations

import argparse
import atexit
import contextlib
import json
import logging
import os
import pathlib
import re
import signal
import tempfile
from collections.abc import Iterator
from typing import Any, Callable

from ytmusicapi import YTMusic
from yt_dlp.cookies import extract_cookies_from_browser

from ytm_offline.archive import Archive
from ytm_offline.batch import exit_code_from_result, run_batch_download
from ytm_offline.download import estimate_track
from ytm_offline.manifest import Manifest, atomic_write_text
from ytm_offline.naming import SINGLES_DIR_DEFAULT, parse_playlist_id
from ytm_offline.playlist import m3u8_entry, write_m3u8
from ytm_offline.sizes import estimate_size, format_size, parse_size

logger = logging.getLogger(__name__)


def _normalize_track(raw: dict[str, Any], index: int) -> dict[str, Any]:
    """Turn a ytmusicapi track dict into our internal shape."""
    artists = raw.get("artists") or []
    artist = artists[0]["name"] if artists else "Unknown Artist"
    album = (raw.get("album") or {}).get("name") or "Unknown Album"
    return {
        "video_id": raw["videoId"],
        "title": raw.get("title", "Unknown"),
        "artist": artist,
        "album": album,
        "track_num": raw.get("trackNumber") or raw.get("index") or index,
        "duration": raw.get("duration_seconds") or raw.get("lengthSeconds") or 0,
    }


_COOKIE_TEMP_FILES: set[str] = set()


def _cleanup_cookie_files() -> None:
    for path in list(_COOKIE_TEMP_FILES):
        try:
            os.unlink(path)
        except OSError:
            pass
        _COOKIE_TEMP_FILES.discard(path)


atexit.register(_cleanup_cookie_files)


_cookie_signal_installed = False
_original_sigint: Callable[[int, Any], Any] | int | None = None
_original_sigterm: Callable[[int, Any], Any] | int | None = None


def _install_cookie_signal_handlers() -> None:
    global _cookie_signal_installed, _original_sigint, _original_sigterm
    if _cookie_signal_installed:
        return

    def _handler(signum: int, frame: Any) -> None:
        _cleanup_cookie_files()
        old = _original_sigint if signum == signal.SIGINT else _original_sigterm
        if callable(old):
            old(signum, frame)
        elif old is not None and old != signal.SIG_DFL:
            signal.signal(signum, old)
            os.kill(os.getpid(), signum)
        else:
            os.kill(os.getpid(), signum)

    _original_sigint = signal.signal(signal.SIGINT, _handler)
    _original_sigterm = signal.signal(signal.SIGTERM, _handler)
    _cookie_signal_installed = True


@contextlib.contextmanager
def browser_cookie_file(browser_spec: str, profile: str | None = None) -> Iterator[str]:
    """Extract cookies to a 0600 temp file and guarantee cleanup on exit/crash."""
    from ytm_offline.cli_args import parse_cookies_from_browser

    spec = f"{browser_spec}:{profile}" if profile else browser_spec
    browser_name, parsed_profile, keyring, container = parse_cookies_from_browser(spec)
    jar = extract_cookies_from_browser(
        browser_name,
        profile=parsed_profile,
        keyring=keyring,
        container=container,
    )
    fd, path = tempfile.mkstemp(
        suffix=".txt", prefix="ytm_cookies_", dir="/tmp/opencode"
    )
    os.close(fd)
    try:
        os.chmod(path, 0o600)
        jar.save(path, ignore_discard=True, ignore_expires=True)
        _COOKIE_TEMP_FILES.add(path)
        _install_cookie_signal_handlers()
        yield path
    finally:
        _cleanup_cookie_files()


def resolve_tracks(source: str, headers_path: str, limit: int) -> list[dict[str, Any]]:
    """Fetch track list from liked songs or a playlist."""
    yt = YTMusic(headers_path)
    if source == "liked":
        data = yt.get_liked_songs(limit=limit)
        tracks = data.get("tracks", [])
    elif source.startswith("playlist:"):
        playlist_id = parse_playlist_id(source)
        data = yt.get_playlist(playlistId=playlist_id, limit=limit)
        tracks = data.get("tracks", [])
    else:
        raise ValueError(f"unknown source: {source}")
    tracks = tracks[:limit]
    return [_normalize_track(t, i + 1) for i, t in enumerate(tracks)]


def _source_name(source: str) -> str:
    if source == "liked":
        return "liked"
    if source.startswith("playlist:"):
        return f"playlist_{parse_playlist_id(source)}"
    return re.sub(r"[^\w.-]+", "_", source)


def _write_failures(path: pathlib.Path, video_ids: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(video_ids, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _m3u8_from_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        m3u8_entry(e)
        for e in entries
        if e.get("status") == "downloaded" and e.get("path")
    ]


def run_retry(
    args: argparse.Namespace,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    control: Any = None,
) -> int:
    """Retry failed/pending tracks recorded in a manifest."""
    manifest_path = pathlib.Path(args.manifest)
    if not manifest_path.exists():
        logger.error("manifest not found: %s", manifest_path)
        return 1

    cookie_ctx = (
        browser_cookie_file(args.cookies_from_browser, args.browser_profile)
        if args.cookies_from_browser and not args.cookies
        else contextlib.nullcontext(args.cookies)
    )
    with cookie_ctx as cookie_path:
        if cookie_path and args.cookies_from_browser and not args.cookies:
            logger.info("caching cookies from %s", args.cookies_from_browser)
            args.cookies = cookie_path
            args.cookies_from_browser = None

        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = data.get("entries", [])
        output_root = pathlib.Path(args.output)
        output_root.mkdir(parents=True, exist_ok=True)
        archive = Archive(args.archive)
        max_size = parse_size(args.max_size) if args.max_size else None

        source_name = manifest_path.stem.replace(".manifest", "").replace(".failed", "")
        m3u8_path = output_root / "playlists" / f"{source_name}.m3u8"
        failures_path = output_root / "playlists" / f"{source_name}.failed.json"

        to_retry = [
            e for e in entries if e["status"] not in ("downloaded", "archived")
        ]
        if not to_retry:
            logger.info("no retryable tracks in manifest")
            return 0

        logger.info("retrying %d tracks", len(to_retry))

        def save_callback(current_entries: list[dict[str, Any]]) -> None:
            atomic_write_text(
                manifest_path,
                json.dumps({"entries": current_entries}, indent=2),
            )
            write_m3u8(m3u8_path, _m3u8_from_entries(current_entries))

        summary = run_batch_download(
            to_retry,
            output_root=output_root,
            save_callback=save_callback,
            cookies=args.cookies,
            max_size=max_size,
            delay=args.delay,
            event_callback=event_callback,
            control=control,
            archive=archive,
            mode="retry",
        )

        failures = [e["video_id"] for e in to_retry if e.get("status") == "failed"]
        _write_failures(failures_path, failures)

        logger.info("retry report:")
        for status in Manifest.VALID_STATUSES:
            count = sum(1 for e in to_retry if e.get("status") == status)
            if count:
                logger.info("  %s: %d", status, count)
        return exit_code_from_result(summary)


def run_sync(
    args: argparse.Namespace,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    control: Any = None,
) -> int:
    """Sync liked songs or a playlist to disk."""
    output_root = pathlib.Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)

    cookie_ctx = (
        browser_cookie_file(args.cookies_from_browser, args.browser_profile)
        if args.cookies_from_browser and not args.cookies
        else contextlib.nullcontext(args.cookies)
    )
    with cookie_ctx as cookie_path:
        if cookie_path and args.cookies_from_browser and not args.cookies:
            logger.info("caching cookies from %s", args.cookies_from_browser)
            args.cookies = cookie_path
            args.cookies_from_browser = None

        archive = Archive(args.archive)
        source_name = _source_name(args.source)
        manifest_path = output_root / "playlists" / f"{source_name}.manifest.{args.manifest_format}"
        m3u8_path = output_root / "playlists" / f"{source_name}.m3u8"
        failures_path = output_root / "playlists" / f"{source_name}.failed.json"

        max_size = parse_size(args.max_size) if args.max_size else None

        tracks = resolve_tracks(args.source, args.headers, args.limit)
        if not tracks:
            logger.info("no tracks found for source")
            return 0

        logger.info("resolved %d tracks from %s", len(tracks), args.source)

        # Pre-flight: build manifest and estimate sizes for non-archived tracks.
        entries: list[dict[str, Any]] = []
        pending_entries: list[dict[str, Any]] = []
        total_est_bytes = 0
        for track in tracks:
            if track["video_id"] in archive:
                entry = {
                    "video_id": track["video_id"],
                    "title": track["title"],
                    "artist": track["artist"],
                    "album": track["album"],
                    "duration": track.get("duration", 0),
                    "est_size": None,
                    "status": "archived",
                    "path": None,
                    "error": None,
                    "source": args.source,
                    "singles_dir": getattr(args, "singles_dir", SINGLES_DIR_DEFAULT),
                }
                entries.append(entry)
                continue
            info = estimate_track(
                track["video_id"],
                cookies=args.cookies,
                cookies_from_browser=args.cookies_from_browser,
                browser_profile=args.browser_profile,
            )
            est_size = estimate_size(info)
            entry = {
                "video_id": track["video_id"],
                "title": track["title"],
                "artist": track["artist"],
                "album": track["album"],
                "duration": track.get("duration", 0),
                "est_size": est_size,
                "status": "pending",
                "path": None,
                "error": None,
                "source": args.source,
                "singles_dir": getattr(args, "singles_dir", SINGLES_DIR_DEFAULT),
            }
            entries.append(entry)
            pending_entries.append(entry)
            if est_size:
                total_est_bytes += est_size

        limit = getattr(args, "limit", None)
        total_pending = len(pending_entries)
        if limit and total_pending > limit:
            pending_entries = pending_entries[:limit]
            total_est_bytes = sum(e.get("est_size") or 0 for e in pending_entries)
            logger.info("limit applied: %d of %d new tracks", limit, total_pending)

        pending_size = sum(
            e.get("est_size") or 0 for e in entries if e["status"] == "pending"
        )
        print(f"pending tracks: {len(pending_entries)}")
        print(f"estimated pending size: {format_size(pending_size)}")
        logger.info("pending tracks: %d", len(pending_entries))
        logger.info("estimated pending size: %s", format_size(pending_size))

        manifest = Manifest()
        manifest.entries = entries
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest.write(manifest_path, args.manifest_format)

        if event_callback:
            event_callback(
                {
                    "type": "start",
                    "payload": {
                        "mode": args.source,
                        "total": len(entries),
                        "total_est_bytes": total_est_bytes,
                        "max_size_bytes": max_size,
                    },
                }
            )

        if args.dry_run:
            print(f"manifest: {manifest_path}")
            print("dry-run: manifest written, no downloads.")
            logger.info("dry-run manifest: %s", manifest_path)
            return 0

        def save_callback(current_entries: list[dict[str, Any]]) -> None:
            # current_entries is the pending subset; the original dicts are
            # mutated in place so manifest.entries already reflects updates.
            manifest.write(manifest_path, args.manifest_format)
            write_m3u8(m3u8_path, _m3u8_from_entries(manifest.entries))
            _write_failures(
                failures_path,
                [e["video_id"] for e in manifest.entries if e.get("status") == "failed"],
            )

        summary = run_batch_download(
            pending_entries,
            output_root=output_root,
            save_callback=save_callback,
            cookies=args.cookies,
            max_size=max_size,
            delay=args.delay,
            event_callback=event_callback,
            control=control,
            archive=archive,
            mode=args.source,
        )

        if summary["stopped"]:
            for e in entries:
                if e.get("status") == "pending":
                    e["error"] = "batch stopped after 3 consecutive retryable errors"
            manifest.write(manifest_path, args.manifest_format)

        logger.info("sync report:")
        for status, count in manifest.counts().items():
            if count:
                logger.info("  %s: %d", status, count)
        print(f"manifest: {manifest_path}")
        logger.info("manifest: %s", manifest_path)
        return exit_code_from_result(summary)
