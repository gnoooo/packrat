"""Pure tests for the TUI color engine (no real curses)."""

from __future__ import annotations

import io

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


# --- redesign phase 1: tiers, multi-stop gradients, crest, custom install ---

def test_select_tier_palette_mode_matrix():
    A = tt.select_tier
    # >= 256 colors
    assert A(True, 256, False, "custom", True) == tt.TIER_CUSTOM
    assert A(True, 256, False, "custom", False) == tt.TIER_FIXED
    assert A(True, 256, False, "auto", True) == tt.TIER_CUSTOM
    assert A(True, 256, False, "auto", False) == tt.TIER_FIXED
    assert A(True, 256, False, "fixed", True) == tt.TIER_FIXED
    assert A(True, 256, False, "basic", True) == tt.TIER_FIXED
    # forced basic with no colors support
    assert A(True, 8, False, "basic") == tt.TIER_BMID
    assert A(True, 8, False, "auto") == tt.TIER_BMID


def test_walk_stops_endpoints_and_midpoints():
    stops = tt.parse_hex_tuple(("#000000", "#808080", "#ffffff"))
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


def test_crest_displacement_per_frame():
    for bar_width in (10, 24, 48, 64, 80, 100):
        moved = tt.crest_offset(0.0, 0.05, bar_width)  # one 50 ms tick
        assert 0 <= moved <= 1, bar_width


def test_crest_range_and_travel():
    vals = [tt.crest(x, 0.0, 100) for x in range(0, 100)]
    assert all(0.0 <= v <= 1.0 for v in vals)
    # the crest advances with time: peak moves rightwards
    def peak(now: float) -> int:
        return max(range(0, 120), key=lambda x: tt.crest(x, now, 100))
    assert peak(0.5) > peak(0.0)


class _FakeCurses:
    def __init__(self) -> None:
        self.colors: list[tuple] = []
        self.pairs: list[tuple] = []

    def init_color(self, slot, r, g, b):
        self.colors.append((slot, r, g, b))

    def init_pair(self, number, slot, bg):
        self.pairs.append((number, slot, bg))


def test_install_custom_rgb_exact_values_and_slots():
    fake = _FakeCurses()
    rgbs = tt.theme_gradient_rgb(tt.THEMES["ocean"], steps=8)
    static = {k: (0, 0, 0) for k in tt.COLOR_KEYS}
    grad_pairs, static_pairs = tt.install_custom_rgb(
        rgbs, static, curses_mod=fake,
    )
    # 8 gradient + 6 static colors = 14 slots, starting at 32
    assert len(fake.colors) == 14
    assert [c[0] for c in fake.colors] == list(range(32, 32 + 14))
    for _, r, g, b in fake.colors:
        assert 0 <= r <= 1000 and 0 <= g <= 1000 and 0 <= b <= 1000
    # black maps to (0,0,0) on the 0..1000 scale
    assert fake.colors[8][1:] == (0, 0, 0)
    # pairs numbered from 1; gradient positions map in order
    assert [p[0] for p in fake.pairs] == list(range(1, 15))
    assert grad_pairs == {i: i + 1 for i in range(8)}
    assert static_pairs == {k: 9 + i for i, k in enumerate(tt.COLOR_KEYS)}


def test_install_custom_rgb_out_of_slots():
    fake = _FakeCurses()
    # 24 gradient steps + 6 UI colors fit; force the overflow with more.
    rgbs = [(i, i, i) for i in range(219)]
    static = {k: (0, 0, 0) for k in tt.COLOR_KEYS}
    try:
        tt.install_custom_rgb(rgbs, static, curses_mod=fake)
    except ValueError as exc:
        assert "32..255" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_restore_palette_writes_sequence():
    out = io.StringIO()
    tt.restore_palette(out)
    assert out.getvalue() == tt.RESTORE_SEQUENCE == "\x1b]104\x07"


def test_palette_tiers_fixed_and_custom():
    pf = tt.Palette(tt.THEMES["sunset"], tt.TIER_FIXED)
    assert pf.gradient and pf.gradient_rgb_list == []
    pc = tt.Palette(tt.THEMES["sunset"], tt.TIER_CUSTOM)
    assert pc.gradient == list(range(len(pc.gradient_rgb_list)))
    assert len(pc.gradient_rgb_list) == tt.GRADIENT_STEPS
    # custom tier keeps rgb list for install_custom_rgb
    assert pc.gradient_rgb_list[0] == tt.parse_hex(pf.stops[0])


def test_palette_none_static_map():
    p = tt.Palette(tt.THEMES["mono"], tt.TIER_NONE)
    assert p.gradient == [] and p.wave3 == []
    assert set(p.static) == set(tt.COLOR_KEYS)
    assert all(v == -1 for v in p.static.values())


def _fake_curses_record():
    class Fake:
        def __init__(self):
            self.calls = []
        def init_color(self, *a):
            self.calls.append(("color",) + a)
        def init_pair(self, *a):
            self.calls.append(("pair",) + a)
    return Fake()


def test_gradient_exact_no_dedupe():
    from ytm_offline import tui_theme as tt
    theme = tt.THEMES["ocean"]
    ex = tt.gradient_exact(tt.parse_hex(theme.base), tt.parse_hex(theme.highlight),
                           stops=theme.stops)
    assert len(ex) == tt.GRADIENT_STEPS
    assert ex[0] == tt.nearest_index(tt.parse_hex("#0b3d91"))
    assert ex[-1] == 231  # xterm white


def test_palette_track_idx_and_gradient_exact():
    from ytm_offline import tui_theme as tt
    pf = tt.Palette(tt.THEMES["ocean"], tt.TIER_FIXED)
    expected = tt.nearest_index(tt.parse_hex("#666666"))
    assert pf.track_idx == expected
    assert len(pf.gradient_exact) == tt.GRADIENT_STEPS
    pn = tt.Palette(tt.THEMES["ocean"], tt.TIER_NONE)
    assert pn.track_idx == -1 and pn.gradient_exact == []


def test_nearest_position_endpoints():
    from ytm_offline import tui_theme as tt
    theme = tt.THEMES["ocean"]
    assert tt.nearest_position((0, 5, 60), theme) == 0
    assert tt.nearest_position((255, 255, 255), theme) == tt.GRADIENT_STEPS - 1
    mid = tt.nearest_position((0, 130, 160), theme)
    assert 0 < mid < tt.GRADIENT_STEPS - 1


def test_install_custom_rgb_track_slot():
    from ytm_offline import tui_theme as tt
    fake = _fake_curses_record()
    theme = tt.THEMES["ocean"]
    rgbs = tt.theme_gradient_rgb(theme)
    static = {k: tt.parse_hex(getattr(theme, k)) for k in tt.COLOR_KEYS}
    grad, stat = tt.install_custom_rgb(
        rgbs, static, track_rgb=tt.parse_hex("#666666"), curses_mod=fake)
    assert tt.TRACK_KEY in stat
    slot_count = len(rgbs) + len(static) + 1
    assert len(fake.calls) == slot_count * 2  # one color + one pair each
    track_slot = stat[tt.TRACK_KEY]
    assert track_slot == 32 + len(rgbs) + len(static)
    # track slot init_color called with the gray on the 0-1000 scale
    color_calls = [c for c in fake.calls if c[0] == "color"]
    assert color_calls[-1] == ("color", track_slot, 400, 400, 400)
