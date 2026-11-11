"""Pure tests for the TUI color engine (no real curses)."""

from __future__ import annotations

import io

from packrat import tui_theme as tt


def test_parse_hex():
    assert tt.parse_hex("#00bcd4") == (0, 188, 212)
    assert tt.parse_hex("#fff") == (255, 255, 255)


def test_blend_endpoints():
    base, hi = (10, 20, 30), (200, 210, 220)
    assert tt.blend(base, hi, 0.0) == base
    assert tt.blend(base, hi, 1.0) == hi


def test_blend_clamps():
    base, hi = (0, 0, 0), (255, 255, 255)
    assert tt.blend(base, hi, -2.0) == base
    assert tt.blend(base, hi, 2.0) == hi


def test_wave_range():
    for phase in (0.0, 0.13, 0.5, 1.7):
        for x in range(0, 64):
            v = tt.wave_intensity(x, phase)
            assert 0.0 <= v <= 1.0


def test_wave_direction_left_to_right():
    # Peak position advances as phase grows (x = wavelength * (phase + 0.25)).
    def peak(phase: float) -> int:
        xs = range(0, 32)
        return max(xs, key=lambda x: tt.wave_intensity(x, phase))

    assert peak(0.0) < peak(0.4)


def test_wave_wall_clock_phase():
    # Phase is linear in now and speed.
    assert tt.wave_phase(0.0, speed=0.8) == 0.0
    assert tt.wave_phase(10.0, speed=0.8) == tt.wave_phase(20.0, speed=0.8) * 0.5


def test_breathe_range():
    for t in range(100):
        assert 0.0 <= tt.breathe(t / 10) <= 1.0


def test_xterm_mapping_known_values():
    assert tt.nearest_index((0, 0, 0)) == 16
    assert tt.nearest_index((255, 255, 255)) == 231
    # Exact cube colors map to their own index: (95,0,0) -> 16+36*1 = 52.
    assert tt.nearest_index((95, 0, 0)) == 52
    # Grays land in 232..255.
    for tint in (100, 128, 160, 200):
        assert 232 <= tt.nearest_index((tint, tint, tint)) <= 255


def test_gradient_dedupes_and_covers_endpoints():
    idx = tt.gradient_indices((0, 68, 255), (255, 255, 255))
    assert idx[0] == tt.nearest_index((0, 68, 255))
    assert idx[-1] == 231
    assert len(idx) == len(set(idx))


def test_flat_gradient_reduces_to_one_index():
    assert tt.gradient_indices((0, 0, 0), (0, 0, 0)) == [16]


def test_select_tier():
    assert tt.select_tier(True, 256, False) == tt.TIER_FULL
    assert tt.select_tier(True, 512, False) == tt.TIER_FULL
    assert tt.select_tier(True, 16, False) == tt.TIER_BMID
    assert tt.select_tier(True, 8, False) == tt.TIER_BMID
    assert tt.select_tier(False, 256, False) == tt.TIER_NONE
    assert tt.select_tier(True, 256, True) == tt.TIER_NONE


