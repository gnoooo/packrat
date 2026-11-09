"""Color engine for the curses TUI: multi-stop gradients, waves, tiers.

Pure functions only; the curses-touching helpers (init_pairs,
install_custom_rgb, restore_palette) are kept separate so tests never
need a real curses screen.
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


def stops_rgb(stops: tuple[str, ...]) -> tuple[Rgb, ...]:
    return tuple(parse_hex(s) for s in stops)


def walk_stops(stops: tuple[Rgb, ...], t: float) -> Rgb:
    """Interpolate a multi-stop gradient at t in 0..1 (piecewise linear)."""
    n = len(stops)
    if n == 1 or t <= 0:
        return stops[0]
    if t >= 1:
        return stops[-1]
    scaled = t * (n - 1)
    i = min(n - 2, int(scaled))
    return blend(stops[i], stops[i + 1], scaled - i)


# --- wave ------------------------------------------------------------------

WAVELENGTH_DEFAULT = 16  # cells
SPEED_DEFAULT = 0.8  # cycles/s
CREST_SPEED = 0.30  # cycles/s (bar highlight crest)
CREST_BLEND = 0.55


def wave_intensity(x: float, phase: float, wavelength: float = WAVELENGTH_DEFAULT) -> float:
    """Brightness 0..1 for bar cell x at wall-clock phase."""
    return 0.5 + 0.5 * math.sin(2 * math.pi * (x / wavelength - phase))


def wave_phase(now: float | None = None, speed: float = SPEED_DEFAULT) -> float:
    """Wall-clock phase; argument injected only for tests."""
    return (time.monotonic() if now is None else now) * speed


def breathe(now: float, hz: float = 0.4) -> float:
    """Global brightness oscillation 0..1 (WAITING state)."""
    return 0.5 + 0.5 * math.sin(2 * math.pi * now * hz)


def smoothstep(t: float) -> float:
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return t * t * (3 - 2 * t)


def crest(x: float, now: float, length: float) -> float:
    """Highlight crest on the bar: smoothstep² of a traveling sine.

    ``length`` is the (full) bar width; L = max(40, length/2), speed
    0.30 cycles/s — the crest advances at most one cell per 50 ms frame.
    """
    L = max(40.0, length / 2)
    raw = 0.5 + 0.5 * math.sin(2 * math.pi * (x / L - now * CREST_SPEED))
    s = smoothstep(raw)
    return s * s


def crest_offset(t0: float, t1: float, length: float) -> int:
    """Cells the crest peak moved between two frames (crest test helper)."""
    return round((t1 - t0) * CREST_SPEED * max(40.0, length / 2))


# --- xterm-256 palette ------------------------------------------------------

_CUBE = (0, 95, 135, 175, 215, 255)


def xterm_palette() -> list[Rgb]:
    """Colors for indices 16..255 (cube first, then the 24 grays)."""
    palette = [(r, g, b) for r in _CUBE for g in _CUBE for b in _CUBE]
    palette.extend((8 + 10 * i, 8 + 10 * i, 8 + 10 * i) for i in range(24))
    return palette


_PALETTE = xterm_palette()
_PALETTE_START = 16


def _redmean(a: Rgb, b: Rgb) -> float:
    """Perceptual RGB distance (redmean approximation)."""
    rmean = (a[0] + b[0]) / 2
    dr, dg, db = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    return math.sqrt(
        (2 + rmean / 256) * dr * dr + 4 * dg * dg + (2 + (255 - rmean) / 256) * db * db
    )


def nearest_index(rgb: Rgb) -> int:
    """Nearest xterm-256 index (16..255) by perceptual distance (redmean)."""
    best, best_d = 16, 10**9
    for i, cand in enumerate(_PALETTE):
        d = _redmean(rgb, cand)
        if d < best_d:
            best, best_d = _PALETTE_START + i, d
    return best


GRADIENT_STEPS = 24


def gradient_indices(
    base: Rgb, highlight: Rgb, steps: int = GRADIENT_STEPS, *, stops: tuple[str, ...] | None = None
) -> list[int]:
    """Quantized gradient as deduped xterm-256 indices.

    With multi-stop themes pass ``stops`` (hex) — the walk passes through
    cube AND gray ramp colors, giving >= 16 distinct indices.
    """
    if stops:
        rgbs = stops_rgb(stops)
        seq = (walk_stops(rgbs, i / (steps - 1)) for i in range(steps))
    else:
        seq = (blend(base, highlight, i / (steps - 1)) for i in range(steps))
    out: list[int] = []
    for rgb in seq:
        idx = nearest_index(rgb)
        if not out or out[-1] != idx:
            out.append(idx)
    return out


# --- tiers ------------------------------------------------------------------

TIER_CUSTOM = "custom"  # curses init_color with exact RGB
TIER_FIXED = "fixed"  # >=256 colors: perceptual nearest xterm-256
TIER_BMID = "wave3"  # 8/16 colors: base / bold base / bold white
TIER_NONE = "none"  # no color at all
TIER_FULL = TIER_FIXED  # legacy alias


def select_tier(
    has_colors: bool,
    colors: int,
    no_color: bool,
    palette_mode: str = "auto",
    can_change_color: bool = False,
) -> str:
    """custom needs init_color + >=256; fixed is palette matching on 256. """
    if no_color or not has_colors:
        return TIER_NONE
    if colors < 256:
        return TIER_BMID
    if palette_mode == "custom" or (palette_mode == "auto" and can_change_color):
        return TIER_CUSTOM if can_change_color else TIER_FIXED
    return TIER_FIXED


def no_color_requested(flag: bool = False) -> bool:
    return bool(flag or os.environ.get("NO_COLOR"))


# --- themes -----------------------------------------------------------------


@dataclass(frozen=True)
class Theme:
    name: str
    base: str  # kept for back-compat = stops[0]
    highlight: str = "#ffffff"  # kept for back-compat = stops[-1]
    dim: str = "#666666"  # track (empty part) cells
    success: str = "#4caf50"
    warning: str = "#ffb300"
    error: str = "#e53935"
    info: str = "#2196f3"
    stopping: str = "#ffb300"
    stops: tuple[str, ...] = ()

    def walk(self, t: float) -> Rgb:
        """Multi-stop gradient walk at t in 0..1 (base..highlight)."""
        if self.stops:
            return walk_stops(parse_hex_tuple(self.stops), t)
        return blend(parse_hex(self.base), parse_hex(self.highlight), t)


def parse_hex_tuple(stops: tuple[str, ...]) -> tuple[Rgb, ...]:
    return stops_rgb(stops)


THEMES: dict[str, Theme] = {
    "ocean": Theme(
        "ocean", "#0b3d91",
        stops=("#0b3d91", "#00b4d8", "#90e0ef", "#ffffff"),
    ),
    "sunset": Theme(
        "sunset", "#7a1f5c",
        stops=("#7a1f5c", "#f25f5c", "#ffd166", "#fff3d6"),
    ),
    "matrix": Theme(
        "matrix", "#003b1f",
        stops=("#003b1f", "#00c853", "#b9f6ca", "#ffffff"),
    ),
    "mono": Theme(
        "mono", "#303030",
        stops=("#303030", "#9e9e9e", "#ffffff"),
    ),
}

COLOR_KEYS = ("dim", "success", "warning", "error", "info", "stopping")


def theme_gradient_rgb(theme: Theme, steps: int = GRADIENT_STEPS) -> list[Rgb]:
    """Raw RGB walk of the theme gradient (custom tier uses these exactly)."""
    return [theme.walk(i / (steps - 1)) for i in range(steps)]


MIN_DISTINCT = 16  # fixed-tier wave must yield at least this many colors


class Palette:
    """Pre-computed index/attr tables for one theme at one tier.

    Built once at startup; the renderer only reads attrs, never init_pair.
    """

    def __init__(self, theme: Theme, tier: str) -> None:
        self.theme = theme
        self.tier = tier
        self.stops = theme.stops or (theme.base, theme.highlight)
        if tier == TIER_NONE:
            self.gradient: list[int] = []
            self.gradient_rgb_list: list[Rgb] = []
            self.wave3: list[int] = []
            self.static: dict[str, int] = {k: -1 for k in COLOR_KEYS}
            return
        base = parse_hex(theme.base)
        highlight = parse_hex(theme.highlight)
        if tier == TIER_CUSTOM:
            self.gradient_rgb_list = theme_gradient_rgb(theme)
            # positions 0..steps-1; install_custom_rgb maps position→pair
            self.gradient = list(range(len(self.gradient_rgb_list)))
        else:
            self.gradient_rgb_list = []
            self.gradient = gradient_indices(
                base, highlight, stops=self.stops,
            )
        self.static: dict[str, int] = {
            k: nearest_index(parse_hex(getattr(theme, k))) for k in COLOR_KEYS
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


def install_custom_rgb(
    rgbs: list[Rgb],
    static: dict[str, Rgb],
    *,
    first_slot: int = 32,
    curses_mod=None,
) -> tuple[dict[int, int], dict[str, int]]:
    """init_color EXACT RGB into reserved color slots 32..255; one pair per
    color, allocated ONCE at startup.

    Returns ({gradient_position: pair}, {static_key: pair}).
    """
    cur = curses_mod
    if cur is None:
        import curses as cur
    slots: list[int] = []
    for rgb in [*rgbs, *static.values()]:
        if len(slots) + first_slot >= 256:
            raise ValueError("custom palette out of reserved slots 32..255")
        r, g, b = (max(0, min(255, c)) for c in rgb)
        s = first_slot + len(slots)
        cur.init_color(s, r * 1000 // 255, g * 1000 // 255, b * 1000 // 255)
        slots.append(s)
    grad_pairs: dict[int, int] = {}
    static_pairs: dict[str, int] = {}
    for number, slot in enumerate(slots, start=1):
        cur.init_pair(number, slot, -1)
        if number <= len(rgbs):
            grad_pairs[number - 1] = number
        else:
            static_pairs[list(static)[number - 1 - len(rgbs)]] = number
    return grad_pairs, static_pairs


RESTORE_SEQUENCE = "\x1b]104\x07"


def restore_palette(stream=None) -> None:
    """Reset the terminal palette after the TUI ends (write AFTER endwin)."""
    import sys

    stream = sys.stdout if stream is None else stream
    if stream is None:
        return
    try:
        stream.write(RESTORE_SEQUENCE)
        stream.flush()
    except (OSError, ValueError):
        pass


def activate(palette: Palette) -> dict[int, int]:
    """Allocate pairs for all indices the renderer will use."""
    if palette.tier == TIER_CUSTOM:
        # Custom tier is installed via install_custom_rgb, not index pairs.
        return {}
    indices = sorted(set(palette.gradient) | {i for i in palette.static.values() if i >= 0} | set(palette.wave3))
    return init_pairs(indices)
