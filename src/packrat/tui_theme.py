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
Hsl = tuple[float, float, float]  # hue 0..360, saturation/lightness 0..1
Hsv = tuple[float, float, float]  # hue 0..360, saturation/value 0..1


def rgb_to_hsl(rgb: Rgb) -> Hsl:
    """sRGB 0..255 -> HSL (hue degrees, saturation/lightness 0..1)."""
    r, g, b = (c / 255.0 for c in rgb)
    high, low = max(r, g, b), min(r, g, b)
    light = (high + low) / 2.0
    if high == low:
        return 0.0, 0.0, light
    delta = high - low
    sat = delta / (2.0 - high - low) if light > 0.5 else delta / (high + low)
    if high == r:
        hue = ((g - b) / delta) % 6.0
    elif high == g:
        hue = (b - r) / delta + 2.0
    else:
        hue = (r - g) / delta + 4.0
    return (hue * 60.0) % 360.0, sat, light


def hsl_to_rgb(hsl: Hsl) -> Rgb:
    """HSL -> sRGB 0..255, rounded."""
    hue, sat, light = hsl
    hue = hue % 360.0
    if sat <= 0:
        value = round(light * 255)
        return (value, value, value)
    q = light * (1 + sat) if light < 0.5 else light + sat - light * sat
    p = 2 * light - q

    def channel(t: float) -> float:
        t %= 1.0
        if t < 1 / 6:
            return p + (q - p) * 6 * t
        if t < 1 / 2:
            return q
        if t < 2 / 3:
            return p + (q - p) * (2 / 3 - t) * 6
        return p

    h = hue / 360.0
    return tuple(round(channel(h + offset) * 255) for offset in (1 / 3, 0, -1 / 3))


def rgb_to_hsv(rgb: Rgb) -> Hsv:
    """sRGB 0..255 -> HSV (hue degrees, saturation/value 0..1)."""
    r, g, b = (c / 255.0 for c in rgb)
    high, low = max(r, g, b), min(r, g, b)
    delta = high - low
    if delta == 0:
        return 0.0, 0.0, high
    sat = delta / high if high else 0.0
    if high == r:
        hue = ((g - b) / delta) % 6.0
    elif high == g:
        hue = (b - r) / delta + 2.0
    else:
        hue = (r - g) / delta + 4.0
    return (hue * 60.0) % 360.0, sat, high


def hsv_to_rgb(hsv: Hsv) -> Rgb:
    """HSV -> sRGB 0..255, rounded."""
    hue, sat, value = hsv
    hue = hue % 360.0
    if sat <= 0:
        v = round(value * 255)
        return (v, v, v)
    sector = hue / 60.0
    i = int(sector) % 6
    f = sector - int(sector)
    p = value * (1 - sat)
    q = value * (1 - sat * f)
    t = value * (1 - sat * (1 - f))
    rgb = [(value, t, p), (q, value, p), (p, value, t),
           (p, q, value), (t, p, value), (value, p, q)][i]
    return tuple(round(c * 255) for c in rgb)


def blend_hsl(base: Hsl, highlight: Hsl, t: float) -> Hsl:
    """Interpolate HSL with the shortest hue path; s/l linearly."""
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    dh = (highlight[0] - base[0] + 540.0) % 360.0 - 180.0
    hue = (base[0] + dh * t) % 360.0
    return (hue, lerp(base[1], highlight[1], t), lerp(base[2], highlight[2], t))


def walk_stops_hsl(stops: tuple[Hsl, ...], t: float) -> Hsl:
    """Piecewise HSL walk with shortest hue path between stops."""
    n = len(stops)
    if n == 1 or t <= 0:
        return stops[0]
    if t >= 1:
        return stops[-1]
    scaled = t * (n - 1)
    i = min(n - 2, int(scaled))
    return blend_hsl(stops[i], stops[i + 1], scaled - i)


def parse_hex_hsl(color: str) -> Hsl:
    return rgb_to_hsl(parse_hex(color))


def as_hsl(color: Rgb | str) -> Hsl:
    """Accept an RGB tuple or a hex string and return HSL."""
    return parse_hex_hsl(color) if isinstance(color, str) else rgb_to_hsl(color)


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


GRADIENT_STEPS = 64
CUSTOM_GRADIENT_STEPS = 100  # exact slots; keep custom pairs <= 256


def gradient_indices(
    base: Rgb, highlight: Rgb, steps: int = GRADIENT_STEPS, *,
    stops: tuple[str, ...] | None = None,
) -> list[int]:
    """Quantized HSL gradient as deduped xterm-256 indices.

    With multi-stop themes pass ``stops`` (hex).  The walk is done in HSL
    with the shortest hue path between stops.
    """
    hsl_stops = tuple(parse_hex_hsl(s) for s in stops) if stops else None
    out: list[int] = []
    for i in range(steps):
        t = i / (steps - 1)
        hsl = (walk_stops_hsl(hsl_stops, t) if hsl_stops
               else blend_hsl(as_hsl(base), as_hsl(highlight), t))
        idx = nearest_index(hsl_to_rgb(hsl))
        if not out or out[-1] != idx:
            out.append(idx)
    return out


