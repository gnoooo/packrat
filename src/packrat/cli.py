"""Command-line interface for packrat."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
from typing import Any, Sequence

from ytmusicapi import YTMusic

from packrat import albums as albums_module
from packrat import artists as artists_module
from packrat import cli_compat
from packrat import discover as discover_module
from packrat import discover_phase4 as discover_phase4_module
from packrat import queues as queues_module
from packrat import sync as sync_module
from packrat import tui as tui_module
from packrat import cli_args
from packrat.batch import exit_code_from_result, run_batch_download
from packrat.download import _browser_tuple, _final_filepath
from packrat.logging_config import redact as _redact
from packrat import security
from packrat.archive import Archive
from packrat import upgrade as upgrade_module
from packrat import paths
from packrat.services import credentials as credentials_service
from packrat.services import diagnostics as diagnostics_service
from packrat.services import metadata as metadata_service
from packrat.manifest import Manifest
from packrat.integrity import verify_audio
from packrat.playlist import m3u8_entry, write_m3u8
from packrat.sizes import format_size, parse_size
from packrat.sync import browser_cookie_file
from packrat.naming import track_path
from packrat.song_meta import SongMetadata, apply_song_metadata, resolve_song_metadata


def print_result_summary(result: Any) -> None:
    """One-line download summary from a worker result; never a raw dict."""
    if isinstance(result, dict) and result.get("error"):
        print(result["error"], file=sys.stderr)
        return
    summary = result.get("summary") if isinstance(result, dict) else None
    if not isinstance(summary, dict):
        summary = result if isinstance(result, dict) else None
    if not isinstance(summary, dict) or "downloaded" not in summary:
        return  # listing-only result (no batch ran) has no summary line
    print(
        f"downloaded {summary.get('downloaded', 0)}"
        f"  archived {summary.get('archived', 0)}"
        f"  skipped-size {summary.get('skipped_size', 0)}"
        f"  failed {summary.get('failed', 0)}"
        f"  stopped {summary.get('stopped', False)}"
    )


def _confirm_download(args: argparse.Namespace, prompt: str) -> bool:
    """Ask before a download; --yes skips. No interactive terminal is an error."""
    if getattr(args, "yes", False):
        return True
    if not sys.stdin.isatty():
        print(
            "Not downloading: this terminal can't ask for confirmation. "
            "Add --yes to continue.",
            file=sys.stderr,
        )
        return False
    answer = input(prompt).strip().lower()
    if answer in ("", "y", "yes"):
        return True
    print("Not downloading.", file=sys.stderr)
    return False


_AUDIO_EXTENSIONS = {".m4a", ".mp3", ".opus", ".flac", ".webm", ".aac", ".ogg"}
_STATE_COMMANDS = {
    "download-one", "sync", "download-manifest", "resume", "artist",
    "discover", "upgrade",
}


@contextlib.contextmanager
def _run_lock():
    """One state-changing Packrat process at a time."""
    import fcntl

    paths.ensure_config_dir()
    lock_path = paths.CONFIG_DIR / "run.lock"
    fh = lock_path.open("w")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        raise RuntimeError(
            "another Packrat run is active; wait for it to finish "
            "before starting another download or upgrade"
        )
    try:
        yield
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


def _print_paths(output_root: pathlib.Path, archive: str) -> None:
    print(f"output folder: {output_root.expanduser().resolve()}")
    print(f"download history: {pathlib.Path(archive).expanduser().resolve()}")


def _check_archive_safety(args: argparse.Namespace, output_root: pathlib.Path) -> bool:
    """Warn when a new archive would be created over an existing audio library."""
    if getattr(args, "force_new_archive", False):
        return True
    archive = pathlib.Path(args.archive).expanduser()
    if archive.exists() or not output_root.exists():
        return True
    has_audio = any(
        p.is_file() and p.suffix.lower() in _AUDIO_EXTENSIONS
        for p in output_root.rglob("*")
    )
    if not has_audio:
        return True
    print(
        f"Warning: the download history file {archive.resolve()} is missing, "
        f"but {output_root.resolve()} already contains music.",
        file=sys.stderr,
    )
    print(
        "Starting a new history file may download songs you already have.",
        file=sys.stderr,
    )
    print(
        "Use --force-new-archive to continue with a new history file.",
        file=sys.stderr,
    )
    if not sys.stdin.isatty():
        print(
            "Not downloading: this terminal can't ask. "
            "Use --force-new-archive to continue with a new download history.",
            file=sys.stderr,
        )
        return False
    answer = input("Continue with a new download history? [y/N] ").strip().lower()
    if answer in ("y", "yes"):
        return True
    print("Not downloading.", file=sys.stderr)
    return False


def _print_login_help() -> None:
    """Explain how to create the YouTube Music login file."""
    print(
        "No YouTube Music login found.\n"
        "Create one with:\n"
        "  packrat login\n"
        "Or point to an existing file with --headers PATH (or set PACKRAT_HEADERS).",
        file=sys.stderr,
    )


def load_ytmusic(headers_path: str) -> YTMusic:
    """Load a ytmusicapi browser-headers auth file."""
    p = pathlib.Path(headers_path)
    if not p.exists():
        raise FileNotFoundError(f"login file not found: {p}")
    security.secure_file(p)
    security.warn_if_insecure(p, "login file")
    return YTMusic(str(p))




_LOGIN_STEPS = """\
To log in, you need the request headers from a logged-in music.youtube.com tab:

  1. Open https://music.youtube.com in your browser and sign in.
  2. Open Developer Tools (F12) and choose the Network tab.
  3. Reload the page, then click a request to /browse.
  4. Copy its "Request Headers".
  5. Paste them below, then press Ctrl-D (on Windows: Ctrl-Z, Enter).
