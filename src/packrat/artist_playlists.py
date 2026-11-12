"""Artist playlist, manifest and download helpers."""

from __future__ import annotations

import json
import pathlib
from typing import Any

from packrat.batch import run_batch_download
from packrat.manifest import atomic_write_text
from packrat.naming import clean, is_single
from packrat.playlist import m3u8_entry, write_m3u8
from packrat.sizes import parse_size


def artist_all_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order entries by (release year, track number)."""

    def sort_key(entry: dict[str, Any]) -> tuple[int, int]:
        try:
            year = int(entry.get("release_year") or 0)
        except (TypeError, ValueError):
            year = 0
        try:
            num = int(entry.get("track_num") or 0)
        except (TypeError, ValueError):
            num = 0
        return (year if year else 9999, num)

    return sorted(entries, key=sort_key)


def write_artist_playlists(
    entries: list[dict[str, Any]],
    playlists_dir: pathlib.Path,
) -> list[str]:
    """Write one .m3u8 per downloaded album plus artist-all.m3u8."""
    downloaded = [
        e for e in entries if e.get("path") and e.get("status") == "downloaded"
    ]
    if not downloaded:
        return []

    written: list[str] = []
    playlists_dir.mkdir(parents=True, exist_ok=True)
    by_album: dict[str, list[dict[str, Any]]] = {}
    for entry in downloaded:
        if is_single(entry.get("album", ""), entry.get("release_type")):
            continue  # no per-single m3u8; they go into <artist>-singles.m3u8
        by_album.setdefault(entry.get("album") or "Unknown Album", []).append(entry)
    for album, tracks in by_album.items():
        tracks.sort(key=lambda x: int(x.get("track_num") or 0))
        m3u8_path = playlists_dir / f"{clean(album) or 'album'}.m3u8"
        write_m3u8(m3u8_path, [m3u8_entry(t) for t in tracks])
        written.append(str(m3u8_path))
    all_path = playlists_dir / "artist-all.m3u8"
    write_m3u8(all_path, [m3u8_entry(e) for e in artist_all_entries(downloaded)])
    written.append(str(all_path))
    single_downloaded = [
        e for e in downloaded if is_single(e.get("album", ""), e.get("release_type"))
    ]
    if single_downloaded:
        single_artist = single_downloaded[0].get("artist", "artist")
        singles_path_m3u8 = playlists_dir / f"{clean(single_artist) or 'artist'}-singles.m3u8"

        def singles_sort_key(entry: dict[str, Any]) -> tuple[int, str]:
            try:
                year = int(entry.get("release_year") or 0)
            except (TypeError, ValueError):
                year = 0
            return (year if year else 9999, str(entry.get("title", "")))

        ordered = sorted(single_downloaded, key=singles_sort_key)
        write_m3u8(singles_path_m3u8, [m3u8_entry(e) for e in ordered])
        written.append(str(singles_path_m3u8))
    top_downloaded = [e for e in downloaded if e.get("release_type") == "top"]
    if top_downloaded:
        top_artist = top_downloaded[0].get("artist", "artist")
        top_path = playlists_dir / f"{clean(top_artist) or 'artist'}-top.m3u8"
        # Popularity order = the order the top entries appear in the manifest.
        write_m3u8(top_path, [m3u8_entry(e) for e in top_downloaded])
        written.append(str(top_path))
    return written


def save_manifest(payload: dict[str, Any]) -> None:
    manifest_path = pathlib.Path(payload["manifest_path"])
    atomic_write_text(manifest_path, json.dumps(payload, indent=2))


def download_artist(
    label: str,
    output_root: pathlib.Path,
    archive_path: str,
    *,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
    browser_profile: str | None = None,
    max_size: str | None = None,
    delay: float = 7.0,
    event_callback=None,
    control=None,
    payload: dict[str, Any] | None = None,
    limit: int | None = None,
    manifest_path: str | pathlib.Path | None = None,
    failed_only: bool = False,
) -> dict[str, Any]:
    """Download pending tracks from an artist manifest.

    ``manifest_path`` wins when the caller loaded the payload from a specific
    file (download-manifest); otherwise ``artist-<label>.manifest.json`` is
    used.
    """
    resolved_manifest = (
        pathlib.Path(manifest_path)
        if manifest_path is not None
        else output_root / "playlists" / f"artist-{clean(label) or 'artist'}.manifest.json"
    )
    if payload is None:
        if not resolved_manifest.exists():
            return {"error": f"saved plan not found: {resolved_manifest}"}
        payload = json.loads(resolved_manifest.read_text(encoding="utf-8"))
    entries = payload.get("entries", [])
    wanted = "failed" if failed_only else "pending"
    pending = [e for e in entries if e.get("status") == wanted]
    if limit and len(pending) > limit:
        pending = pending[:limit]
    if not pending:
        return {"error": f"no {wanted} tracks in manifest"}

    playlists_dir = output_root / "playlists" / "artists" / (clean(label) or "artist")
    manifest_max_size = payload.get("max_size")
    if max_size:
        manifest_max_size = parse_size(max_size)

    def save_callback(current_entries: list[dict[str, Any]]) -> None:
        # current_entries is the pending subset; the original dicts are
        # mutated in place so payload["entries"] already reflects updates.
        atomic_write_text(
            resolved_manifest,
            json.dumps({k: v for k, v in payload.items()}, indent=2),
        )
        write_artist_playlists(payload["entries"], playlists_dir)

    summary = run_batch_download(
        pending,
        output_root=output_root,
        save_callback=save_callback,
        cookies=cookies,
        cookies_from_browser=cookies_from_browser,
        browser_profile=browser_profile,
        max_size=manifest_max_size,
        delay=delay,
        event_callback=event_callback,
        control=control,
        archive_path=archive_path,
        mode=f"artist:{label}",
    )
    return {
        "summary": summary,
        "manifest_path": str(resolved_manifest),
        "m3u8_dir": str(playlists_dir),
        "m3u8_paths": write_artist_playlists(entries, playlists_dir),
    }