def test_no_color_requested(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert tt.no_color_requested() is False
    monkeypatch.setenv("NO_COLOR", "1")
    assert tt.no_color_requested() is True
    assert tt.no_color_requested(flag=True) is True


def test_themes_parse_and_define_semantics():
    for name, theme in tt.THEMES.items():
        assert theme.name == name
        base = tt.parse_hex(theme.base)
        hi = tt.parse_hex(theme.highlight)
        assert len(base) == 3
        for key in tt.COLOR_KEYS:
            assert tt.parse_hex(getattr(theme, key))
    # Defaults cover the required four.
    assert set(tt.THEMES) == {"ocean", "sunset", "matrix", "mono"}


def test_palette_attr_for_intensity():
    p = tt.Palette(tt.THEMES["ocean"], tt.TIER_FULL)
    idx, _ = p.attr_for_intensity(0.0)
    assert idx == p.gradient[0]
    idx, _ = p.attr_for_intensity(1.0)
    assert idx == 231
    # Monotonic within the gradient.
    vals = [p.attr_for_intensity(i / 99)[0] for i in range(100)]
    order = [p.gradient.index(v) for v in vals]
    assert order == sorted(order)


def test_palette_tiers_none_and_wave3():
    p = tt.Palette(tt.THEMES["ocean"], tt.TIER_NONE)
    assert p.attr_for_intensity(0.5) == (-1, False)
    p3 = tt.Palette(tt.THEMES["ocean"], tt.TIER_BMID)
    assert len(p3.wave3) == 3
    assert p3.wave3[-1] == 231  # bold white


# --- redesign phase 1: tiers, multi-stop gradients, crest, custom install ---

def test_select_tier_matrix():
    A = tt.select_tier
    assert A(True, 256, False) == tt.TIER_FIXED
    assert A(True, 8, False) == tt.TIER_BMID
    assert A(False, 0, False) == tt.TIER_NONE
    assert A(True, 256, True) == tt.TIER_NONE


def test_walk_stops_endpoints_and_midpoints():
    stops = tt.stops_rgb(("#000000", "#808080", "#ffffff"))
    assert tt.walk_stops(stops, 0.0) == (0, 0, 0)
    assert tt.walk_stops(stops, 1.0) == (255, 255, 255)
    assert tt.walk_stops(stops, 0.5) == (128, 128, 128)
    assert tt.walk_stops(stops, 0.25) == (64, 64, 64)


def test_theme_walk_uses_multi_stops():
    theme = tt.THEMES["ocean"]
    assert theme.walk(0.0) == tt.parse_hex(theme.stops[0])
    assert theme.walk(1.0) == tt.parse_hex(theme.stops[-1])
    # the middle of the ocean walk is cyan-ish, not base-vs-white pink
    mid = theme.walk(0.33)
    assert mid[2] > mid[0]  # blue channel dominates near #00b4d8


def test_fixed_gradient_distinct_colors_per_theme():
    """Every theme's FIXED-mode wave (base walk + crest blend) yields
    >= MIN_DISTINCT distinct xterm-256 colors. Reports the counts."""
    counts = {}
    hi = tt.parse_hex("#ffffff")
    W = 100  # capped bar width
    for name, theme in tt.THEMES.items():
        seen = set()
        for k in range(24):  # one crest period
            now = k / 24 / tt.CREST_SPEED
            for x in range(0, W, 2):
                c = tt.crest(x, now, W)
                rgb = theme.walk(x / W)
                if c > 0:
                    rgb = tt.blend(rgb, hi, tt.CREST_BLEND * c)
                seen.add(tt.nearest_index(rgb))
        counts[name] = len(seen)
        assert len(seen) >= tt.MIN_DISTINCT, (name, counts)
    print("distinct fixed-mode colors:", counts)




def test_crest_range_and_travel():
    vals = [tt.crest(x, 0.0, 100) for x in range(0, 100)]
    assert all(0.0 <= v <= 1.0 for v in vals)
    # the crest advances with time: peak moves rightwards
    def peak(now: float) -> int:
        return max(range(0, 120), key=lambda x: tt.crest(x, now, 100))
    assert peak(0.5) > peak(0.0)




def test_palette_none_static_map():
    p = tt.Palette(tt.THEMES["mono"], tt.TIER_NONE)
    assert p.gradient == [] and p.wave3 == []
    assert set(p.static) == set(tt.COLOR_KEYS)
    assert all(v == -1 for v in p.static.values())




def test_gradient_exact_no_dedupe():
    from packrat import tui_theme as tt
    theme = tt.THEMES["ocean"]
    ex = tt.gradient_exact(tt.parse_hex(theme.base), tt.parse_hex(theme.highlight),
                           stops=theme.stops)
    assert len(ex) == tt.GRADIENT_STEPS
    assert ex[0] == tt.nearest_index(tt.parse_hex("#0b3d91"))
    assert ex[-1] == 231  # xterm white


def test_palette_track_idx_and_gradient_exact():
    from packrat import tui_theme as tt
    pf = tt.Palette(tt.THEMES["ocean"], tt.TIER_FIXED)
    expected = tt.nearest_index(tt.blend(tt.parse_hex(tt.THEMES["ocean"].base),
                                         (0, 0, 0), 0.55))
    assert pf.track_idx == expected
    assert len(pf.gradient_exact) == tt.GRADIENT_STEPS
    pn = tt.Palette(tt.THEMES["ocean"], tt.TIER_NONE)
    assert pn.track_idx == -1 and pn.gradient_exact == []


def test_nearest_position_endpoints():
    from packrat import tui_theme as tt
    theme = tt.THEMES["ocean"]
    assert tt.nearest_position((0, 5, 60), theme) == 0
    assert tt.nearest_position((255, 255, 255), theme) == tt.GRADIENT_STEPS - 1
    mid = tt.nearest_position((0, 130, 160), theme)
    assert 0 < mid < tt.GRADIENT_STEPS - 1


def test_select_tier_custom_when_terminal_allows_palette_changes():
    assert tt.select_tier(True, 256, False, "auto", True) == tt.TIER_CUSTOM
    assert tt.select_tier(True, 256, False, "fixed", True) == tt.TIER_FIXED
    assert tt.select_tier(True, 256, False, "auto", False) == tt.TIER_FIXED
    assert tt.select_tier(False, 0, False, "auto", True) == tt.TIER_NONE


def test_custom_palette_has_exact_rgb_steps():
    p = tt.Palette(tt.THEMES["ocean"], tt.TIER_CUSTOM)
    assert len(p.gradient_rgb_list) == tt.CUSTOM_GRADIENT_STEPS
    assert p.gradient == list(range(tt.CUSTOM_GRADIENT_STEPS))
    assert p.gradient_exact == []
    assert p.gradient_rgb_list[0] == tt.parse_hex(tt.THEMES["ocean"].base)
    assert p.gradient_rgb_list[-1] == tt.parse_hex(tt.THEMES["ocean"].highlight)


def test_restore_palette_writes_osc104():
    out = io.StringIO()
    tt.restore_palette(out)
    assert out.getvalue() == tt.RESTORE_SEQUENCE == "\x1b]104\x07"


def test_hsl_and_hsv_roundtrip_primaries():
    for rgb in ((255, 0, 0), (0, 255, 0), (0, 0, 255),
                (128, 128, 128), (255, 255, 255), (0, 0, 0)):
        assert tt.hsl_to_rgb(tt.rgb_to_hsl(rgb)) == rgb
        assert tt.hsv_to_rgb(tt.rgb_to_hsv(rgb)) == rgb


def test_progress_gradient_advances_hue_per_tile():
    hues = [tt.progress_hsl(tt.THEMES["ocean"], i, 8)[0] for i in range(8)]
    diffs = [((b - a) % 360) for a, b in zip(hues, hues[1:])]
    assert all(abs(d - tt.HUE_STEP_PER_TILE) < 1e-6 for d in diffs), diffs
    assert tt.HUE_STEP_PER_TILE == 1.0
