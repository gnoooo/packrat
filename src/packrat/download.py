"""Single-track download via the yt-dlp Python API."""

from __future__ import annotations

import dataclasses
import logging
import pathlib
import re
import time
from typing import Any, Callable

import yt_dlp

from packrat.integrity import verify_audio
from packrat.logging_config import redact
from packrat.sizes import estimate_size
from packrat.naming import SINGLES_DIR_DEFAULT, collision_path, track_path
from packrat.song_meta import apply_song_metadata, metadata_from_track

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


def _friendly_download_error(message: str) -> str:
    """Short, actionable error instead of a full yt-dlp help paragraph."""
    low = str(message).lower()
    if "sign in to confirm your age" in low:
        return ("age-restricted: yt-dlp could not authenticate; use "
                "--cookies-from-browser <browser> (the account must be age-verified)")
    if "sign in to confirm" in low or "not a bot" in low:
        return ("YouTube requested a sign-in/bot check; use "
                "--cookies-from-browser <browser>")
    return redact(message)


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
    from packrat.cli_args import parse_cookies_from_browser

    spec = f"{browser_spec}:{profile}" if profile else browser_spec
    return parse_cookies_from_browser(spec)


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


_AUDIO_EXTS = {".m4a", ".mp3", ".webm", ".ogg", ".flac", ".wav", ".opus"}


def _final_filepath(
    info: dict[str, Any], ydl: yt_dlp.YoutubeDL
) -> pathlib.Path:
    """Return the actual downloaded file path after yt-dlp postprocessors.

    Falls back to scanning the output directory for an audio file with the
    expected stem when yt-dlp's metadata keys are missing.
    """
    candidate = (
        info.get("filepath")
        or info.get("_filename")
        or ydl.prepare_filename(info)
    )
    path = pathlib.Path(candidate)
    if path.exists():
        return path
    # Postprocessors may have changed the extension; look for a likely audio file.
    if path.parent.exists():
        for ext in _AUDIO_EXTS:
            maybe = path.with_suffix(ext)
            if maybe.exists():
                return maybe
        for maybe in sorted(path.parent.glob(path.stem + ".*")):
            if maybe.suffix.lower() in _AUDIO_EXTS:
                return maybe
    return path


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


def _quarantine(path: pathlib.Path) -> None:
    """Move a corrupt/partial file aside so yt-dlp cannot silently reuse it."""
    try:
        target = path.with_name(path.name + ".corrupt")
        n = 1
        while target.exists():
            target = path.with_name(f"{path.name}.corrupt.{n}")
            n += 1
        path.replace(target)
        logger.warning("quarantined invalid audio file: %s", target)
    except OSError:
        logger.debug("could not quarantine %s", path, exc_info=True)


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

    def tag_existing(path: pathlib.Path) -> None:
        try:
            apply_song_metadata(path, metadata_from_track(track))
        except Exception:
            logger.warning("could not rewrite tags for %s", path, exc_info=True)

    # Idempotency: a valid file already at the destination is the result.
    # Compare duration when available so same-title/different-video collisions
    # do not silently reuse the wrong audio.
    expected_duration = track.get("duration")
    if output_path.exists():
        existing = verify_audio(output_path)
        if existing.ok:
            duration_mismatch = (
                isinstance(expected_duration, (int, float))
                and expected_duration > 0
                and existing.duration is not None
                and abs(float(existing.duration) - float(expected_duration)) > 3.0
            )
            if not duration_mismatch:
                tag_existing(output_path)
                return emit_done(
                    status="downloaded",
                    video_id=video_id,
                    title=track.get("title", ""),
                    filepath=output_path,
                    bytes=existing.size,
                )
            output_path = collision_path(output_path, video_id)
            if output_path.exists():
                alternate = verify_audio(output_path)
                if alternate.ok:
                    tag_existing(output_path)
                    return emit_done(
                        status="downloaded",
                        video_id=video_id,
                        title=track.get("title", ""),
                        filepath=output_path,
                        bytes=alternate.size,
                    )
                _quarantine(output_path)
        else:
            _quarantine(output_path)

    ytdl_logger = _YtdlpLogger()
    progress_hook = (
        (lambda d: _progress_hook(control, event_callback, d))
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
                est_size = estimate_size(info)
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
                downloaded = _final_filepath(info, ydl)
                if not downloaded.exists():
                    return emit_done(
                        status="failed",
                        video_id=video_id,
                        title=track.get("title", ""),
                        error=f"expected file not found: {downloaded}",
                        retryable=False,
                        player_client=ytdl_logger.player_client,
                    )
                try:
                    actual_bytes = downloaded.stat().st_size
                except OSError:
                    actual_bytes = est_size or 0
                check = verify_audio(downloaded, est_size)
                if not check.ok:
                    _quarantine(downloaded)
                    if attempt < max_retries:
                        continue
                    return emit_done(
                        status="failed",
                        video_id=video_id,
                        title=track.get("title", ""),
                        error=f"audio integrity check failed: {check.reason}",
                        retryable=True,
                        player_client=ytdl_logger.player_client,
                    )
                tag_existing(downloaded)
                return emit_done(
                    status="downloaded",
                    video_id=video_id,
                    title=track.get("title", ""),
                    filepath=downloaded,
                    bytes=actual_bytes,
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
                error=_friendly_download_error(exc.msg),
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
