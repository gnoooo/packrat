"""Command-line interface for ytm_offline."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
from typing import Sequence

from ytmusicapi import YTMusic

from ytm_offline import albums as albums_module
from ytm_offline import artists as artists_module
from ytm_offline import discover as discover_module
from ytm_offline import discover_phase4 as discover_phase4_module
from ytm_offline import queues as queues_module
from ytm_offline import sync as sync_module
from ytm_offline import tui as tui_module
from ytm_offline.batch import run_batch_download
from ytm_offline.manifest import Manifest
from ytm_offline.playlist import write_m3u8
from ytm_offline.sizes import format_size, parse_size
from ytm_offline.sync import _cache_browser_cookies


def _redact(msg: object) -> str:
    """Mask sensitive token values from messages that may be logged."""
    text = str(msg)
    patterns = [
        (r"(?i)(Cookie:\s*)[^\s\r\n]+", r"\1<redacted>"),
        (r"(?i)(Authorization:\s*)[^\s\r\n]+", r"\1<redacted>"),
        (r"(?i)(SAPISID\s*=\s*)[^\s;,&]+", r"\1<redacted>"),
    ]
    for pat, repl in patterns:
        text = re.sub(pat, repl, text)
    return text


def _headers_default() -> str | None:
    return os.environ.get("YTM_HEADERS")


def _cookies_default() -> str | None:
    return os.environ.get("YTM_COOKIES")


def load_ytmusic(headers_path: str) -> YTMusic:
    """Load a ytmusicapi browser-headers auth file."""
    p = pathlib.Path(headers_path)
    if not p.exists():
        raise FileNotFoundError(f"headers file not found: {p}")
    return YTMusic(str(p))


def cmd_auth(args: argparse.Namespace) -> int:
    """Validate ytmusicapi auth and print a short account summary."""
    try:
        yt = load_ytmusic(args.headers)
    except FileNotFoundError as exc:
        print(f"auth failed: {exc}", file=sys.stderr)
        return 1

    try:
        yt.get_library_playlists(limit=1)
        liked = yt.get_liked_songs(limit=1)
    except Exception as exc:
        cls = exc.__class__.__name__
        print(
            f"auth failed: {cls} (headers may be expired or invalid)",
            file=sys.stderr,
        )
        return 1

    if not liked.get("tracks"):
        print("auth OK, but library appears empty")
        return 0

    print("auth OK")
    return 0


def cmd_library(args: argparse.Namespace) -> int:
    """List library playlists."""
    yt = load_ytmusic(args.headers)
    playlists = yt.get_library_playlists(limit=args.limit)
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


def cmd_download_one(args: argparse.Namespace) -> int:
    """Download a single track by video ID and verify the result."""
    deno_ok, deno_version = _check_deno()
    if not deno_ok:
        found = f"found {deno_version!r}" if deno_version else "not found"
        print(
            f"deno >=2.3.0 is required but {found}. "
            "Install Deno and try again; the sandbox will not install it.",
            file=sys.stderr,
        )
        return 1

    if not args.cookies and not args.cookies_from_browser:
        print(
            "download-one requires --cookies or --cookies-from-browser",
            file=sys.stderr,
        )
        return 1

    output_root = pathlib.Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)

    import yt_dlp

    logger = _YtdlpLogger()
    url = f"https://music.youtube.com/watch?v={args.video_id}"

    ydl_opts: dict = {
        "format": "bestaudio[ext=m4a]/bestaudio",
        "outtmpl": str(
            output_root / "%(artist,uploader)s" / "%(album|Unknown Album)s" / "%(title)s.%(ext)s"
        ),
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
        ydl_opts["cookiesfrombrowser"] = (
            args.cookies_from_browser.lower(),
            args.browser_profile,
            None,
            None,
        )

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        print(f"download failed: {_redact(exc.msg)}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"download failed: {_redact(exc)}", file=sys.stderr)
        return 1

    downloaded = pathlib.Path(ydl.prepare_filename(info))
    if not downloaded.exists():
        print(f"download failed: expected file not found: {downloaded}", file=sys.stderr)
        return 1

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

    temp_cookie_file: str | None = None
    try:
        if args.cookies_from_browser and not args.cookies:
            print(f"caching cookies from {args.cookies_from_browser}...")
            args.cookies = _cache_browser_cookies(
                args.cookies_from_browser, args.browser_profile
            )
            args.cookies_from_browser = None
            temp_cookie_file = args.cookies

        sources = [s.strip() for s in args.source.split(",") if s.strip()]
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
            browser_profile=args.browser_profile,
            max_size=args.max_size,
            dry_run=args.dry_run,
            output_root=str(output_root),
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

        if not args.dry_run:
            print("\ndownloading discovered tracks...")
            return discover_module.download_pending(
                manifest=manifest,
                output_root=output_root,
                cookies=args.cookies,
                cookies_from_browser=args.cookies_from_browser,
                browser_profile=args.browser_profile,
                archive_path=args.archive,
                delay=args.delay,
            )
        return 0
    finally:
        if temp_cookie_file:
            try:
                os.unlink(temp_cookie_file)
            except OSError:
                pass


def cmd_discover_phase4(args: argparse.Namespace) -> int:
    """Discover taste-adjacent tracks beyond YT Music's own sections."""
    if not args.dry_run:
        print("discover-phase4 currently supports dry-run only; add --dry-run", file=sys.stderr)
        return 1

    output_root = pathlib.Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)

    discover_manifest_path = None
    if args.include_phase3:
        discover_manifest_path = output_root / "playlists" / "discover.manifest.json"

    quotas = {
        "deep_cuts": args.quota_deep,
        "adjacent": args.quota_adjacent,
        "radio": args.quota_radio,
        "external_graph": args.quota_external,
        "tag_search": args.quota_tag,
    }

    manifest, summary = discover_phase4_module.run_discover_phase4(
        headers_path=args.headers,
        output_root=str(output_root),
        archive_path=args.archive,
        discover_manifest_path=str(discover_manifest_path) if discover_manifest_path else None,
        quotas=quotas,
        per_container=args.per_container,
        per_artist=args.per_artist,
        min_duration=args.min_duration,
        max_duration=args.max_duration,
        max_size=args.max_size,
        dry_run=True,
    )

    manifest_path = output_root / "playlists" / "discover-phase4.manifest.json"
    manifest.write(manifest_path, "json")

    print(f"\nphase 4 profile: {output_root / 'playlists' / 'profile.json'}")
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
    if args.tui and not args.dry_run:
        return tui_module.run_tui(
            sync_module.run_sync,
            {"args": args},
            mode=args.source,
            ascii_mode=args.ascii,
        )
    return sync_module.run_sync(args)


