"""Pure tests for the TUI color engine (no real curses)."""

from __future__ import annotations

from ytm_offline import tui_theme as tt


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
