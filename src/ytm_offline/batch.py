"""Generic batch download loop used by sync, retry, albums and queues."""

from __future__ import annotations

import pathlib
import random
import time
from typing import Any, Callable

from ytm_offline.archive import Archive
from ytm_offline.control import Control
from ytm_offline.download import download_track
from ytm_offline.naming import SINGLES_DIR_DEFAULT
from ytm_offline import runs


def _emit(
    callback: Callable[[dict[str, Any]], None] | None,
    type_: str,
    payload: dict[str, Any],
) -> None:
    if callback is None:
        return
    callback({"type": type_, "payload": payload})


def run_batch_download(
    entries: list[dict[str, Any]],
    output_root: pathlib.Path,
    save_callback: Callable[[list[dict[str, Any]]], None] | None = None,
    *,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
    browser_profile: str | None = None,
    max_size: int | None = None,
    delay: float = 7.0,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    control: Control | None = None,
    archive: Archive | None = None,
    archive_path: str | pathlib.Path | None = None,
    mode: str = "batch",
) -> dict[str, Any]:
    """Download a list of manifest entries sequentially.

    Mutates entries in place (status, error, path). Emits events for the TUI.
    Calls save_callback after each completed track for incremental persistence.
    """
    if archive is None and archive_path is not None:
        archive = Archive(archive_path)
    started = time.monotonic()

    _emit(
        event_callback,
        "start",
        {
            "mode": mode,
            "total": len(entries),
            "max_size_bytes": max_size,
            "total_est_bytes": sum((e.get("est_size") or 0) for e in entries),
        },
    )

    failures: list[str] = []
    consecutive_retryable = 0
    stop_early = False

    for i, entry in enumerate(entries, start=1):
        if control and control.should_stop():
            stop_early = True
            entry["error"] = "batch stopped by user"
            break

        if control:
            control.wait_while_paused(
                callback=lambda: _emit(
                    event_callback,
                    "delay",
                    {"remaining": delay, "paused": True},
                )
            )

        track = {
            "video_id": entry["video_id"],
            "title": entry.get("title", ""),
            "artist": entry.get("artist", ""),
            "album": entry.get("album", ""),
            "track_num": entry.get("track_num"),
            "duration": entry.get("duration", 0),
            "source": entry.get("source", mode),
            "release_type": entry.get("release_type"),
            "singles_dir": entry.get("singles_dir", SINGLES_DIR_DEFAULT),
        }

        result = download_track(
            track,
            output_root=output_root,
            cookies=cookies,
            cookies_from_browser=cookies_from_browser,
            browser_profile=browser_profile,
            max_size=max_size,
            max_retries=3,
            event_callback=event_callback,
            control=control,
        )

        entry["status"] = result.status
        if result.error:
            entry["error"] = result.error
        if result.status == "downloaded":
            entry["path"] = str(result.filepath)
            if archive is not None:
                archive.add(entry["video_id"])
                archive.save()
            consecutive_retryable = 0
        elif result.retryable:
            consecutive_retryable += 1
            failures.append(entry["video_id"])
            if consecutive_retryable >= 3:
                _emit(
                    event_callback,
                    "status",
                    {
                        "status": "STOPPING",
                        "reason": "3 consecutive retryable errors (403/429/bot-check)",
                    },
                )
                stop_early = True
                break
        else:
            consecutive_retryable = 0
            failures.append(entry["video_id"])

        if result.status == "skipped_size":
            consecutive_retryable = 0

        if save_callback is not None:
            save_callback(entries)

        if i < len(entries) and not stop_early and not (control and control.should_stop()):
            if delay > 0:
                slept = 0.0
                step = 0.2
                while slept < delay:
                    if control and control.should_stop():
                        stop_early = True
                        break
                    remaining = max(0.0, delay - slept)
                    _emit(event_callback, "delay", {"remaining": remaining})
                    time.sleep(min(step, remaining))
                    slept += step

    if stop_early:
        for e in entries:
            if e.get("status") == "pending":
                e["error"] = e.get("error") or "batch stopped"

    if save_callback is not None:
        save_callback(entries)

    downloaded_count = sum(1 for e in entries if e.get("status") == "downloaded")
    runs.record_run(
        output_root,
        mode=mode,
        tracks=len(entries),
        downloaded=downloaded_count,
        seconds=time.monotonic() - started,
    )

    _emit(
        event_callback,
        "done",
        {"reason": "stopped" if stop_early else "completed"},
    )

    return {
        "total": len(entries),
        "downloaded": sum(1 for e in entries if e.get("status") == "downloaded"),
        "archived": sum(1 for e in entries if e.get("status") == "archived"),
        "skipped_size": sum(1 for e in entries if e.get("status") == "skipped_size"),
        "failed": sum(1 for e in entries if e.get("status") == "failed"),
        "skipped": sum(1 for e in entries if e.get("status") == "skipped"),
        "failures": failures,
        "stopped": stop_early,
    }