"""


def _remove_empty_login(path: pathlib.Path, existed: bool) -> None:
    """Do not leave a blank file behind after a failed first login."""
    if existed:
        return
    try:
        if path.exists() and path.stat().st_size == 0:
            path.unlink()
    except OSError:
        pass


def cmd_login(args: argparse.Namespace) -> int:
    """Create the YouTube Music login file from pasted browser headers."""
    target = pathlib.Path(
        getattr(args, "file", None)
        or getattr(args, "headers", None)
        or cli_args._headers_default()
        or cli_args.HEADERS_CONFIG_PATH
    ).expanduser()

    if not sys.stdin.isatty():
        print(
            "Login needs an interactive terminal: it asks you to paste browser "
            "headers.\nRun 'packrat login' directly in a terminal.",
            file=sys.stderr,
        )
        return 2

    existed = target.exists()
    security.secure_directory(target.parent)
    try:
        target.touch(mode=0o600, exist_ok=True)
        security.secure_file(target)
    except OSError:
        pass

    print(_LOGIN_STEPS)
    try:
        from ytmusicapi.auth.browser import setup_browser

        setup_browser(filepath=str(target))
    except KeyboardInterrupt:
        print("\nLogin cancelled.", file=sys.stderr)
        _remove_empty_login(target, existed)
        return 130
    except Exception as exc:
        print(f"Login failed: {exc}", file=sys.stderr)
        print("Nothing was saved. Run 'packrat login' to try again.", file=sys.stderr)
        _remove_empty_login(target, existed)
        return 1
    finally:
        security.secure_file(target)

    print(f"Login file saved: {target}")
    print("Checking it...")
    try:
        yt = load_ytmusic(str(target))
        yt.get_library_playlists(limit=1)
    except Exception as exc:
        print(
            f"Could not verify the login: {exc.__class__.__name__}.\n"
            "The file was saved; run 'packrat auth' to try again.",
            file=sys.stderr,
        )
        return 1
    print("Login works.")
    return 0


def cmd_logout(args: argparse.Namespace) -> int:
    """Delete the saved login headers file."""
    try:
        target, removed = credentials_service.delete_login(
            getattr(args, "file", None)
        )
    except IsADirectoryError:
        print("Refusing to delete a non-file login path.", file=sys.stderr)
        return 1
    if not target.exists() and not removed:
        print(f"No login file found at {target}")
        return 0
    if not removed:
        print(f"Could not delete {target}", file=sys.stderr)
        return 1
    print(f"Removed login file: {target}")
    return 0


def cmd_export_metadata(args: argparse.Namespace) -> int:
    """Export normalized track metadata from a saved manifest."""
    if args.manifest:
        manifest_path = pathlib.Path(args.manifest)
    else:
        root = pathlib.Path(cli_args._output_default())
        plans = sorted(
            (root / "playlists").glob("*.manifest.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not plans:
            print("No saved plan found; pass --manifest FILE.", file=sys.stderr)
            return 1
        manifest_path = plans[0]
    if not manifest_path.exists():
        print(f"Saved plan not found: {manifest_path}", file=sys.stderr)
        return 1
    target = (
        pathlib.Path(args.file)
        if args.file
        else manifest_path.with_name(f"metadata.{args.format}")
    )
    try:
        metadata_service.export_manifest_metadata(manifest_path, target, args.format)
    except Exception as exc:
        print(f"Cannot export metadata: {exc}", file=sys.stderr)
        return 1
    print(f"metadata: {target}")
    return 0


def cmd_upgrade(args: argparse.Namespace) -> int:
    """Check for or install a newer release."""
    try:
        return upgrade_module.run_upgrade(
            check_only=getattr(args, "check", False),
            include_prerelease=getattr(args, "pre", False) or getattr(args, "from_master", False),
            yes=getattr(args, "yes", False),
            dry_run=getattr(args, "dry_run", False),
        )
    except upgrade_module.UpgradeError as exc:
        print(f"upgrade: {exc}", file=sys.stderr)
        return 1


def cmd_doctor(args: argparse.Namespace) -> int:
    """Check local prerequisites and optionally run an online canary."""
    checks = diagnostics_service.run_checks(
        getattr(args, "headers", None),
        online=getattr(args, "online", False),
    )
    for check in checks:
        print(f"{check.status:4}  {check.label:24} {check.detail}")
    return 1 if any(c.status == "FAIL" for c in checks) else 0


def cmd_auth(args: argparse.Namespace) -> int:
    """Check the YouTube Music login and print a short summary."""
    try:
        yt = load_ytmusic(args.headers)
    except FileNotFoundError as exc:
        print(f"Login failed: {exc}", file=sys.stderr)
        _print_login_help()
        return 1

    try:
        yt.get_library_playlists(limit=1)
        liked = yt.get_liked_songs(limit=1)
    except Exception as exc:
        print(
            f"Login failed ({exc.__class__.__name__}). Your login may have "
            "expired. Run: ytmusicapi browser --file "
            "~/.config/packrat/browser.json",
            file=sys.stderr,
        )
        return 1

    if not liked.get("tracks"):
        print("Login works, but your library looks empty.")
        return 0

    print("Login works.")
    return 0


def cmd_library(args: argparse.Namespace) -> int:
    """List library playlists."""
    yt = load_ytmusic(args.headers)
    playlists = yt.get_library_playlists(limit=args.top)
    if not playlists:
        print("No playlists found.")
        return 0
    for pl in playlists:
        print(f"{pl['playlistId']}\t{pl['title']}")
    return 0


class _YtdlpLogger:
    """Capture the player client yt-dlp used without dumping raw URLs/tokens."""

    def __init__(self) -> None:
        self.player_client: str | None = None

    def debug(self, msg: str) -> None:
        m = _redact(msg)
        if match := re.search(r"Downloading ([^ ]+) player API JSON", m):
            self.player_client = match.group(1)

    def info(self, _msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        print(f"yt-dlp warning: {_redact(msg)}", file=sys.stderr)

    def error(self, msg: str) -> None:
        print(f"yt-dlp error: {_redact(msg)}", file=sys.stderr)


def _check_deno() -> tuple[bool, str | None]:
    """Return (ok, version_or_reason)."""
    binary = shutil.which("deno")
    if not binary:
        return False, None
    try:
        out = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout
    except Exception:
        return False, None
    match = re.search(r"deno (\d+\.\d+\.\d+)", out, re.IGNORECASE)
    if not match:
        return False, None
    version = match.group(1)
    major, minor, _patch = map(int, version.split(".", 2))
    if (major, minor) < (2, 3):
        return False, version
    return True, version


def _verify_with_ffprobe(path: pathlib.Path) -> dict:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-show_entries",
        "stream=codec_name",
        "-of",
        "json",
        str(path),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def _verify_with_mutagen(path: pathlib.Path) -> dict:
    import mutagen

    audio = mutagen.File(str(path))
    if audio is None:
        return {"error": "mutagen could not read file"}
    tags = getattr(audio, "tags", None) or {}
    has_cover = False
    if hasattr(audio, "pictures") and audio.pictures:
        has_cover = True
    if hasattr(tags, "get"):
        # MP4 cover is in 'covr'
        if tags.get("covr"):
            has_cover = True
    return {
        "type": type(audio).__name__,
        "tags": {k: str(v)[:120] for k, v in tags.items()},
        "has_cover": has_cover,
    }


def _mark_downloaded_in_manifests(
    output_root: pathlib.Path, video_id: str, filepath: pathlib.Path
) -> None:
    """Best-effort: update an existing plan that contains this video."""
    from packrat.manifest import atomic_write_text

    for manifest_path in (output_root / "playlists").glob("*.manifest.json"):
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        changed = False
        for entry in payload.get("entries", []):
            if entry.get("video_id") == video_id:
                entry["status"] = "downloaded"
                entry["path"] = str(filepath)
                entry["error"] = None
                changed = True
        if changed:
            atomic_write_text(manifest_path, json.dumps(payload, indent=2))


def cmd_download_one(args: argparse.Namespace) -> int:
    """Download a single song from a link or video ID and verify the result."""
    video_id = queues_module.parse_video_id(args.video_id)
    if not video_id:
        print(
            f"error: could not find a YouTube video ID in {args.video_id!r}",
            file=sys.stderr,
        )
        print(
            "Pass a music.youtube.com link, a youtu.be link, or the "
            "11-character video ID.",
            file=sys.stderr,
        )
        return 2

    deno_ok, deno_version = _check_deno()
    if not deno_ok:
        if deno_version:
            print(
                f"Warning: Deno {deno_version} is older than 2.3.0; some songs "
                "may fail. Install a newer Deno from https://deno.com.",
                file=sys.stderr,
            )
        else:
            print(
                "Warning: Deno is not installed; some songs may fail. "
                "Install it from https://deno.com.",
                file=sys.stderr,
            )

    output_root = pathlib.Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)

    metadata: SongMetadata | None = None
    headers_path = getattr(args, "headers", None) or cli_args._headers_default() or ""
    if headers_path and pathlib.Path(headers_path).expanduser().exists():
        try:
            metadata = resolve_song_metadata(video_id, headers_path)
        except Exception as exc:
            print(
                f"metadata lookup failed: {_redact(exc)}; using uploader layout",
                file=sys.stderr,
            )
    elif not headers_path:
        print(
            "warning: no login file; using uploader layout (run 'packrat login')",
            file=sys.stderr,
        )
    if metadata is not None:
        output_path = track_path(
            output_root,
            metadata.artist,
            metadata.album,
            metadata.title,
            metadata.track_num,
            ext="m4a",
            release_type=metadata.release_type,
        )
        print(f"metadata: {metadata.artist} / {metadata.album or 'Singles'}")
        print(f"destination: {output_path}")
    else:
        output_path = output_root / "%(artist,uploader)s" / "%(album|Unknown Album)s" / "%(title)s.%(ext)s"

    import yt_dlp

    logger = _YtdlpLogger()
    url = f"https://music.youtube.com/watch?v={video_id}"

    ydl_opts: dict = {
        "format": "bestaudio[ext=m4a]/bestaudio",
        "outtmpl": str(output_path),
        "writethumbnail": True,
        "postprocessors": [
            {"key": "FFmpegMetadata", "add_metadata": True},
            {"key": "EmbedThumbnail"},
        ],
        "js_runtimes": {"deno": {}},
        "quiet": True,
        "progress": True,
        "logger": logger,
        "noplaylist": True,
    }
    if args.cookies:
        ydl_opts["cookiefile"] = args.cookies
    if args.cookies_from_browser:
        ydl_opts["cookiesfrombrowser"] = _browser_tuple(
            args.cookies_from_browser, getattr(args, "browser_profile", None)
        )

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        print(f"Download failed: {_redact(exc.msg)}", file=sys.stderr)
        if not deno_ok:
            print(
                "This song may need Deno: install it from https://deno.com.",
                file=sys.stderr,
            )
        if not args.cookies and not args.cookies_from_browser:
            print(
                "If this song is private or age-restricted, add your browser "
                "cookies: --cookies-from-browser firefox",
                file=sys.stderr,
            )
        return 1
    except Exception as exc:
        print(f"download failed: {_redact(exc)}", file=sys.stderr)
        return 1

    downloaded = _final_filepath(info, ydl)
    if not downloaded.exists():
        print(f"download failed: expected file not found: {downloaded}", file=sys.stderr)
        return 1
    if metadata is not None:
        try:
            apply_song_metadata(downloaded, metadata)
        except Exception as exc:
            print(f"tag update failed: {_redact(exc)}", file=sys.stderr)
    try:
        archive = Archive(cli_args._archive_default())
        archive.add(video_id)
        archive.save()
    except Exception as exc:
        print(f"warning: could not update download history: {_redact(exc)}",
              file=sys.stderr)
    try:
        _mark_downloaded_in_manifests(output_root, video_id, downloaded)
    except Exception as exc:
        print(f"warning: could not update saved plans: {_redact(exc)}",
              file=sys.stderr)

    print(f"downloaded: {downloaded}")
    print(
        f"format: {info.get('format_id')} "
        f"(codec={info.get('acodec')}, abr={info.get('abr')})"
    )
    print(
        "cookies configured: "
        f"{'yes' if args.cookies or args.cookies_from_browser else 'no'}"
    )
    if logger.player_client:
        print(f"player client: {logger.player_client}")

    try:
        probe = _verify_with_ffprobe(downloaded)
        print("ffprobe:")
        print(json.dumps(probe, indent=2))
    except Exception as exc:
        print(f"ffprobe failed: {_redact(exc)}", file=sys.stderr)
        return 1

    try:
        meta = _verify_with_mutagen(downloaded)
        print("mutagen:")
        print(json.dumps(meta, indent=2))
    except Exception as exc:
        print(f"mutagen failed: {_redact(exc)}", file=sys.stderr)
        return 1

    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    """Discover new tracks from YouTube Music recommendations."""
    output_root = pathlib.Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)
    _print_paths(output_root, args.archive)
    if not _check_archive_safety(args, output_root):
        return 2

    cookie_ctx = (
        browser_cookie_file(args.cookies_from_browser, getattr(args, "browser_profile", None))
        if args.cookies_from_browser and not args.cookies
        else contextlib.nullcontext(args.cookies)
    )
    with cookie_ctx as cookie_path:
        if cookie_path and args.cookies_from_browser and not args.cookies:
            print(f"caching cookies from {args.cookies_from_browser}...")
            args.cookies = cookie_path
            args.cookies_from_browser = None

        sources = [s.strip() for s in args.source.split(",") if s.strip()]
        valid_sources = {"home", "charts", "moods", "new_releases"}
        unknown = [s for s in sources if s not in valid_sources]
        if unknown:
            print(
                f"error: unknown source(s): {', '.join(unknown)}; choose from "
                + ", ".join(sorted(valid_sources)),
                file=sys.stderr,
            )
            return 2
        manifest, summary = discover_module.run_discover(
            headers_path=args.headers,
            sources=sources,
            country=args.country,
            quota=args.quota,
            per_container=args.per_container,
            per_artist=args.per_artist,
            min_duration=args.min_duration,
            max_duration=args.max_duration,
            archive_path=args.archive,
            cookies=args.cookies,
            cookies_from_browser=args.cookies_from_browser,
            browser_profile=getattr(args, "browser_profile", None),
            max_size=args.max_size,
            singles_dir=getattr(args, "singles_dir", "Singles"),
        )

        manifest_path = output_root / "playlists" / "discover.manifest.json"
        manifest.write(manifest_path, "json")

        print(f"\ndiscover manifest: {manifest_path}")
        print(f"total tracks: {summary['total']}")
        print("per source:")
        for source, count in summary["per_source"].items():
            print(f"  {source}: {count}")
        print(f"estimated size: {summary['estimated_size_human']}")
        if summary["skipped_by_size"]:
            print(f"skipped by size: {summary['skipped_by_size']}")
        print("top artists:")
        for artist, count in summary["top_artists"]:
            print(f"  {artist}: {count}")

        if args.dry_run:
            return 0
        if not _confirm_download(args, "download discovered tracks? [Y/n] "):
            return 2
        print("\ndownloading discovered tracks...")
        return exit_code_from_result(
            discover_module.download_pending(
                manifest=manifest,
                output_root=output_root,
                cookies=args.cookies,
                cookies_from_browser=args.cookies_from_browser,
                browser_profile=getattr(args, "browser_profile", None),
                archive_path=args.archive,
                delay=args.delay,
                limit=getattr(args, "limit", None),
                max_size=parse_size(args.max_size) if args.max_size else None,
            )
        )


def cmd_discover_phase4(args: argparse.Namespace) -> int:
    """Discover taste-adjacent tracks beyond YT Music's own sections."""
    output_root = pathlib.Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)

    discover_manifest_path = None
    if not args.no_phase3:
        discover_manifest_path = output_root / "playlists" / "discover.manifest.json"

    quota = args.quota
    if quota is not None:
        for name, default in (
            ("deep", 12), ("adjacent", 10), ("radio", 10),
            ("external", 5), ("tag", 5),
        ):
            if getattr(args, name) == default:
                setattr(args, name, quota)
    quotas = {
        "deep_cuts": args.deep,
        "adjacent": args.adjacent,
        "radio": args.radio,
        "external_graph": args.external,
        "tag_search": args.tag,
    }

    manifest, summary = discover_phase4_module.run_discover_phase4(
        headers_path=args.headers,
        output_root=str(output_root),
        archive_path=args.archive,
        discover_manifest_path=str(discover_manifest_path) if discover_manifest_path else None,
        quotas=quotas,
        per_container=args.per_container,
        per_artist=args.per_artist,
        family_cap=args.family_cap,
        min_duration=args.min_duration,
        max_duration=args.max_duration,
        max_size=args.max_size,
    )

    manifest_path = output_root / "playlists" / "discover-phase4.manifest.json"
    manifest.write(manifest_path, "json")

    print(f"\nphase 4 profile: {paths.PROFILE_PATH}")
    print(f"phase 4 manifest: {manifest_path}")
    print(f"total tracks: {summary['total']}")
    print("per bucket:")
    for bucket, count in summary["per_bucket"].items():
        print(f"  {bucket}: {count}")
    print("funnel (candidates -> selected):")
    for bucket, data in summary["funnel"].items():
        print(f"  {bucket}: {data['candidates']} -> {data['selected']}")
    print(f"estimated size: {summary['estimated_size_human']}")
    if summary["skipped_by_size"]:
        print(f"skipped by size: {summary['skipped_by_size']}")
    print("top artists:")
    for artist, count in summary["top_artists"]:
        print(f"  {artist}: {count}")

    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    """Sync liked songs or a playlist to disk."""
    args.source = sync_module.normalize_source(args.source)
    output_root = pathlib.Path(args.output)
    _print_paths(output_root, args.archive)
    if not _check_archive_safety(args, output_root):
        return 2
    if not args.dry_run and not getattr(args, "yes", False):
        prompt = (
            "Download your Liked Songs now? This can download many songs; "
            "add --limit N to cap. [Y/n] "
            if args.source == "liked"
            else "Download this playlist now? [Y/n] "
        )
        if not _confirm_download(args, prompt):
            return 2
    if args.tui and not args.dry_run:
        return exit_code_from_result(
            tui_module.run_tui(
                sync_module.run_sync,
                {"args": args},
                mode=args.source,
                ascii_mode=args.ascii,
            )
        )
    return exit_code_from_result(sync_module.run_sync(args))


