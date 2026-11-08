"""Color engine for the curses TUI: gradients, waves, xterm-256 mapping.

Pure functions only; the one curses-touching helper (setup_colors) is kept
separate so tests never need a real curses screen.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass

# --- RGB helpers -----------------------------------------------------------

Rgb = tuple[float, float, float]


def parse_hex(color: str) -> Rgb:
    s = color.lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    return tuple(int(s[i : i + 2], 16) for i in (0, 2, 4))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def blend(base: Rgb, highlight: Rgb, t: float) -> Rgb:
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return tuple(round(lerp(c, d, t)) for c, d in zip(base, highlight))


# --- wave ------------------------------------------------------------------

WAVELENGTH_DEFAULT = 16  # cells
SPEED_DEFAULT = 0.8  # cycles/s


def wave_intensity(x: float, phase: float, wavelength: float = WAVELENGTH_DEFAULT) -> float:
    """Brightness 0..1 for bar cell x at wall-clock phase."""
    return 0.5 + 0.5 * math.sin(2 * math.pi * (x / wavelength - phase))


def wave_phase(now: float | None = None, speed: float = SPEED_DEFAULT) -> float:
    """Wall-clock phase; argument injected only for tests."""
    return (time.monotonic() if now is None else now) * speed


def breathe(now: float, hz: float = 0.4) -> float:
    """Global brightness oscillation 0..1 (WAITING state)."""
    return 0.5 + 0.5 * math.sin(2 * math.pi * now * hz)


# --- xterm-256 palette ------------------------------------------------------

_CUBE = (0, 95, 135, 175, 215, 255)


def xterm_palette() -> list[Rgb]:
    """Colors for indices 16..255 (cube first, then the 24 grays)."""
    palette = [(r, g, b) for r in _CUBE for g in _CUBE for b in _CUBE]
    palette.extend((8 + 10 * i, 8 + 10 * i, 8 + 10 * i) for i in range(24))
    return palette


_PALETTE = xterm_palette()
_PALETTE_START = 16


def nearest_index(rgb: Rgb) -> int:
    """Nearest xterm-256 index (16..255) by Euclidean RGB distance."""
    pr, pg, pb = rgb
    best, best_d = 16, 10**9
    for i, (r, g, b) in enumerate(_PALETTE):
        d = (pr - r) ** 2 + (pg - g) ** 2 + (pb - b) ** 2
        if d < best_d:
            best, best_d = _PALETTE_START + i, d
    return best


GRADIENT_STEPS = 24


def gradient_indices(base: Rgb, highlight: Rgb, steps: int = GRADIENT_STEPS) -> list[int]:
    """Quantized base→highlight gradient as deduped xterm-256 indices."""
    out: list[int] = []
    for i in range(steps):
        idx = nearest_index(blend(base, highlight, i / (steps - 1)))
        if not out or out[-1] != idx:
            out.append(idx)
    return out


# --- tiers ------------------------------------------------------------------

TIER_FULL = "full"  # >=256 colors: quantized wave
TIER_BMID = "wave3"  # 8/16 colors: base / bold base / bold white
TIER_NONE = "none"  # no color at all


def select_tier(has_colors: bool, colors: int, no_color: bool) -> str:
    if no_color or not has_colors:
        return TIER_NONE
    if colors >= 256:
        return TIER_FULL
    return TIER_BMID


def no_color_requested(flag: bool = False) -> bool:
    return bool(flag or os.environ.get("NO_COLOR"))


# --- themes -----------------------------------------------------------------


@dataclass(frozen=True)
class Theme:
    name: str
    base: str  # gradient base color, hex
    highlight: str = "#ffffff"
    dim: str = "#666666"  # track (empty part) cells
    success: str = "#4caf50"
    warning: str = "#ffb300"
    error: str = "#e53935"
    info: str = "#2196f3"
    stopping: str = "#ffb300"


THEMES: dict[str, Theme] = {
    "ocean": Theme("ocean", "#00bcd4"),
    "sunset": Theme("sunset", "#ff9800"),
    "matrix": Theme("matrix", "#4caf50"),
    "mono": Theme("mono", "#9e9e9e"),
}

COLOR_KEYS = ("dim", "success", "warning", "error", "info", "stopping")


class Palette:
    """Pre-computed index/attr tables for one theme at one tier.

    Built once at startup; the renderer only reads attrs, never init_pair.
    """

    def __init__(self, theme: Theme, tier: str) -> None:
        self.theme = theme
        self.tier = tier
        base = parse_hex(theme.base)
        highlight = parse_hex(theme.highlight)
        self.gradient = gradient_indices(base, highlight) if tier != TIER_NONE else []
        self.static: dict[str, int] = {
            k: (nearest_index(parse_hex(getattr(theme, k))) if tier != TIER_NONE else -1)
            for k in COLOR_KEYS
        }
        # wave3 ladder: base → bold base → bold white
        if tier == TIER_BMID:
            self.wave3 = [self.static_nearest(base), self.static_nearest(blend(base, highlight, 0.5)), 231]
        else:
            self.wave3 = []

    @staticmethod
    def static_nearest(rgb: Rgb) -> int:
        return nearest_index(rgb)

    def attr_for_intensity(self, intensity: float) -> tuple[int, int]:
        """(index, bold?) for a wave intensity; tier-aware."""
        if self.tier == TIER_NONE:
            return -1, False
        if self.tier == TIER_BMID:
            n = len(self.wave3)
            idx = min(n - 1, int(round(intensity * (n - 1))))
            return self.wave3[idx], idx >= 1
        n = len(self.gradient)
        idx = min(n - 1, int(round(intensity * (n - 1))))
        return self.gradient[idx], False


def init_pairs(indices: list[int]) -> dict[int, int]:
    """Allocate color pairs ONCE; caller must have called start_color().

    Returns {index: pair_number} without touching curses per frame.
    """
    import curses

    curses.use_default_colors()
    pairs: dict[int, int] = {}
    for number, idx in enumerate(indices, start=1):
        curses.init_pair(number, idx, -1)
        pairs[idx] = number
    return pairs


def activate(palette: Palette) -> dict[int, int]:
    """Allocate pairs for all indices the renderer will use."""
    indices = sorted(set(palette.gradient) | {i for i in palette.static.values() if i >= 0} | set(palette.wave3))
    return init_pairs(indices)
