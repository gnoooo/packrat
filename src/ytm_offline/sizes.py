"""Human-readable size parsing and formatting."""

from __future__ import annotations

import re


_UNITS = {
    "B": 1,
    "KB": 1_000,
    "MB": 1_000_000,
    "GB": 1_000_000_000,
    "TB": 1_000_000_000_000,
    "K": 1_000,
    "M": 1_000_000,
    "G": 1_000_000_000,
    "T": 1_000_000_000_000,
}

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMGT]B?|[B])?\s*$", re.IGNORECASE)


def parse_size(value: str) -> int:
    """Parse '3GB', '250M', '10KB' into bytes."""
    match = _SIZE_RE.match(str(value))
    if not match:
        raise ValueError(f"invalid size: {value!r}")
    number = float(match.group(1))
    unit = (match.group(2) or "B").upper()
    return int(number * _UNITS[unit])


def format_size(bytes_count: int) -> str:
    """Format bytes as human readable (base-1024)."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(bytes_count) < 1024 or unit == "TB":
            return f"{bytes_count:.1f} {unit}"
        bytes_count /= 1024.0
    return f"{bytes_count:.1f} TB"


def heuristic_size(duration: int, bytes_per_second: int = 16000) -> int:
    """Rough file-size estimate from duration in seconds."""
    return int(duration * bytes_per_second)