def cmd_download_manifest(args: argparse.Namespace) -> int:
    """Resume downloads from a saved plan (default: the newest one)."""
    if args.manifest:
        manifest_path = pathlib.Path(args.manifest)
    else:
        root = pathlib.Path(args.output or cli_args._output_default())
        plans = sorted(
            (root / "playlists").glob("*.manifest.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not plans:
            print(
                "No saved plan found. Run a download or discovery command "
                "first, or pass --manifest FILE.",
                file=sys.stderr,
            )
            return 1
        manifest_path = plans[0]
        print(f"Using saved plan: {manifest_path}")
    if not manifest_path.exists():
        print(f"Saved plan not found: {manifest_path}", file=sys.stderr)
        return 1
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Cannot read the saved plan: {exc}", file=sys.stderr)
        return 1

    if args.output:
        output_root = pathlib.Path(args.output)
    elif manifest_path.parent.name == "playlists":
        output_root = manifest_path.parent.parent
    else:
        print(
            "error: --output is required when the manifest is not inside a "
            "playlists/ folder",
            file=sys.stderr,
        )
        return 2
    output_root.mkdir(parents=True, exist_ok=True)
    _print_paths(output_root, args.archive)
    if not _check_archive_safety(args, output_root):
        return 2

    # Artist manifests carry their own label and playlist layout.
    if payload.get("command") == "artist":
        label = payload.get("label") or payload.get("artist", manifest_path.stem)
        failed_only = bool(getattr(args, "failed_only", False))
        wanted = "failed" if failed_only else "pending"
        artist_entries = [
            e for e in payload.get("entries", [])
            if e.get("status") == wanted
        ]
        if not artist_entries:
            print(f"no {wanted} tracks in manifest")
            return 0
        total_size = sum((e.get("est_size") or 0) for e in artist_entries)
        print(f"pending tracks: {len(artist_entries)}")
        print(f"estimated size: {format_size(total_size)}")
        if getattr(args, "dry_run", False):
            print("dry-run: no downloads")
            return 0
        if not getattr(args, "yes", False):
            if sys.stdin.isatty():
                answer = input("download now? [y/N] ").strip().lower()
                if answer not in ("y", "yes"):
                    print("Not downloading. Use --yes to skip the question.")
                    return 2
            else:
                print(
                    "Not downloading: this terminal can't ask. Add --yes to continue.",
                    file=sys.stderr,
                )
                return 2
        kwargs: dict[str, Any] = {
            "label": label,
            "output_root": output_root,
            "archive_path": args.archive,
            "cookies": args.cookies,
            "cookies_from_browser": args.cookies_from_browser,
            "browser_profile": getattr(args, "browser_profile", None),
            "max_size": args.max_size,
            "delay": args.delay,
            "payload": payload,
            "limit": getattr(args, "limit", None),
            "manifest_path": str(manifest_path),
            "failed_only": failed_only,
        }
        if args.tui:
            result = tui_module.run_tui(
                artists_module.download_artist,
                kwargs,
                mode="artist",
                ascii_mode=args.ascii,
            )
        else:
            result = artists_module.download_artist(**kwargs)
        print_result_summary(result)
        if isinstance(result, dict) and result.get("error"):
            return 1
        print(f"manifest: {result.get('manifest_path')}")
        print(f"playlists: {result.get('m3u8_dir')}")
        return exit_code_from_result(result)

    # Generic manifest (sync, discover, albums, queues, phase4, radio, ...).
    manifest = Manifest.load(manifest_path)
    if getattr(args, "failed_only", False):
        retryable = {"failed", "skipped_size", "skipped"}
        entries = [e for e in manifest.entries if e.get("status") in retryable]
    else:
        entries = []
        for entry in manifest.entries:
            if entry.get("status") == "pending":
                entries.append(entry)
            elif entry.get("status") == "downloaded":
                path = entry.get("path")
                if not path or not verify_audio(pathlib.Path(path)).ok:
                    entry["status"] = "pending"
                    entry["error"] = entry.get("error") or "downloaded file is missing or invalid"
                    entries.append(entry)
    if not entries:
        print("no failed tracks in manifest" if getattr(args, "failed_only", False)
              else "no pending tracks in manifest")
        return 0
    total_pending = len(entries)
    limit = getattr(args, "limit", None)
    if limit and total_pending > limit:
        entries = entries[:limit]
        print(f"limit applied: {limit} of {total_pending} new tracks")

    total_size = sum((e.get("est_size") or 0) for e in entries)
    print(f"pending tracks: {len(entries)}")
    print(f"estimated size: {format_size(total_size)}")
    if getattr(args, "dry_run", False):
        print("dry-run: no downloads")
        return 0

    if not args.yes:
        if sys.stdin.isatty():
            answer = input("download now? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("Not downloading. Use --yes to skip the question.")
                return 2
        else:
            print(
                "Not downloading: this terminal can't ask. Add --yes to continue.",
                file=sys.stderr,
            )
            return 2

    stem = manifest_path.stem
    if stem.endswith(".manifest"):
        stem = stem[: -len(".manifest")]
    m3u8_path = output_root / "playlists" / f"{stem}.m3u8"

    def save_callback(current_entries: list[dict[str, Any]]) -> None:
        # current_entries is the pending subset; the original dicts are
        # mutated in place so manifest.entries already reflects updates.
        manifest.write(manifest_path, "json")
        downloaded = [
            e for e in manifest.entries
            if e.get("status") == "downloaded" and e.get("path")
        ]
        if downloaded:
            write_m3u8(
                m3u8_path,
                [m3u8_entry(e) for e in downloaded],
            )

    if args.tui:
        worker_result: Any = tui_module.run_tui(
            run_batch_download,
            {
                "entries": entries,
                "output_root": output_root,
                "save_callback": save_callback,
                "cookies": args.cookies,
                "cookies_from_browser": args.cookies_from_browser,
                "browser_profile": getattr(args, "browser_profile", None),
                "max_size": parse_size(args.max_size) if args.max_size else None,
                "delay": args.delay,
                "archive_path": args.archive,
                "mode": "manifest",
            },
            mode="manifest",
            ascii_mode=args.ascii,
        )
    else:
        worker_result = run_batch_download(
            entries,
            output_root=output_root,
            save_callback=save_callback,
            cookies=args.cookies,
            cookies_from_browser=args.cookies_from_browser,
            browser_profile=getattr(args, "browser_profile", None),
            max_size=parse_size(args.max_size) if args.max_size else None,
            delay=args.delay,
            archive_path=args.archive,
            mode="manifest",
        )

    print_result_summary(worker_result)
    print(f"manifest: {manifest_path}")
    print(f"playlist: {m3u8_path}")
    return exit_code_from_result(worker_result)


def _selection_prompt(width: int) -> str:
    """Picker prompt; truncated with … only when the terminal is narrower."""
    text = (
        "selection> (Enter = recommended | 'all' = everything incl. tagged"
        " | selection set = A[1-3],S[2],-S[4] | 'none' to abort)"
    )
    return artists_module._dw_truncate(text, width)


def _start_prompt() -> str:
    """The artist start prompt, truncated when the terminal is narrow."""
    return artists_module._dw_truncate(
        "Enter = start | type another selection | q = quit",
        shutil.get_terminal_size((80, 24)).columns,
    ).strip()


def _print_recap(catalog: dict[str, Any], args: argparse.Namespace) -> None:
    """Save the manifest and print tagged/estimate recap lines."""
    totals = catalog["totals"]
    summary = artists_module.summarize_entries(
        catalog["entries"], delay=args.delay,
        measured=totals.get("measured_s_per_track"),
    )
    artists_module.save_manifest(catalog["payload"])
    rule_counts = totals.get("excluded_counts") or {}
    rule_bits = []
    for key, value in rule_counts.items():
        if value:
            rule_bits.append(("dup" if key == "edition_duplicates" else key, value))
    # Tagged releases are shown in the picker, deselected by default.
    if rule_bits:
        print(
            "tagged: "
            + ", ".join(f"{key}={value}" for key, value in rule_bits)
            + " (all shown, deselected by default)"
        )
    if totals.get("skipped_duration"):
        print(f"outside duration window: {totals['skipped_duration']}")
    time_label = f"~{summary['est_time_seconds'] // 60} min"
    if summary["est_source"] == "measured":
        time_label += f" (measured {summary['measured_s_per_track']:.1f} s/track)"
    else:
        # No runs.jsonl history yet: fall back to the measured batch
        # rate 18 s/track (7 s delay + ~11 s download), not the
        # duration model.
        eta = summary["new_tracks"] * 18
        time_label = f"~{eta // 60} min (default estimate)"
    print(
        f"recap: {summary['new_tracks']} new tracks | {summary['est_human']} | {time_label}"
    )
    print(f"manifest: {catalog['manifest_path']}", end="\n\n")


def cmd_artist(args: argparse.Namespace) -> int:
    """Discover and download one or more artists' full catalogs."""
    specs = [args.target] if getattr(args, "target", None) else []
    specs.extend(getattr(args, "also", []) or [])
    if args.from_file:
        for line in pathlib.Path(args.from_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                specs.append(line)
    if not specs:
        print(
            'No artist given. Try: packrat artist "Artist name"',
            file=sys.stderr,
        )
        return 2
    print("Will process: " + ", ".join(specs))
    if len(specs) > 1 and not args.yes and not args.dry_run and sys.stdin.isatty():
        if not _confirm_download(args, "continue? [Y/n] "):
            return 2

    yt = YTMusic(args.headers)
    resolved: list[dict[str, Any]] = []
    pick_used = False
    for spec in specs:
        channel = artists_module.parse_channel_id(spec)
        if channel:
            print(f"interpreting '{spec}' as a channel id/url")
            resolved.append({"name": channel, "browseId": channel})
            continue
        if artists_module.looks_like_video_id(spec):
            print(f"interpreting '{spec}' as a video id/url")
            resolved_v = artists_module.resolve_video(yt, spec)
            if resolved_v:
                print(f"  -> artist: {resolved_v['name']}")
                resolved.append(resolved_v)
                continue
            print(f"could not resolve video '{spec}' to an artist", file=sys.stderr)
            return 2
        candidates = artists_module.search_artists(yt, spec)
        if candidates:
            print(f"interpreting '{spec}' as an artist name")
            exact = [
                c for c in candidates
                if c["name"].strip().lower() == spec.strip().lower()
            ]
            if len(exact) == 1:
                resolved.append(exact[0])
                continue
        elif re.fullmatch(r"[A-Za-z0-9_-]{11}", spec.strip()):
            # Bare 11-char token: try it as a video id instead.
            print(f"interpreting '{spec}' as a video id (no name match)")
            resolved_v = artists_module.resolve_video(yt, spec)
            if resolved_v:
                print(f"  -> artist: {resolved_v['name']}")
                resolved.append(resolved_v)
                continue
            print(f"no artist found for '{spec}'", file=sys.stderr)
            return 2
        else:
            print(f"no artist found for '{spec}'", file=sys.stderr)
            return 2
        candidate = getattr(args, "candidate", None)
        if candidate:
            if pick_used:
                print(
                    f"ambiguous match for '{spec}': --candidate already used;"
                    " use an artist link instead",
                    file=sys.stderr,
                )
                return 2
            if not 1 <= candidate <= len(candidates):
                print(
                    f"error: --candidate {candidate} is out of range; "
                    f"found {len(candidates)} match(es)",
                    file=sys.stderr,
                )
                return 2
            pick_used = True
            resolved.append(candidates[candidate - 1])
        elif sys.stdin.isatty():
            index = artists_module.interactive_pick(candidates)
            if index is None:
                print("cancelled", file=sys.stderr)
                return 2
            resolved.append(candidates[index])
        else:
            print("Multiple artists match:")
            for i, c in enumerate(candidates, start=1):
                subs = c.get("subscribers") or ""
                subs_shown = subs if subs else "subscribers unknown"
                print(
                    f"  {i}) {c['name']}  [{subs_shown}]"
                    f"  sample album: {c.get('sample') or '?'}"
                )
            print("use --candidate N", file=sys.stderr)
            return 2

    catalogs: list[dict[str, Any]] = []
    output_root = pathlib.Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)
    _print_paths(output_root, args.archive)
    if not _check_archive_safety(args, output_root):
        return 2
    for chosen in resolved:
        print(f"resolving catalog for {chosen['browseId']} ...")
        # With the interactive picker the download set is the picked rows
        # only, so pending top songs must not count as coverage: with
        # scope="all" their titles seed owned_keys and releases can show
        # "already owned"/[x] for tracks this run would never download.
        scope = "albums" if sys.stdin.isatty() else "all"
        catalog = artists_module.build_catalog(
            args.headers,
            chosen["browseId"],
            output_root,
            args.archive,
            include_live=getattr(args, "include_live", False),
            include_compilations=getattr(args, "include_compilations", False),
            include_remixes=getattr(args, "include_remixes", False),
            albums_only=getattr(args, "albums_only", False),
            no_singles=getattr(args, "no_singles", False),
            duration_window=True,
            min_duration=getattr(args, "min_duration", 90),
            max_duration=getattr(args, "max_duration", 540),
            scope=scope,
            max_size=args.max_size,
            delay=args.delay,
            singles_dir=getattr(args, "singles_dir", "Singles"),
            resolve_excluded=sys.stdin.isatty(),
        )
        catalogs.append(catalog)

        print(f"artist: {catalog['artist']}")
        subs = catalog.get("subscribers")
        if subs:
            print(f"subscribers: {subs}")
        sections = artists_module.build_sections(catalog)
        select_expr = getattr(args, "select", None)
        if select_expr:
            try:
                picked = artists_module.parse_selection(select_expr)
            except ValueError as exc:
                print(f"bad selection: {exc}; e.g. A[1-3],S[2],-S[4]", file=sys.stderr)
                return 2
            catalog = artists_module.apply_selection(
                catalog,
                sections,
                picked,
                headers_path=args.headers,
                output_root=output_root,
                archive_path=args.archive,
                duration_window=True,
                min_duration=getattr(args, "min_duration", 90),
                max_duration=getattr(args, "max_duration", 540),
                singles_dir=getattr(args, "singles_dir", "Singles"),
                delay=args.delay,
            )
            if catalog is None:
                print("nothing selected; manifest untouched for this artist.", file=sys.stderr)
                return 2
            catalogs[-1] = catalog
        elif sys.stdin.isatty():
            artists_module.print_sections(sections, ascii_mode=args.ascii)
            picked: list[dict[str, Any]] | None = None
            while True:
                try:
                    answer = input(_selection_prompt(
                        shutil.get_terminal_size((80, 24)).columns
                    )).strip()
                except EOFError:
                    break
                if answer == "":
                    picked = []  # default selection
                    break
                if answer.lower() in ("none", "q", "quit"):
                    break
                try:
                    picked = artists_module.parse_selection(answer)
                    break
                except ValueError as exc:
                    print(f"bad selection: {exc}; e.g. A[1-3],S[2],-S[4]")
                    continue
            if picked is None:
                print("nothing selected; manifest untouched for this artist.")
                continue
            catalog = artists_module.apply_selection(
                catalog,
                sections,
                picked,
                headers_path=args.headers,
                output_root=output_root,
                archive_path=args.archive,
                duration_window=True,
                min_duration=getattr(args, "min_duration", 90),
                max_duration=getattr(args, "max_duration", 540),
                singles_dir=getattr(args, "singles_dir", "Singles"),
                delay=args.delay,
            )
            if catalog is None:
                print("nothing selected; manifest untouched for this artist.")
                continue
        _print_recap(catalog, args)
        # DISPLAY/flow only: Enter starts the (later) batch download,
        # a typed selection re-runs the picker math for this artist,
        # 'q' quits without downloading.
        if sys.stdin.isatty() and not args.dry_run and not args.yes:
            answer = input(_start_prompt()).strip()
            while answer not in ("", "q", "quit"):
                try:
                    tokens = artists_module.parse_selection(answer)
                except ValueError as exc:
                    print(f"bad selection: {exc}; e.g. A[1-3],S[2],-S[4]")
                    answer = input(_start_prompt()).strip()
                    continue
                new_catalog = artists_module.apply_selection(
                    catalog, sections, tokens,
                    headers_path=args.headers,
                    output_root=output_root,
                    archive_path=args.archive,
                    duration_window=True,
                    min_duration=getattr(args, "min_duration", 90),
                    max_duration=getattr(args, "max_duration", 540),
                    singles_dir=getattr(args, "singles_dir", "Singles"),
                    delay=args.delay,
                )
                if new_catalog is None:
                    print("nothing selected; manifest untouched for this artist.")
                else:
                    catalog = new_catalog
                    catalogs[-1] = catalog
                    _print_recap(catalog, args)
                answer = input(_start_prompt()).strip()
            if answer.lower() in ("q", "quit"):
                print("Not downloading.")
                return 0

    if args.dry_run:
        print("dry-run: manifest written, no downloads.")
        return 0

    if not args.yes and not sys.stdin.isatty():
        print(
            "Not downloading: this terminal can't ask. Add --yes to continue.",
            file=sys.stderr,
        )
        return 2
    if not args.yes and getattr(args, "select", None):
        if not _confirm_download(args, "start download? [Y/n] "):
            return 2

    exit_code = 0
    remaining = getattr(args, "limit", None)
    for catalog in catalogs:
        label = catalog["label"]
        print(f"downloading catalog: {catalog['artist']}")
        pending_count = sum(
            1 for e in catalog.get("entries", []) if e.get("status") == "pending"
        )
        if pending_count == 0:
            print("nothing new to download")
            continue
        if remaining is not None and remaining <= 0:
            print(f"limit reached; skipping {label}")
            continue
        catalog_limit = remaining
        if remaining is not None:
            catalog_limit = min(remaining, pending_count)
            remaining = max(0, remaining - catalog_limit)
            if pending_count > catalog_limit:
                print(
                    f"limit applied: {catalog_limit} of {pending_count} new tracks"
                )
        kwargs = {
            "label": label,
            "output_root": output_root,
            "archive_path": args.archive,
            "cookies": args.cookies,
            "cookies_from_browser": args.cookies_from_browser,
            "browser_profile": getattr(args, "browser_profile", None),
            "max_size": args.max_size,
            "delay": args.delay,
            "limit": catalog_limit,
        }
        if args.tui:
            result = tui_module.run_tui(
                artists_module.download_artist,
                kwargs,
                mode="artist",
                ascii_mode=args.ascii,
            )
        else:
            result = artists_module.download_artist(**kwargs)
        print_result_summary(result)
        if isinstance(result, dict) and result.get("error"):
            print(result["error"], file=sys.stderr)
            exit_code = 1
            continue
        print(f"manifest: {result.get('manifest_path')}")
        print(f"playlists: {result.get('m3u8_dir')}")
        if exit_code_from_result(result):
            exit_code = 1
    return exit_code


def cmd_radio(args: argparse.Namespace) -> int:
    """Download the radio from one track URL or id. --dry-run only lists it."""
    output_root = pathlib.Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)
    _print_paths(output_root, args.archive)
    if not _check_archive_safety(args, output_root):
        return 2
    limit = args.limit or 50
    kwargs = {
        "seed_spec": args.seed,
        "headers_path": args.headers,
        "output_root": pathlib.Path(args.output),
        "archive_path": args.archive,
        "limit": limit,
        "per_artist_per_queue": args.per_artist,
        "min_duration": args.min_duration,
        "max_duration": args.max_duration,
        "cookies": args.cookies,
        "cookies_from_browser": args.cookies_from_browser,
        "browser_profile": getattr(args, "browser_profile", None),
        "max_size": args.max_size,
        "delay": args.delay,
    }
    plan_kwargs = dict(kwargs, dry_run=True)
    if args.tui:
        plan = tui_module.run_tui(
            queues_module.build_radio,
            plan_kwargs,
            mode="radio",
            ascii_mode=args.ascii,
        )
    else:
        plan = queues_module.build_radio(**plan_kwargs)
    if isinstance(plan, dict) and plan.get("error"):
        print_result_summary(plan)
        return 1
    print(f"radio seed: {plan.get('seed')} ({plan.get('seed_video_id')})")
    print(f"tracks: {plan.get('total_tracks')}  est size: {plan.get('total_est_size_human')}")
    requested = plan.get("requested_tracks")
    if requested and requested > plan.get("total_tracks", 0):
        print(f"limit applied: {plan.get('total_tracks')} of {requested} new tracks")
    for t in plan.get("tracks", []):
        print(f"  {t}")

    if args.dry_run:
        return 0
    if not _confirm_download(args, "download radio? [Y/n] "):
        return 2

    if args.tui:
        result = tui_module.run_tui(
            queues_module.build_radio,
            dict(kwargs, dry_run=False),
            mode="radio",
            ascii_mode=args.ascii,
        )
    else:
        result = queues_module.build_radio(**dict(kwargs, dry_run=False))
    print_result_summary(result)
    if isinstance(result, dict) and result.get("error"):
        return 1
    print(f"manifest: {result.get('manifest_path')}")
    print(f"playlist: {result.get('m3u8_path')}")
    return exit_code_from_result(result)


def cmd_discover_queues(args: argparse.Namespace) -> int:
    """Build playback-queue playlists from liked-track seeds."""
    output_root = pathlib.Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)
    _print_paths(output_root, args.archive)
    if not _check_archive_safety(args, output_root):
        return 2

    limit = getattr(args, "limit", None)
    total_cap = getattr(args, "total_cap", 200)
    if limit:
        total_cap = max(total_cap, limit * 2, 100)
    result = queues_module.discover_queues(
        headers_path=args.headers,
        output_root=output_root,
        archive_path=args.archive,
        seed_count=args.seed_count,
        hop_depth=args.hop_depth,
        hop_width=args.hop_width,
        per_artist_per_queue=args.per_artist_per_queue,
        per_queue_limit=args.per_queue_limit,
        total_cap=total_cap,
        min_duration=args.min_duration,
        max_duration=args.max_duration,
        random_seed=args.random_seed,
        singles_dir=getattr(args, "singles_dir", "Singles"),
    )

    print(f"queues manifest: {result['manifest_path']}")
    print(f"seeds used: {result['seeds_used']}")
    print(f"total tracks: {result['total_tracks']}")
    print(f"estimated size: {result['total_est_size_human']}")
    print("per queue:")
    for q in result["queues"]:
        print(
            f"  {q['seed']}: {q['tracks']} tracks, "
            f"{q['unique_artists']} artists, {format_size(q['est_size'])}"
        )
    if limit and result["total_tracks"] > limit:
        print(f"limit applied: {limit} of {result['total_tracks']} new tracks")

    if args.dry_run:
        return 0
    if not _confirm_download(args, "download queues? [Y/n] "):
        return 2

    if args.tui:
        dl_result: dict[str, Any] = tui_module.run_tui(
            queues_module.download_queues,
            {
                "output_root": output_root,
                "archive_path": args.archive,
                "cookies": args.cookies,
                "cookies_from_browser": args.cookies_from_browser,
                "browser_profile": getattr(args, "browser_profile", None),
                "max_size": args.max_size,
                "delay": args.delay,
                "limit": limit,
            },
            mode="queues",
            ascii_mode=args.ascii,
        )
    else:
        dl_result = queues_module.download_queues(
            output_root=output_root,
            archive_path=args.archive,
            cookies=args.cookies,
            cookies_from_browser=args.cookies_from_browser,
            browser_profile=getattr(args, "browser_profile", None),
            max_size=args.max_size,
            delay=args.delay,
            limit=limit,
        )
    print_result_summary(dl_result)
    if isinstance(dl_result, dict) and dl_result.get("error"):
        return exit_code_from_result(dl_result)
    print(f"queues download complete: {dl_result['manifest_path']}")
    return exit_code_from_result(dl_result)


def cmd_discover_albums(args: argparse.Namespace) -> int:
    """Discover whole albums from top liked artists."""
    output_root = pathlib.Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)
    _print_paths(output_root, args.archive)
    if not _check_archive_safety(args, output_root):
        return 2

    result = albums_module.discover_albums(
        headers_path=args.headers,
        output_root=output_root,
        archive_path=args.archive,
        artist_top_k=args.artist_top_k,
        albums_per_artist=args.albums_per_artist,
        include_singles=args.include_singles,
        min_duration=args.min_duration,
        max_duration=args.max_duration,
        max_size=args.max_size,
    )

    print(f"albums manifest: {result['manifest_path']}")
    print(f"artists considered: {result['artists_considered']}")
    print(f"total new tracks: {result['total_new_tracks']}")
    print(f"already owned tracks: {result['total_already_owned']}")
    if result["total_skipped_size"]:
        print(f"skipped by size: {result['total_skipped_size']}")
    print(f"estimated size: {result['total_est_size_human']}")
    print("albums:")
    for a in result["albums"]:
        print(
            f"  {a['artist']} - {a['album']}: {a['tracks']} new, "
            f"{a['already_owned']} owned, {format_size(a['est_size'])}"
        )
    limit = getattr(args, "limit", None)
    if limit and result["total_new_tracks"] > limit:
        print(f"limit applied: {limit} of {result['total_new_tracks']} new tracks")

    if args.dry_run:
        return 0
    if not _confirm_download(args, "download albums? [Y/n] "):
        return 2

    if args.tui:
        dl_result: dict[str, Any] = tui_module.run_tui(
            albums_module.download_albums,
            {
                "output_root": output_root,
                "archive_path": args.archive,
                "cookies": args.cookies,
                "cookies_from_browser": args.cookies_from_browser,
                "browser_profile": getattr(args, "browser_profile", None),
                "max_size": args.max_size,
                "delay": args.delay,
                "limit": limit,
            },
            mode="albums",
            ascii_mode=args.ascii,
        )
    else:
        dl_result = albums_module.download_albums(
            output_root=output_root,
            archive_path=args.archive,
            cookies=args.cookies,
            cookies_from_browser=args.cookies_from_browser,
            browser_profile=getattr(args, "browser_profile", None),
            max_size=args.max_size,
            delay=args.delay,
            limit=limit,
        )
    print_result_summary(dl_result)
    if isinstance(dl_result, dict) and dl_result.get("error"):
        return exit_code_from_result(dl_result)
    print(f"albums download complete: {dl_result['manifest_path']}")
    return exit_code_from_result(dl_result)