def cmd_retry(args: argparse.Namespace) -> int:
    """Retry failed tracks from a manifest."""
    if args.tui:
        return tui_module.run_tui(
            sync_module.run_retry,
            {"args": args},
            mode="retry",
            ascii_mode=args.ascii,
        )
    return sync_module.run_retry(args)


def cmd_download_manifest(args: argparse.Namespace) -> int:
    """Resume downloads from any existing manifest file."""
    manifest_path = pathlib.Path(args.manifest)
    if not manifest_path.exists():
        print(f"manifest not found: {manifest_path}", file=sys.stderr)
        return 1
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"cannot read manifest: {exc}", file=sys.stderr)
        return 1

    output_root = pathlib.Path(args.output) if args.output else manifest_path.parent.parent
    output_root.mkdir(parents=True, exist_ok=True)

    # Artist manifests carry their own label and playlist layout.
    if payload.get("command") == "artist" or "label" in payload:
        label = payload.get("label") or payload.get("artist", manifest_path.stem)
        kwargs: dict[str, Any] = {
            "label": label,
            "output_root": output_root,
            "archive_path": args.archive,
            "cookies": args.cookies,
            "cookies_from_browser": args.cookies_from_browser,
            "browser_profile": args.browser_profile,
            "max_size": args.max_size,
            "delay": args.delay,
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
        if "error" in result:
            print(result["error"], file=sys.stderr)
            return 1
        summary = result["summary"]
        print(
            f"downloaded {summary['downloaded']}  archived {summary['archived']}"
            f"  skipped-size {summary['skipped_size']}  failed {summary['failed']}"
            f"  stopped {summary['stopped']}"
        )
        print(f"manifest: {result['manifest_path']}")
        print(f"playlists: {result['m3u8_dir']}")
        return 1 if (summary["failed"] or summary["stopped"]) else 0

    # Generic manifest (sync, discover, albums, queues, phase4, radio, ...).
    manifest = Manifest.load(manifest_path)
    entries = manifest.pending()
    if not entries:
        print("no pending tracks in manifest")
        return 0

    total_size = sum((e.get("est_size") or 0) for e in entries)
    print(f"pending tracks: {len(entries)}")
    print(f"estimated size: {format_size(total_size)}")

    if not args.yes:
        if sys.stdin.isatty():
            answer = input("download now? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("not downloading (use --yes to skip the prompt)")
                return 0
        else:
            print("not downloading (use --yes to skip the prompt)")
            return 0

    stem = manifest_path.stem
    if stem.endswith(".manifest"):
        stem = stem[: -len(".manifest")]
    m3u8_path = output_root / "playlists" / f"{stem}.m3u8"

    def save_callback(current_entries: list[dict[str, Any]]) -> None:
        manifest.entries = current_entries
        manifest.write(manifest_path, "json")
        downloaded = [
            e for e in current_entries
            if e.get("status") == "downloaded" and e.get("path")
        ]
        if downloaded:
            write_m3u8(
                m3u8_path,
                [
                    {
                        "path": e["path"],
                        "duration": e.get("duration", 0),
                        "title": e["title"],
                        "artist": e["artist"],
                    }
                    for e in downloaded
                ],
            )

    if args.tui:
        return tui_module.run_tui(
            run_batch_download,
            {
                "entries": entries,
                "output_root": output_root,
                "save_callback": save_callback,
                "cookies": args.cookies,
                "cookies_from_browser": args.cookies_from_browser,
                "browser_profile": args.browser_profile,
                "max_size": parse_size(args.max_size) if args.max_size else None,
                "delay": args.delay,
                "archive_path": args.archive,
                "mode": "manifest",
            },
            mode="manifest",
            ascii_mode=args.ascii,
        )

    summary = run_batch_download(
        entries,
        output_root=output_root,
        save_callback=save_callback,
        cookies=args.cookies,
        cookies_from_browser=args.cookies_from_browser,
        browser_profile=args.browser_profile,
        max_size=parse_size(args.max_size) if args.max_size else None,
        delay=args.delay,
        archive_path=args.archive,
        mode="manifest",
    )
    print(
        f"downloaded {summary['downloaded']}  archived {summary['archived']}"
        f"  skipped-size {summary['skipped_size']}  failed {summary['failed']}"
        f"  stopped {summary['stopped']}"
    )
    print(f"manifest: {manifest_path}")
    print(f"playlist: {m3u8_path}")
    return 1 if (summary["failed"] or summary["stopped"]) else 0


def cmd_artist(args: argparse.Namespace) -> int:
    """Discover and download one or more artists' full catalogs."""
    specs = list(args.artists)
    if args.from_file:
        for line in pathlib.Path(args.from_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                specs.append(line)
    if not specs:
        print("no artist specified", file=sys.stderr)
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
        if args.pick:
            if pick_used:
                print(
                    f"ambiguous match for '{spec}': --pick already used;"
                    " use a channel id/URL instead",
                    file=sys.stderr,
                )
                return 2
            pick_used = True
            resolved.append(candidates[args.pick - 1])
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
            print("use --pick N", file=sys.stderr)
            return 2

    catalogs: list[dict[str, Any]] = []
    output_root = pathlib.Path(args.output)
    for chosen in resolved:
        print(f"resolving catalog for {chosen['browseId']} ...")
        catalog = artists_module.build_catalog(
            args.headers,
            chosen["browseId"],
            output_root,
            args.archive,
            include_live=args.include_live,
            include_compilations=args.include_compilations,
            include_remixes=args.include_remixes,
            albums_only=args.albums_only,
            no_singles=args.no_singles,
            duration_window=args.duration_window,
            scope=args.scope,
            top_limit=args.top_limit,
            max_per_album=args.max_per_album,
            max_size=args.max_size,
            delay=args.delay,
        )
        artists_module.save_manifest(catalog["payload"])
        catalogs.append(catalog)

        print(f"artist: {catalog['artist']}")
        subs = catalog.get("subscribers")
        if subs:
            print(f"subscribers: {subs}")
        for meta in catalog["payload"]["releases"]:
            title = f"{meta['title']} ({meta.get('year') or '?'}, {meta.get('type', '?')})"
            if meta["status"] == "kept":
                print(f"  KEEP {title}: {meta['tracks']} tracks, {meta['new_tracks']} new")
            elif meta["reason"] == "unavailable":
                print(f"  SKIP {title}: unavailable")
            else:
                print(f"  skip {title}: {meta['reason']}")
        totals = catalog["totals"]
        print(
            f"kept releases: {totals['releases_kept']}  excluded: {totals['releases_excluded']}"
        )
        print(f"new tracks: {totals['new_tracks']}  already owned: {totals['already_owned']}")
        if totals.get("skipped_duration"):
            print(f"outside --duration-window: {totals['skipped_duration']}")
        rule_bits = [f"{k}={v}" for k, v in totals["excluded_counts"].items() if v]
        if rule_bits:
            print(f"excluded by rule: {', '.join(rule_bits)}")
        print(f"estimated size: {totals['est_human']}")
        print(f"estimated time: ~{totals['est_time_seconds'] // 60} min")
        top_totals = catalog["payload"].get("top_songs")
        if top_totals:
            print(
                f"top songs: {top_totals['new_tracks']} new, "
                f"{top_totals['already_owned']} owned, "
                f"{top_totals['est_human']}"
            )
        print(f"manifest: {catalog['manifest_path']}", end="\n\n")

    if args.dry_run:
        print("dry-run: manifest written, no downloads.")
        return 0

    approved = args.yes
    if not approved and sys.stdin.isatty():
        answer = input("download now? [y/N] ").strip().lower()
        approved = answer in ("y", "yes")
    if not approved:
        print("not downloading (use --yes to skip the prompt)")
        return 0

    exit_code = 0
    for catalog in catalogs:
        label = catalog["label"]
        print(f"downloading catalog: {catalog['artist']}")
        kwargs = {
            "label": label,
            "output_root": output_root,
            "archive_path": args.archive,
            "cookies": args.cookies,
            "cookies_from_browser": args.cookies_from_browser,
            "browser_profile": args.browser_profile,
            "max_size": args.max_size,
            "delay": args.delay,
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
        if "error" in result:
            print(result["error"], file=sys.stderr)
            exit_code = 1
            continue
        summary = result["summary"]
        print(
            f"downloaded {summary['downloaded']}  archived {summary['archived']}"
            f"  skipped-size {summary['skipped_size']}  failed {summary['failed']}"
            f"  stopped {summary['stopped']}"
        )
        print(f"manifest: {result['manifest_path']}")
        print(f"playlists: {result['m3u8_dir']}")
        if summary["failed"] or summary["stopped"]:
            exit_code = 1
    return exit_code


def cmd_radio(args: argparse.Namespace) -> int:
    """Download the radio from one track URL or id. --dry-run only lists it."""
    kwargs = {
        "seed_spec": args.seed,
        "headers_path": args.headers,
        "output_root": pathlib.Path(args.output),
        "archive_path": args.archive,
        "limit": args.limit,
        "per_artist_per_queue": args.per_artist,
        "min_duration": args.min_duration,
        "max_duration": args.max_duration,
        "dry_run": args.dry_run,
        "cookies": args.cookies,
        "cookies_from_browser": args.cookies_from_browser,
        "browser_profile": args.browser_profile,
        "max_size": args.max_size,
        "delay": args.delay,
    }
    if args.tui:
        result = tui_module.run_tui(
            queues_module.build_radio,
            kwargs,
            mode="radio",
            ascii_mode=args.ascii,
        )
    else:
        result = queues_module.build_radio(**kwargs)
    if "error" in result:
        print(result["error"], file=sys.stderr)
        return 1
    print(f"radio seed: {result['seed']} ({result['seed_video_id']})")
    print(f"tracks: {result['total_tracks']}  est size: {result['total_est_size_human']}")
    if "summary" in result:
        s = result["summary"]
        print(
            f"downloaded {s['downloaded']}  archives {s['archived']}"
            f"  skipped-size {s['skipped_size']}  failed {s['failed']}"
            f"  stopped {s['stopped']}"
        )
        print(f"manifest: {result['manifest_path']}")
        print(f"playlist: {result['m3u8_path']}")
        return 1 if (s["failed"] or s["stopped"]) else 0
    for t in result["tracks"]:
        print(f"  {t}")
    return 0


def cmd_discover_queues(args: argparse.Namespace) -> int:
    """Build playback-queue playlists from liked-track seeds."""
    output_root = pathlib.Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)

    if args.download:
        if args.tui:
            return tui_module.run_tui(
                queues_module.download_queues,
                {
                    "output_root": output_root,
                    "archive_path": args.archive,
                    "cookies": args.cookies,
                    "cookies_from_browser": args.cookies_from_browser,
                    "browser_profile": args.browser_profile,
                    "max_size": args.max_size,
                    "delay": args.delay,
                },
                mode="queues",
                ascii_mode=args.ascii,
            )
        dl_result = queues_module.download_queues(
            output_root=output_root,
            archive_path=args.archive,
            cookies=args.cookies,
            cookies_from_browser=args.cookies_from_browser,
            browser_profile=args.browser_profile,
            max_size=args.max_size,
            delay=args.delay,
        )
        if "error" in dl_result:
            print(dl_result["error"], file=sys.stderr)
            return 1
        print(f"queues download complete: {dl_result['manifest_path']}")
        return 0

    result = queues_module.discover_queues(
        headers_path=args.headers,
        output_root=output_root,
        archive_path=args.archive,
        seed_count=args.seed_count,
        hop_depth=args.hop_depth,
        hop_width=args.hop_width,
        per_artist_per_queue=args.per_artist_per_queue,
        per_queue_limit=args.per_queue_limit,
        total_cap=args.total_cap,
        min_duration=args.min_duration,
        max_duration=args.max_duration,
        random_seed=args.random_seed,
        dry_run=True,
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
    return 0


def cmd_discover_albums(args: argparse.Namespace) -> int:
    """Discover whole albums from top liked artists."""
    output_root = pathlib.Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)

    if args.download:
        if args.tui:
            return tui_module.run_tui(
                albums_module.download_albums,
                {
                    "output_root": output_root,
                    "archive_path": args.archive,
                    "cookies": args.cookies,
                    "cookies_from_browser": args.cookies_from_browser,
                    "browser_profile": args.browser_profile,
                    "max_size": args.max_size,
                    "delay": args.delay,
                },
                mode="albums",
                ascii_mode=args.ascii,
            )
        dl_result = albums_module.download_albums(
            output_root=output_root,
            archive_path=args.archive,
            cookies=args.cookies,
            cookies_from_browser=args.cookies_from_browser,
            browser_profile=args.browser_profile,
            max_size=args.max_size,
            delay=args.delay,
        )
        if "error" in dl_result:
            print(dl_result["error"], file=sys.stderr)
            return 1
        print(f"albums download complete: {dl_result['manifest_path']}")
        return 0

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
        dry_run=True,
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
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ytm-offline")
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose logging")
    parser.add_argument(
        "--headers",
        default=_headers_default(),
        help="path to ytmusicapi browser-headers JSON auth file (env: YTM_HEADERS)",
    )
    parser.add_argument(
        "--cookies",
        default=_cookies_default(),
        help="path to Netscape cookies.txt for yt-dlp (env: YTM_COOKIES)",
    )
    parser.add_argument(
        "--cookies-from-browser",
        dest="cookies_from_browser",
        help="browser name/profile for yt-dlp --cookies-from-browser",
    )
    parser.add_argument(
        "--browser-profile",
        dest="browser_profile",
        help="Firefox profile name/path for --cookies-from-browser",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="use the curses TUI for supported download commands",
    )
    parser.add_argument(
        "--ascii",
        action="store_true",
        help="use ASCII progress bars in the TUI",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_auth = sub.add_parser("auth", help="validate ytmusicapi auth")
    p_auth.set_defaults(func=cmd_auth)

    p_library = sub.add_parser("library", help="list library playlists")
    p_library.add_argument("--limit", type=int, default=25, help="max playlists to show")
    p_library.set_defaults(func=cmd_library)

    p_download_one = sub.add_parser("download-one", help="download and verify one track by video ID")
    p_download_one.add_argument("video_id", help="YouTube Music video ID")
    p_download_one.add_argument("--output", "-o", default="music", help="output root directory")
    p_download_one.set_defaults(func=cmd_download_one)

    p_sync = sub.add_parser("sync", help="sync liked songs or a playlist")
    # default=SUPPRESS so a subcommand flag never clobbers a --tui given before it.
    p_sync.add_argument("--tui", action="store_true", default=argparse.SUPPRESS, help="use curses TUI")
    p_sync.add_argument("--ascii", action="store_true", default=argparse.SUPPRESS, help="use ASCII progress bars in TUI")
    p_sync.add_argument(
        "--source",
        required=True,
        help="liked or playlist:<id/url>",
    )
    p_sync.add_argument("--limit", type=int, default=None, help="max tracks to sync (default: all)")
    p_sync.add_argument("--output", "-o", default="music", help="output root directory")
    p_sync.add_argument("--archive", default=".ytm_offline_archive", help="download archive file")
    p_sync.add_argument("--delay", type=float, default=7.0, help="seconds between downloads")
    p_sync.add_argument("--max-size", help="skip tracks estimated larger than SIZE (e.g. 3GB)")
    p_sync.add_argument("--cookies", default=_cookies_default(), help="path to Netscape cookies.txt for yt-dlp (env: YTM_COOKIES)")
    p_sync.add_argument("--cookies-from-browser", dest="cookies_from_browser", help="browser name/profile for yt-dlp --cookies-from-browser")
    p_sync.add_argument("--browser-profile", dest="browser_profile", help="Firefox profile name/path for --cookies-from-browser")
    p_sync.add_argument("--dry-run", action="store_true", help="write manifest without downloading")
    p_sync.add_argument("--manifest-format", choices=["json", "csv"], default="json")
    p_sync.set_defaults(func=cmd_sync)

    p_retry = sub.add_parser("retry", help="retry failed tracks from a manifest")
    p_retry.add_argument("--tui", action="store_true", default=argparse.SUPPRESS, help="use curses TUI")
    p_retry.add_argument("--ascii", action="store_true", default=argparse.SUPPRESS, help="use ASCII progress bars in TUI")
    p_retry.add_argument("--manifest", required=True, help="path to manifest JSON from a previous sync")
    p_retry.add_argument("--output", "-o", default="music", help="output root directory")
    p_retry.add_argument("--archive", default=".ytm_offline_archive", help="download archive file")
    p_retry.add_argument("--delay", type=float, default=7.0, help="seconds between downloads")
    p_retry.add_argument("--max-size", help="skip tracks estimated larger than SIZE")
    p_retry.add_argument("--cookies", default=_cookies_default(), help="path to Netscape cookies.txt for yt-dlp (env: YTM_COOKIES)")
    p_retry.add_argument("--cookies-from-browser", dest="cookies_from_browser", help="browser name/profile for yt-dlp --cookies-from-browser")
    p_retry.add_argument("--browser-profile", dest="browser_profile", help="Firefox profile name/path for --cookies-from-browser")
    p_retry.set_defaults(func=cmd_retry)

    p_discover = sub.add_parser("discover", help="discover tracks from Home/Charts/Moods/New Releases")
    p_discover.add_argument(
        "--source",
        default="home,charts,moods,new_releases",
        help="comma-separated discovery sources",
    )
    p_discover.add_argument("--country", default="FR", help="country for charts")
    p_discover.add_argument("--quota", type=int, default=10, help="max tracks per source")
    p_discover.add_argument("--per-container", type=int, default=5, help="max tracks per container")
    p_discover.add_argument("--per-artist", type=int, default=3, help="max tracks per artist per source")
    p_discover.add_argument("--min-duration", type=int, default=90, help="minimum track duration in seconds")
    p_discover.add_argument("--max-duration", type=int, default=540, help="maximum track duration in seconds")
    p_discover.add_argument("--output", "-o", default="music", help="output root directory")
    p_discover.add_argument("--archive", default=".ytm_offline_archive", help="download archive file")
    p_discover.add_argument("--delay", type=float, default=7.0, help="seconds between downloads")
    p_discover.add_argument("--max-size", help="skip tracks estimated larger than SIZE")
    p_discover.add_argument("--dry-run", action="store_true", help="write manifest without downloading")
    p_discover.set_defaults(func=cmd_discover)

    p_dp4 = sub.add_parser("discover-phase4", help="discover taste-adjacent tracks beyond YT Music sections")
    p_dp4.add_argument("--quota-deep", type=int, default=12, help="max deep-cut tracks")
    p_dp4.add_argument("--quota-adjacent", type=int, default=10, help="max adjacent-artist tracks")
    p_dp4.add_argument("--quota-radio", type=int, default=10, help="max radio tracks")
    p_dp4.add_argument("--quota-external", type=int, default=5, help="max external-graph tracks")
    p_dp4.add_argument("--quota-tag", type=int, default=5, help="max tag-search tracks")
    p_dp4.add_argument("--per-container", type=int, default=2, help="max tracks per album/container")
    p_dp4.add_argument("--per-artist", type=int, default=3, help="max tracks per artist")
    p_dp4.add_argument("--min-duration", type=int, default=90, help="minimum track duration in seconds")
    p_dp4.add_argument("--max-duration", type=int, default=540, help="maximum track duration in seconds")
    p_dp4.add_argument("--output", "-o", default="music", help="output root directory")
    p_dp4.add_argument("--archive", default=".ytm_offline_archive", help="download archive file")
    p_dp4.add_argument("--max-size", help="skip tracks estimated larger than SIZE")
    p_dp4.add_argument("--include-phase3", action="store_true", default=True, help="dedupe against Phase 3 discover manifest")
    p_dp4.add_argument("--dry-run", action="store_true", help="write manifest without downloading")
    p_dp4.set_defaults(func=cmd_discover_phase4)

    p_queues = sub.add_parser("discover-queues", help="build playback-queue playlists from liked-track seeds")
    p_queues.add_argument("--tui", action="store_true", default=argparse.SUPPRESS, help="use curses TUI when downloading")
    p_queues.add_argument("--ascii", action="store_true", default=argparse.SUPPRESS, help="use ASCII progress bars in TUI")
    p_queues.add_argument("--seed-count", type=int, default=12, help="number of liked-track seeds")
    p_queues.add_argument("--hop-depth", type=int, default=1, help="queue hop depth")
    p_queues.add_argument("--hop-width", type=int, default=3, help="tracks to seed second-hop queues")
    p_queues.add_argument("--per-artist-per-queue", type=int, default=3, help="max tracks per artist within one queue")
    p_queues.add_argument("--per-queue-limit", type=int, default=25, help="max tracks per queue playlist")
    p_queues.add_argument("--total-cap", type=int, default=200, help="max total tracks across all queues")
    p_queues.add_argument("--min-duration", type=int, default=90, help="minimum track duration in seconds")
    p_queues.add_argument("--max-duration", type=int, default=540, help="maximum track duration in seconds")
    p_queues.add_argument("--random-seed", type=int, default=None, help="seed for reproducible seed selection")
    p_queues.add_argument("--output", "-o", default="music", help="output root directory")
    p_queues.add_argument("--archive", default=".ytm_offline_archive", help="download archive file")
    p_queues.add_argument("--delay", type=float, default=7.0, help="seconds between downloads")
    p_queues.add_argument("--max-size", help="skip tracks estimated larger than SIZE")
    p_queues.add_argument("--cookies", default=_cookies_default(), help="path to Netscape cookies.txt for yt-dlp (env: YTM_COOKIES)")
    p_queues.add_argument("--cookies-from-browser", dest="cookies_from_browser", help="browser name/profile for yt-dlp --cookies-from-browser")
    p_queues.add_argument("--browser-profile", dest="browser_profile", help="Firefox profile name/path for --cookies-from-browser")
    p_queues.add_argument("--download", action="store_true", help="download after building manifest (default: dry-run)")
    p_queues.set_defaults(func=cmd_discover_queues)

    p_radio = sub.add_parser("radio", help="download the auto radio built from one track URL/id")
    p_radio.add_argument("seed", help="video id or URL (music.youtube.com/watch?v=... or youtu.be/...)")
    p_radio.add_argument("--tui", action="store_true", default=argparse.SUPPRESS, help="use curses TUI")
    p_radio.add_argument("--ascii", action="store_true", default=argparse.SUPPRESS, help="use ASCII progress bars in TUI")
    p_radio.add_argument("--limit", type=int, default=50, help="max tracks in the radio")
    p_radio.add_argument("--per-artist", type=int, default=3, help="max tracks per artist in the radio")
    p_radio.add_argument("--min-duration", type=int, default=90, help="minimum track duration in seconds")
    p_radio.add_argument("--max-duration", type=int, default=540, help="maximum track duration in seconds")
    p_radio.add_argument("--output", "-o", default="music", help="output root directory")
    p_radio.add_argument("--archive", default=".ytm_offline_archive", help="download archive file")
    p_radio.add_argument("--delay", type=float, default=7.0, help="seconds between downloads")
    p_radio.add_argument("--max-size", help="skip tracks estimated larger than SIZE")
    p_radio.add_argument("--cookies", default=_cookies_default(), help="path to Netscape cookies.txt for yt-dlp (env: YTM_COOKIES)")
    p_radio.add_argument("--cookies-from-browser", dest="cookies_from_browser", help="browser name/profile for yt-dlp --cookies-from-browser")
    p_radio.add_argument("--browser-profile", dest="browser_profile", help="Firefox profile name/path for --cookies-from-browser")
    p_radio.add_argument("--download", action="store_true", help=argparse.SUPPRESS)  # kept for compat
    p_radio.add_argument("--dry-run", action="store_true", help="list the radio without downloading")
    p_radio.set_defaults(func=cmd_radio)

    p_manifest = sub.add_parser("download-manifest", help="resume downloads from an existing manifest")
    p_manifest.add_argument("--manifest", required=True, help="path to a manifest JSON file")
    p_manifest.add_argument("--output", "-o", default=None, help="output root directory (default: parent of playlists/)")
    p_manifest.add_argument("--yes", action="store_true", help="download without asking")
    p_manifest.add_argument("--tui", action="store_true", default=argparse.SUPPRESS, help="use curses TUI")
    p_manifest.add_argument("--ascii", action="store_true", default=argparse.SUPPRESS, help="use ASCII progress bars in TUI")
    p_manifest.add_argument("--archive", default=".ytm_offline_archive", help="download archive file")
    p_manifest.add_argument("--delay", type=float, default=7.0, help="seconds between downloads")
    p_manifest.add_argument("--max-size", help="skip tracks estimated larger than SIZE")
    p_manifest.add_argument("--cookies", default=_cookies_default(), help="path to Netscape cookies.txt for yt-dlp (env: YTM_COOKIES)")
    p_manifest.add_argument("--cookies-from-browser", dest="cookies_from_browser", help="browser name/profile for yt-dlp --cookies-from-browser")
    p_manifest.add_argument("--browser-profile", dest="browser_profile", help="Firefox profile name/path for --cookies-from-browser")
    p_manifest.set_defaults(func=cmd_download_manifest)

    p_artist = sub.add_parser("artist", help="download one or more artists' full catalogs")
    p_artist.add_argument("artists", nargs="+", help="artist name, channelId, or channel URL")
    p_artist.add_argument("--from-file", help="text file with one artist name/(channel id) per line")
    p_artist.add_argument("--pick", type=int, default=None, metavar="N", help="pick the Nth candidate from the search results")
    p_artist.add_argument("--include-live", action="store_true", help="include live releases")
    p_artist.add_argument("--include-compilations", action="store_true", help="include compilations")
    p_artist.add_argument("--include-remixes", action="store_true", help="include remix/instrumental releases")
    p_artist.add_argument("--albums-only", action="store_true", help="only albums (no EPs/singles)")
    p_artist.add_argument("--no-singles", action="store_true", help="exclude singles but keep EPs")
    p_artist.add_argument("--duration-window", action="store_true", help="only keep tracks between 90 s and 9 min")
    p_artist.add_argument("--scope", choices=["top-songs", "albums", "all"], default="all", help="what to download (default: all)")
    p_artist.add_argument("--top-songs", dest="scope", action="store_const", const="top-songs", help="shorthand for --scope top-songs")
    p_artist.add_argument("--top-limit", type=int, default=None, help="max top songs (default: all)")
    p_artist.add_argument("--max-per-album", type=int, default=None, help="cap kept versions per album in top songs")
    p_artist.add_argument("--yes", action="store_true", help="download without asking")
    p_artist.add_argument("--dry-run", action="store_true", help="catalog + manifest only, never download")
    p_artist.add_argument("--tui", action="store_true", default=argparse.SUPPRESS, help="use curses TUI")
    p_artist.add_argument("--ascii", action="store_true", default=argparse.SUPPRESS, help="use ASCII progress bars in TUI")
    p_artist.add_argument("--output", "-o", default="music", help="output root directory")
    p_artist.add_argument("--archive", default=".ytm_offline_archive", help="download archive file")
    p_artist.add_argument("--delay", type=float, default=7.0, help="seconds between downloads")
    p_artist.add_argument("--max-size", help="skip tracks estimated larger than SIZE")
    p_artist.add_argument("--cookies", default=_cookies_default(), help="path to Netscape cookies.txt for yt-dlp (env: YTM_COOKIES)")
    p_artist.add_argument("--cookies-from-browser", dest="cookies_from_browser", help="browser name/profile for yt-dlp --cookies-from-browser")
    p_artist.add_argument("--browser-profile", dest="browser_profile", help="Firefox profile name/path for --cookies-from-browser")
    p_artist.set_defaults(func=cmd_artist)

    p_albums = sub.add_parser("discover-albums", help="discover whole albums from top liked artists")
    p_albums.add_argument("--tui", action="store_true", default=argparse.SUPPRESS, help="use curses TUI when downloading")
    p_albums.add_argument("--ascii", action="store_true", default=argparse.SUPPRESS, help="use ASCII progress bars in TUI")
    p_albums.add_argument("--artist-top-k", type=int, default=15, help="number of top liked artists to consider")
    p_albums.add_argument("--albums-per-artist", type=int, default=2, help="max albums per artist")
    p_albums.add_argument("--include-singles", action="store_true", help="include singles/EPs")
    p_albums.add_argument("--min-duration", type=int, default=90, help="minimum track duration in seconds")
    p_albums.add_argument("--max-duration", type=int, default=540, help="maximum track duration in seconds")
    p_albums.add_argument("--output", "-o", default="music", help="output root directory")
    p_albums.add_argument("--archive", default=".ytm_offline_archive", help="download archive file")
    p_albums.add_argument("--delay", type=float, default=7.0, help="seconds between downloads")
    p_albums.add_argument("--max-size", help="skip tracks estimated larger than SIZE")
    p_albums.add_argument("--cookies", default=_cookies_default(), help="path to Netscape cookies.txt for yt-dlp (env: YTM_COOKIES)")
    p_albums.add_argument("--cookies-from-browser", dest="cookies_from_browser", help="browser name/profile for yt-dlp --cookies-from-browser")
    p_albums.add_argument("--browser-profile", dest="browser_profile", help="Firefox profile name/path for --cookies-from-browser")
    p_albums.add_argument("--download", action="store_true", help="download after building manifest (default: dry-run)")
    p_albums.set_defaults(func=cmd_discover_albums)

    return parser


_NEEDS_HEADERS = {"auth", "library", "sync", "discover", "discover-phase4", "discover-queues", "discover-albums", "radio", "artist"}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command in _NEEDS_HEADERS and not args.headers:
        print("--headers or YTM_HEADERS is required for this command", file=sys.stderr)
        return 1

    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
