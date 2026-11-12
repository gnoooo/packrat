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

from packrat.archive import Archive
from packrat import paths
from packrat.integrity import verify_audio
from packrat.download import estimate_track  # kept for test/back-compat patch points
from packrat.batch import exit_code_from_result, run_batch_download
from packrat.manifest import Manifest
from packrat.naming import SINGLES_DIR_DEFAULT, parse_playlist_id, track_path
from packrat.playlist import m3u8_entry, write_m3u8
from packrat.sizes import format_size, heuristic_size, parse_size

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
        "track_num": raw.get("trackNumber") if isinstance(raw.get("trackNumber"), int) else None,
        "release_type": raw.get("releaseType") or ("single" if not raw.get("album") else None),
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
    from packrat.cli_args import parse_cookies_from_browser

    spec = f"{browser_spec}:{profile}" if profile else browser_spec
    browser_name, parsed_profile, keyring, container = parse_cookies_from_browser(spec)
    jar = extract_cookies_from_browser(
        browser_name,
        profile=parsed_profile,
        keyring=keyring,
        container=container,
    )
    fd, path = tempfile.mkstemp(suffix=".txt", prefix="packrat_cookies_")
    os.close(fd)
    try:
        os.chmod(path, 0o600)
        jar.save(path, ignore_discard=True, ignore_expires=True)
        _COOKIE_TEMP_FILES.add(path)
        _install_cookie_signal_handlers()
        yield path
    finally:
        _cleanup_cookie_files()


def normalize_source(source: str) -> str:
    """Accept 'liked', 'playlist:<...>', or a pasted playlist link."""
    value = (source or "").strip()
    if value.lower() == "liked":
        return "liked"
    if value.startswith("playlist:"):
        return value
    if "list=" in value or value.startswith(("http://", "https://", "www.")):
        return f"playlist:{value}"
    return value


def resolve_tracks(
    source: str, headers_path: str, limit: int | None = None
) -> list[dict[str, Any]]:
    """Fetch track list from liked songs or a playlist (None = all)."""
    source = normalize_source(source)
    yt = YTMusic(headers_path)
    if source == "liked":
        data = yt.get_liked_songs(limit=limit)
        tracks = data.get("tracks", [])
    elif source.startswith("playlist:"):
        playlist_id = parse_playlist_id(source)
        data = yt.get_playlist(playlistId=playlist_id, limit=limit)
        tracks = data.get("tracks", [])
    else:
        raise ValueError(
            f"unknown source {source!r}; use 'liked' or 'playlist:<link or ID>'"
        )
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


def run_sync(
    args: argparse.Namespace,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    control: Any = None,
) -> int:
    """Sync liked songs or a playlist to disk."""
    args.source = normalize_source(args.source)
    browser_profile = getattr(args, "browser_profile", None)
    output_root = pathlib.Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)

    cookie_ctx = (
        browser_cookie_file(args.cookies_from_browser, browser_profile)
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
        manifest_path = output_root / "playlists" / f"{source_name}.manifest.json"
        m3u8_path = output_root / "playlists" / f"{source_name}.m3u8"
        failures_path = paths.FAILED_DIR / f"{source_name}.json"

        max_size = parse_size(args.max_size) if args.max_size else None

        # Fetch every track: --limit caps downloads after the archive/dedupe
        # step, so capping the API fetch would silently return archived-only
        # pages and skip genuinely new tracks.
        tracks = resolve_tracks(args.source, args.headers, None)
        if not tracks:
            print("No songs found to download.")
            logger.info("no tracks found for source")
            return 0

        logger.info("resolved %d tracks from %s", len(tracks), args.source)

        # The manifest is the source of truth for prior runs.  Only fall
        # back to the legacy archive when no manifest entry exists.
        previous: dict[str, dict[str, Any]] = {}
        if manifest_path.exists():
            try:
                previous = {
                    e["video_id"]: e
                    for e in Manifest.load(manifest_path).entries
                }
            except Exception as exc:
                logger.warning("could not read previous manifest: %s", exc)

        # Pre-flight: build manifest and estimate sizes for non-archived tracks.
        entries: list[dict[str, Any]] = []
        pending_entries: list[dict[str, Any]] = []
        total_est_bytes = 0
        for track in tracks:
            old = previous.get(track["video_id"])
            retry_existing = False
            if old and old.get("status") == "downloaded" and old.get("path"):
                check = verify_audio(
                    pathlib.Path(old["path"]),
                    expected_duration=old.get("duration"),
                )
                if check.ok:
                    entries.append(old)
                    continue
                old["status"] = "pending"
                old["error"] = f"existing file failed integrity: {check.reason}"
                retry_existing = True
            elif old and old.get("status") == "archived":
                entries.append(old)
                continue
            elif old:
                retry_existing = True
            if not retry_existing and track["video_id"] in archive:
                archive_path = track_path(
                    output_root,
                    track["artist"],
                    track["album"],
                    track["title"],
                    track.get("track_num"),
                    ext="m4a",
                    release_type=track.get("release_type"),
                    singles_dir=getattr(args, "singles_dir", SINGLES_DIR_DEFAULT),
                )
                if verify_audio(archive_path).ok:
                    entry = {
                        "video_id": track["video_id"],
                        "title": track["title"],
                        "artist": track["artist"],
                        "album": track["album"],
                        "track_num": track.get("track_num"),
                        "release_type": track.get("release_type"),
                        "duration": track.get("duration", 0),
                        "est_size": None,
                        "status": "archived",
                        "path": str(archive_path),
                        "error": None,
                        "source": args.source,
                        "singles_dir": getattr(args, "singles_dir", SINGLES_DIR_DEFAULT),
                    }
                    entries.append(entry)
                    continue
            # No per-track yt-dlp extraction here: that doubled the network
            # work (once for the estimate, again in download_track) and made
            # startup scale with the whole library.  Duration is already in
            # the YouTube Music response.
            est_size = heuristic_size(int(track.get("duration") or 0)) or None
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

        pending_size = sum(e.get("est_size") or 0 for e in pending_entries)
        print(f"pending tracks: {len(pending_entries)}")
        print(f"estimated pending size: {format_size(pending_size)}")
        logger.info("pending tracks: %d", len(pending_entries))
        logger.info("estimated pending size: %s", format_size(pending_size))

        manifest = Manifest()
        manifest.entries = entries
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest.write(manifest_path, args.manifest_format)

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