_rewrite_legacy_argv = cli_compat.rewrite_legacy_argv
_move_global_options_after_subcommand = cli_compat.move_global_options_after_subcommand


def build_parser() -> argparse.ArgumentParser:
    parser = cli_args.build_parser()
    cli_compat.register_handlers(parser)
    return parser


_NEEDS_HEADERS = {"auth", "library", "sync", "discover", "artist"}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    raw = cli_compat.rewrite_legacy_argv(
        list(argv if argv is not None else sys.argv[1:])
    )
    raw = cli_compat.move_global_options_after_subcommand(parser, raw)
    if not raw:
        parser.print_help()
        return 0
    if raw in (["discover"], ["ui"]):
        _print_group_help(parser, raw[0])
        return 0
    args = parser.parse_args(raw)
    if getattr(args, "output", None):
        args.output = os.path.expanduser(str(args.output))

    cli_args.resolve_auth(args, raw)
    if args.command in _STATE_COMMANDS:
        cli_args.resolve_run_control_aliases(args)

    verbose = cli_args.resolve_verbose(args)
    if verbose >= 2:
        os.environ["PACKRAT_YTDLP_VERBOSE"] = "1"
    if verbose:
        import logging

        logging.basicConfig(
            level=logging.DEBUG,
            format="%(levelname)-8s %(message)s",
        )

    if args.command in _NEEDS_HEADERS:
        if not args.headers:
            _print_login_help()
            return 2
        if not pathlib.Path(args.headers).expanduser().exists():
            print(
                f"Login file not found: {args.headers}",
                file=sys.stderr,
            )
            _print_login_help()
            return 2

    from packrat import tui as _tui

    _tui.set_visual_options(
        getattr(args, "theme", "ocean"),
        getattr(args, "no_color", False),
        getattr(args, "palette", "auto"),
    )
    _tui.set_no_anim_default(getattr(args, "no_anim", False))

    # If the user did not pass cookies explicitly, reuse the browser that
    # produced the login headers.  The headers file only authenticates
    # ytmusicapi; yt-dlp needs a real cookie jar for age/bot-gated videos.
    browser = None
    needs_cookies = (
        args.command in {"download-one", "sync", "download-manifest", "resume", "artist"}
        or (args.command == "discover"
            and getattr(args, "discover_command", "") in {"queues", "albums", "radio"})
    )
    if (needs_cookies and hasattr(args, "cookies")
            and not getattr(args, "cookies", None)
            and not getattr(args, "cookies_from_browser", None)):
        headers_path = (getattr(args, "headers", None)
                        or cli_args._headers_default())
        browser = cli_args.browser_from_headers(headers_path)

    try:
        with contextlib.ExitStack() as stack:
            if browser:
                try:
                    cookie_path = stack.enter_context(browser_cookie_file(browser))
                except Exception as exc:
                    print(
                        f"warning: could not read {browser} cookies: "
                        f"{_friendly_error(exc)}; age-restricted tracks may fail",
                        file=sys.stderr,
                    )
                else:
                    args.cookies = cookie_path
            if args.command in _STATE_COMMANDS:
                stack.enter_context(_run_lock())
                if not getattr(args, "tui", False):
                    from packrat.logging_config import setup_logging
                    setup_logging(tui_active=False)
            return args.func(args)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"error: {_friendly_error(exc)}", file=sys.stderr)
        return 1


def _print_group_help(parser: argparse.ArgumentParser, name: str) -> None:
    """Print the help for a command group instead of a missing-argument error."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction) and name in action.choices:
            action.choices[name].print_help()
            return


def _friendly_error(exc: Exception) -> str:
    """Keep auth/API failures readable instead of dumping a raw API response."""
    name = type(exc).__name__
    if name.startswith("YTMusic"):
        return "authentication may be expired or missing; run 'packrat auth'"
    if isinstance(exc, KeyError):
        return "unexpected YouTube Music response; try again (or update ytmusicapi)"
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    if isinstance(exc, (ValueError, FileNotFoundError, OSError)):
        return _redact(text[:300]) if text else name
    return f"{name}: {_redact(text[:300])}" if text else name




if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
