"""Single-track download via the yt-dlp Python API."""

from __future__ import annotations

import dataclasses
import logging
import pathlib
import re
import time
from typing import Any, Callable

import yt_dlp

from ytm_offline.logging_config import redact
from ytm_offline.naming import SINGLES_DIR_DEFAULT, track_path

logger = logging.getLogger(__name__)

_RETRYABLE_PATTERNS = (
    "http error 403",
    "http error 429",
    "too many requests",
    "sign in to confirm",
    "bot",
)


def _is_retryable(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(p in msg for p in _RETRYABLE_PATTERNS)


class _YtdlpLogger:
    def __init__(self) -> None:
        self.player_client: str | None = None

    def debug(self, msg: str) -> None:
        m = redact(msg)
        if match := re.search(r"Downloading ([^ ]+) player API JSON", m):
            self.player_client = match.group(1)
        logger.debug(m)

    def info(self, msg: str) -> None:
        logger.info(redact(msg))

    def warning(self, msg: str) -> None:
        logger.warning(redact(msg))

    def error(self, msg: str) -> None:
        logger.error(redact(msg))


class AbortTrack(Exception):
    """Raised when the user asks to skip the current download."""

    pass


@dataclasses.dataclass
class DownloadResult:
    status: str  # downloaded, skipped_size, failed, skipped
    filepath: pathlib.Path | None = None
    info: dict[str, Any] | None = None
    error: str | None = None
    retryable: bool = False
    player_client: str | None = None
    video_id: str | None = None
    title: str = ""
    bytes: int = 0


def _browser_tuple(
    browser_spec: str | None, profile: str | None
) -> tuple[str, str | None, str | None, str | None] | None:
    if not browser_spec:
        return None
    return (browser_spec.lower(), profile, None, None)


def _build_ydl_opts(
    output_path: pathlib.Path,
    cookies: str | None,
    cookies_from_browser: str | None,
    browser_profile: str | None,
    logger_obj: _YtdlpLogger,
    progress_hook: Callable[[dict[str, Any]], None] | None,
) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "format": "bestaudio[ext=m4a]/bestaudio",
        "outtmpl": str(output_path),
        "writethumbnail": True,
        "postprocessors": [
            {"key": "FFmpegMetadata", "add_metadata": True},
            {"key": "EmbedThumbnail"},
        ],
        "js_runtimes": {"deno": {}},
        "quiet": True,
        "progress": False,
        "logger": logger_obj,
        "noplaylist": True,
    }
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]
    if cookies:
        opts["cookiefile"] = cookies
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = _browser_tuple(cookies_from_browser, browser_profile)
    return opts


def _build_extract_opts(
    cookies: str | None,
    cookies_from_browser: str | None,
    browser_profile: str | None,
    logger_obj: _YtdlpLogger,
) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "format": "bestaudio[ext=m4a]/bestaudio",
        "js_runtimes": {"deno": {}},
        "quiet": True,
        "logger": logger_obj,
        "noplaylist": True,
    }
    if cookies:
        opts["cookiefile"] = cookies
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = _browser_tuple(cookies_from_browser, browser_profile)
    return opts


def estimate_track(
    video_id: str,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
    browser_profile: str | None = None,
) -> dict[str, Any] | None:
    """Return yt-dlp info for a track without downloading, or None on error."""
    url = f"https://music.youtube.com/watch?v={video_id}"
    ytdl_logger = _YtdlpLogger()
    opts = _build_extract_opts(cookies, cookies_from_browser, browser_profile, ytdl_logger)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception:
        return None


def _estimate_size(info: dict[str, Any] | None) -> int | None:
    if not info:
        return None
    if info.get("filesize_approx"):
        return int(info["filesize_approx"])
    requested = info.get("requested_formats") or []
    if requested:
        return int(sum(f.get("filesize_approx") or 0 for f in requested))
    return None


def _progress_hook(
    control: Any,
    event_callback: Callable[[dict[str, Any]], None] | None,
    d: dict[str, Any],
) -> None:
    if control and control.should_skip():
        raise AbortTrack("user skipped track")
    if event_callback is None:
        return
    status = d.get("status", "")
    if status == "downloading":
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        downloaded = d.get("downloaded_bytes", 0)
        percent = 0.0
        if total:
            percent = min(100.0, downloaded / total * 100)
        event_callback(
            {
                "type": "track_progress",
                "payload": {
                    "stage": "downloading",
                    "percent": percent,
                    "downloaded_bytes": downloaded,
                    "total_bytes": total,
                    "speed": d.get("speed", ""),
                    "eta_seconds": d.get("eta"),
                },
            }
        )
    elif status == "finished":
        event_callback(
            {
                "type": "track_progress",
                "payload": {
                    "stage": "converting",
                    "percent": 100.0,
                    "downloaded_bytes": d.get("total_bytes", 0),
                    "total_bytes": d.get("total_bytes", 0),
                    "speed": "",
                    "eta_seconds": 0,
                },
            }
        )


