"""Audio-file integrity checks used before skipping or after downloading."""

from __future__ import annotations

import dataclasses
import pathlib


@dataclasses.dataclass(frozen=True)
class AudioCheck:
    ok: bool
    reason: str = ""
    duration: float | None = None
    size: int = 0


def verify_audio(path: pathlib.Path, expected_size: int | None = None) -> AudioCheck:
    """Validate that *path* is a non-empty, parseable audio file.

    Mutagen is used as the lightweight structural check (it parses the
    container and reports duration).  ``expected_size`` is only a sanity
    bound; codec/bitrate variation means it must not be treated as exact.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return AudioCheck(False, "file is missing", size=0)
    if size <= 0:
        return AudioCheck(False, "file is empty", size=size)
    if expected_size and size < int(expected_size * 0.5):
        return AudioCheck(False, "file is much smaller than expected", size=size)
    try:
        import mutagen
        audio = mutagen.File(str(path))
    except Exception as exc:
        return AudioCheck(False, f"cannot parse audio: {exc}", size=size)
    if audio is None:
        return AudioCheck(False, "not a recognised audio container", size=size)
    duration = getattr(getattr(audio, "info", None), "length", None)
    if duration is not None and duration <= 0:
        return AudioCheck(False, "zero-length audio stream", duration=duration, size=size)
    return AudioCheck(True, duration=duration, size=size)