def gradient_exact(
    base: Rgb, highlight: Rgb, steps: int = GRADIENT_STEPS, *,
    stops: tuple[str, ...] | None = None, hue_step: float = 0.0,
) -> list[int]:
    """Per-position xterm-256 indices (no dedupe) for space-background bars.

    ``hue_step`` is the per-tile hue advance used by the progress bar.
    """
    hsl_stops = tuple(parse_hex_hsl(s) for s in stops) if stops else None
    base_hsl = hsl_stops[0] if hsl_stops else as_hsl(base)
    hi_hsl = hsl_stops[-1] if hsl_stops else as_hsl(highlight)
    out: list[int] = []
    for i in range(steps):
        t = i / (steps - 1)
        if hue_step:
            hue = (base_hsl[0] + i * hue_step) % 360.0
            sat = lerp(base_hsl[1], hi_hsl[1], t)
            light = base_hsl[2] + (1.0 - base_hsl[2]) * (t ** 0.5)
        else:
            hsl = (walk_stops_hsl(hsl_stops, t) if hsl_stops
                   else blend_hsl(base_hsl, hi_hsl, t))
            hue, sat, light = hsl
        out.append(nearest_index(hsl_to_rgb((hue, sat, light))))
    return out


def nearest_position(
    rgb: Rgb, theme: Theme, steps: int = GRADIENT_STEPS,
) -> int:
    """Nearest gradient POSITION (0..steps-1) to an arbitrary wave RGB."""
    r = theme.walk  # multi-stop walk
    best, best_d = 0, None
    for i in range(steps):
        d = _redmean(rgb, r(i / (steps - 1)))
        if best_d is None or d < best_d:
            best, best_d = i, d
    return best


# --- tiers ------------------------------------------------------------------

TIER_CUSTOM = "custom"  # exact RGB via init_color (smooth 256-color bar)
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
    """Pick the best color tier the terminal supports.

    ``custom`` redefines palette slots with exact RGB via init_color when the
    terminal allows it; that is what makes the progress gradient smooth.
    """
    if no_color or not has_colors:
        return TIER_NONE
    if colors < 256 or palette_mode == "basic":
        return TIER_BMID
    if palette_mode == "auto" and can_change_color:
        return TIER_CUSTOM
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

    def walk_hsl(self, t: float) -> Hsl:
        """Multi-stop HSL gradient walk at t in 0..1 (base..highlight)."""
        if self.stops:
            return walk_stops_hsl(tuple(parse_hex_hsl(s) for s in self.stops), t)
        return blend_hsl(parse_hex_hsl(self.base), parse_hex_hsl(self.highlight), t)

    def walk(self, t: float) -> Rgb:
        """RGB view of the HSL gradient walk (back-compat)."""
        return hsl_to_rgb(self.walk_hsl(t))



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


HUE_STEP_PER_TILE = 1.0  # degrees added for each progress-bar tile


def progress_hsl(theme: Theme, i: int, steps: int,
                 hue_step: float = HUE_STEP_PER_TILE) -> Hsl:
    """HSL color for progress tile i.

    Hue advances by exactly ``hue_step`` degrees per tile; saturation fades
    toward the theme highlight and lightness rises on a square-root curve so
    the leading edge is visible even at a low percentage.
    """
    t = 0.0 if steps <= 1 else i / (steps - 1)
    base_h, base_s, base_l = theme.walk_hsl(0.0)
    _hi_h, hi_s, _hi_l = theme.walk_hsl(1.0)
    hue = (base_h + i * hue_step) % 360.0
    sat = lerp(base_s, hi_s, t)
    light = base_l + (1.0 - base_l) * (t ** 0.5)
    return (hue, sat, light)


def theme_gradient_rgb(theme: Theme, steps: int = CUSTOM_GRADIENT_STEPS) -> list[Rgb]:
    """Exact RGB walk used by the custom (init_color) palette tier."""
    return [hsl_to_rgb(progress_hsl(theme, i, steps)) for i in range(steps)]


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
            self.gradient_exact: list[int] = []
            self.track_idx = -1
            self.wave3: list[int] = []
            self.static: dict[str, int] = {k: -1 for k in COLOR_KEYS}
            return
        base = parse_hex(theme.base)
        highlight = parse_hex(theme.highlight)
        if tier == TIER_CUSTOM:
            self.gradient_rgb_list = theme_gradient_rgb(theme)
            self.gradient = list(range(len(self.gradient_rgb_list)))
            self.gradient_exact: list[int] = []
        else:
            self.gradient_rgb_list = []
            self.gradient = gradient_indices(base, highlight, stops=self.stops)
            # per-position indices, no consecutive dedupe (space-bg bars)
            self.gradient_exact = gradient_exact(
                base, highlight, stops=self.stops,
            )
        # A dark track keeps the unfilled part quiet; the text dim color is
        # intentionally separate so labels stay readable.
        self.track_idx = nearest_index(blend(parse_hex(theme.base), (0, 0, 0), 0.55))
        self.static: dict[str, int] = {
            k: nearest_index(parse_hex(getattr(theme, k))) for k in COLOR_KEYS
        }
        # wave3 ladder: base → bold base → bold white
        if tier == TIER_BMID:
            self.wave3 = [
                self.static_nearest(base),
                self.static_nearest(blend(base, highlight, 0.5)),
                231,
            ]
        else:
            self.wave3 = []

    @staticmethod
    def static_nearest(rgb: Rgb) -> int:
        return nearest_index(rgb)

    def attr_for_intensity(self, intensity: float) -> tuple[int, bool]:
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


RESTORE_SEQUENCE = "\x1b]104\x07"


def restore_palette(stream=None) -> None:
    """Reset terminal palette entries after the TUI exits (OSC 104)."""
    import sys

    stream = sys.stdout if stream is None else stream
    if stream is None:
        return
    try:
        stream.write(RESTORE_SEQUENCE)
        stream.flush()
    except (OSError, ValueError):
        pass
