"""migrate-layout: move legacy single folders into the Singles layout."""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
from datetime import datetime, timezone
from typing import Any

from ytm_offline.naming import SINGLES_DIR_DEFAULT, clean, is_single, singles_path, track_path
from ytm_offline.playlist import m3u8_entry, write_m3u8

LOG_NAME = "migrate-layout.log.json"


def _find_manifests(root: pathlib.Path) -> list[pathlib.Path]:
    """All manifests under music/ that may reference downloaded files."""
    return sorted(root.rglob("*.manifest.json"))


def _load(path: pathlib.Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(path: pathlib.Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _resolve(root: pathlib.Path, value: str) -> pathlib.Path:
    p = pathlib.Path(value)
    return p if p.is_absolute() else root / p


def _regenerate_playlists(
    root: pathlib.Path,
    manifest_paths: list[pathlib.Path],
) -> list[str]:
    """Rewrite the m3u8 playlists owned by the given manifests."""
    from ytm_offline.artists import write_artist_playlists

    written: list[str] = []
    for mpath in manifest_paths:
        data = _load(mpath)
        entries = data.get("entries") or []
        downloaded = [
            e for e in entries
            if e.get("path") and pathlib.Path(e["path"]).exists()
        ]
        if data.get("command") == "artist" or data.get("label"):
            label = data.get("label") or clean(mpath.stem) or "artist"
            playlists_dir = root / "playlists" / "artists" / label
            written.extend(write_artist_playlists(entries, playlists_dir))
        elif downloaded:
            stem = mpath.stem
            if stem.endswith(".manifest"):
                stem = stem[: -len(".manifest")]
            wp = root / "playlists" / f"{stem}.m3u8"
            write_m3u8(
                wp,
                [m3u8_entry(e) for e in downloaded],
            )
            written.append(str(wp))
    return written


def _plan(
    root: pathlib.Path,
    manifests: list[pathlib.Path],
    default_singles_dir: str,
) -> tuple[list[dict[str, Any]], list[pathlib.Path]]:
    """Find eligible single-layout files per manifest entry.

    Returns (moves, ambiguous_dirs). Never mutates anything.
    """
    moves: list[dict[str, Any]] = []
    ambiguous: list[pathlib.Path] = []
    taken: set[str] = set()
    refs: set[str] = set()
    singles_names = {clean(SINGLES_DIR_DEFAULT), clean(default_singles_dir)}

    for mpath in manifests:
        data = _load(mpath)
        for entry in data.get("entries") or []:
            path = entry.get("path")
            if not path:
                continue
            old = _resolve(root, path)
            if not old.is_file() or not old.is_relative_to(root.resolve()):
                continue
            refs.add(str(old.resolve()))
            if not is_single(entry.get("album", ""), entry.get("release_type")):
                continue
            year = entry.get("release_year")
            if year in (None, 9999):
                year = None
            ideal = track_path(
                root,
                entry.get("artist", ""),
                entry.get("album", ""),
                entry.get("title", ""),
                entry.get("track_num"),
                release_type=entry.get("release_type"),
                singles_dir=entry.get("singles_dir", default_singles_dir),
            )
            if old == ideal:
                continue  # already in the new layout
            dst = singles_path(
                root,
                entry.get("artist", ""),
                entry.get("title", ""),
                year=year,
                singles_dir=entry.get("singles_dir", default_singles_dir),
                taken=taken,
            )
            taken.add(str(dst))
            moves.append(
                {
                    "src": str(old),
                    "dst": str(dst),
                    "manifest": str(mpath),
                    "video_id": entry.get("video_id", ""),
                    "collision": dst != ideal,
                }
            )

    # Single-track folders not referenced by any manifest are ambiguous.
    for artist_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if artist_dir.name in ("playlists", ".ytm_offline_archive"):
            continue
        for child in sorted(p for p in artist_dir.iterdir() if p.is_dir()):
            if child.name in singles_names:
                continue
            tracks = list(child.glob("*.m4a"))
            if len(tracks) != 1:
                continue
            if all(str(t) not in refs for t in tracks):
                ambiguous.append(child)
    return moves, ambiguous


def _rmdir_if_empty(path: pathlib.Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass


def _apply_moves(moves: list[dict[str, Any]]) -> None:
    for move in moves:
        src = pathlib.Path(move["src"])
        dst = pathlib.Path(move["dst"])
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        _rmdir_if_empty(src.parent)


def _confirm(yes: bool) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        return False
    return input("apply migration? [y/N] ").strip().lower() in ("y", "yes")


def run_migrate(
    output_root: str | pathlib.Path,
    *,
    dry_run: bool = False,
    yes: bool = False,
    undo: bool = False,
    singles_dir: str = SINGLES_DIR_DEFAULT,
) -> int:
    """Run `migrate-layout`; returns the process exit code."""
    root = pathlib.Path(output_root)
    if not root.exists():
        print(f"output root not found: {root}")
        return 1
    log_path = root / "playlists" / LOG_NAME

    if undo:
        return _undo(root, log_path)

    manifests = _find_manifests(root)
    moves, ambiguous = _plan(root, manifests, singles_dir)
    collisions = [m for m in moves if m.get("collision")]

    print(f"manifests scanned: {len(manifests)}")
    print(f"singles to move: {len(moves)}")
    print(f"collisions (auto-renamed): {len(collisions)}")
    print(f"ambiguous folders (not referenced by manifests): {len(ambiguous)}")
    for m in moves:
        tag = " (collision)" if m.get("collision") else ""
        print(f"  MOVE {m['src']} -> {m['dst']}{tag}")
    for d in ambiguous:
        print(f"  AMBIGUOUS {d}")

    if dry_run:
        return 0
    if not moves:
        return 0
    if not _confirm(yes):
        print("aborted (nothing moved; use --yes to skip the prompt)")
        return 0

    _apply_moves(moves)

    manifest_paths = sorted({pathlib.Path(m["manifest"]) for m in moves})
    manifest_changes: list[dict[str, Any]] = []
    for mpath in manifest_paths:
        data = _load(mpath)
        changes: list[dict[str, Any]] = []
        for entry in data.get("entries") or []:
            old_path = entry.get("path")
            if not old_path:
                continue
            for move in moves:
                if _resolve(root, old_path) == pathlib.Path(move["src"]):
                    changes.append(
                        {"video_id": entry.get("video_id", ""), "old_path": old_path}
                    )
                    entry["path"] = move["dst"]
                    break
        if changes:
            _save(mpath, data)
            manifest_changes.append({"manifest": str(mpath), "changes": changes})

    regenerated = _regenerate_playlists(root, manifest_paths)

    log = _load(log_path)
    runs = log.get("runs") if isinstance(log, dict) else None
    runs = runs if isinstance(runs, list) else []
    runs.append(
        {
            "time": datetime.now(timezone.utc).isoformat(),
            "moves": moves,
            "manifests": manifest_changes,
            "playlists_rewritten": regenerated,
        }
    )
    _save(log_path, {"runs": runs})
    print(f"moved {len(moves)} single files; log: {log_path}")
    return 0


def _undo(root: pathlib.Path, log_path: pathlib.Path) -> int:
    log = _load(log_path)
    runs = log.get("runs") if isinstance(log, dict) else None
    if not runs:
        print("nothing to undo")
        return 1
    run = runs[-1]
    for move in reversed(run.get("moves", [])):
        dst = pathlib.Path(move["dst"])
        src = pathlib.Path(move["src"])
        if dst.exists() and not src.exists():
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dst), str(src))
            _rmdir_if_empty(dst.parent)
    _restore_manifests(run)
    manifest_paths = [pathlib.Path(r["manifest"]) for r in run.get("manifests", [])]
    regenerated = _regenerate_playlists(root, manifest_paths)
    log["runs"] = runs[:-1]
    _save(log_path, log)
    print(f"undid {len(run.get('moves', []))} moves; playlists regenerated: {len(regenerated)}")
    return 0


def _restore_manifests(run: dict[str, Any]) -> None:
    for record in run.get("manifests", []):
        mpath = pathlib.Path(record["manifest"])
        data = _load(mpath)
        if not data.get("entries"):
            continue
        for change in record.get("changes", []):
            for entry in data["entries"]:
                if entry.get("video_id") == change["video_id"]:
                    entry["path"] = change["old_path"]
        _save(mpath, data)