def download_track(
    track: dict[str, Any],
    output_root: pathlib.Path,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
    browser_profile: str | None = None,
    max_size: int | None = None,
    max_retries: int = 3,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    control: Any = None,
) -> DownloadResult:
    """Download one track, retrying only on 403/429/bot-check errors."""
    video_id = track["video_id"]
    url = f"https://music.youtube.com/watch?v={video_id}"
    output_path = track_path(
        output_root,
        track.get("artist", ""),
        track.get("album", ""),
        track.get("title", ""),
        track.get("track_num"),
        ext="m4a",
        release_type=track.get("release_type"),
        singles_dir=track.get("singles_dir", SINGLES_DIR_DEFAULT),
    )

    def emit_start(stage: str = "resolving") -> None:
        if event_callback:
            event_callback(
                {
                    "type": "track_start",
                    "payload": {
                        "video_id": video_id,
                        "title": track.get("title", ""),
                        "artist": track.get("artist", ""),
                        "album": track.get("album", ""),
                        "source": track.get("source", ""),
                        "stage": stage,
                    },
                }
            )

    def emit_done(**kwargs: Any) -> DownloadResult:
        if event_callback:
            event_callback({"type": "track_done", "payload": kwargs})
        return DownloadResult(**kwargs)

    ytdl_logger = _YtdlpLogger()
    progress_hook = (
        lambda d: _progress_hook(control, event_callback, d)
        if control or event_callback
        else None
    )
    ydl_opts = _build_ydl_opts(
        output_path, cookies, cookies_from_browser, browser_profile, ytdl_logger, progress_hook
    )

    emit_start("resolving")
    for attempt in range(1, max_retries + 1):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                est_size = _estimate_size(info)
                if control and control.should_skip():
                    return emit_done(
                        status="skipped",
                        video_id=video_id,
                        title=track.get("title", ""),
                        error="skipped by user",
                        retryable=False,
                    )
                if max_size and est_size and est_size > max_size:
                    return emit_done(
                        status="skipped_size",
                        video_id=video_id,
                        title=track.get("title", ""),
                        info=info,
                        player_client=ytdl_logger.player_client,
                    )
                emit_start("downloading")
                ydl.process_ie_result(info, download=True)
                downloaded = pathlib.Path(ydl.prepare_filename(info))
                if not downloaded.exists():
                    return emit_done(
                        status="failed",
                        video_id=video_id,
                        title=track.get("title", ""),
                        error=f"expected file not found: {downloaded}",
                        retryable=False,
                        player_client=ytdl_logger.player_client,
                    )
                return emit_done(
                    status="downloaded",
                    video_id=video_id,
                    title=track.get("title", ""),
                    filepath=downloaded,
                    bytes=est_size or 0,
                    info=info,
                    player_client=ytdl_logger.player_client,
                )
        except AbortTrack:
            return emit_done(
                status="skipped",
                video_id=video_id,
                title=track.get("title", ""),
                error="skipped by user",
                retryable=False,
            )
        except yt_dlp.utils.DownloadError as exc:
            retryable = _is_retryable(exc)
            if retryable and attempt < max_retries:
                if event_callback:
                    event_callback(
                        {
                            "type": "track_progress",
                            "payload": {"stage": f"retry {attempt}/{max_retries}"},
                        }
                    )
                time.sleep(2**attempt)
                continue
            return emit_done(
                status="failed",
                video_id=video_id,
                title=track.get("title", ""),
                error=redact(exc.msg),
                retryable=retryable,
                player_client=ytdl_logger.player_client,
            )
        except Exception as exc:
            return emit_done(
                status="failed",
                video_id=video_id,
                title=track.get("title", ""),
                error=redact(exc),
                retryable=False,
                player_client=ytdl_logger.player_client,
            )

    return emit_done(
        status="failed",
        video_id=video_id,
        title=track.get("title", ""),
        error="exhausted retries",
        retryable=True,
        player_client=ytdl_logger.player_client,
    )
