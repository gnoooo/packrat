"""Throughput history for time estimates (runs.jsonl).

Records how long a batch download actually took so future catalogs can
estimate download time from measured throughput instead of the duration
model. Contains only counts and timings — no auth data, ever.
"""

from __future__ import annotations

import json
import pathlib
import statistics
import time

from packrat import paths

RUNS_NAME = "runs.jsonl"
RUNS_PATH = paths.RUNS_PATH


def _runs_path(output_root: pathlib.Path) -> pathlib.Path:
    # output_root is kept in the signature for callers, but throughput state
    # belongs to the user's config, not to a particular library folder.
    return RUNS_PATH


def record_run(
    output_root: pathlib.Path,
    *,
    mode: str,
    tracks: int,
    downloaded: int,
    seconds: float,
) -> None:
    """Append one batch result line to playlists/runs.jsonl."""
    line = {
        "ts": time.time(),
        "mode": mode,
        "tracks": tracks,
        "downloaded": downloaded,
        "seconds": round(seconds, 1),
    }
    path = _runs_path(output_root)
    paths.ensure_config_dir()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")


def last_measured(output_root: pathlib.Path, *, max_runs: int = 5) -> float | None:
    """Median seconds-per-downloaded-track over the last usable runs, or None."""
    try:
        lines = _runs_path(output_root).read_text(encoding="utf-8").splitlines()
    except OSError:
        # Legacy runs.jsonl lived in <output>/playlists/.
        legacy = pathlib.Path(output_root) / "playlists" / RUNS_NAME
        try:
            lines = legacy.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
    samples: list[float] = []
    for line in reversed(lines):
        try:
            data = json.loads(line)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if data.get("downloaded", 0) > 0 and data.get("seconds", 0) > 0:
            samples.append(data["seconds"] / data["downloaded"])
        if len(samples) >= max_runs:
            break
    if not samples:
        return None
    return float(statistics.median(samples))
