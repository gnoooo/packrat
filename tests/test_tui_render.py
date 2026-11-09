"""Renderer tests: state→animation mapping and a FakeScreen smoke test."""

from __future__ import annotations

import curses

import pytest

from ytm_offline import tui_glyphs as tg
from ytm_offline import tui_theme as tt
import pathlib
import tempfile
from unittest import mock as _mock

from ytm_offline import events as ev
from ytm_offline.events import Event, State
from ytm_offline import tui as tui_mod


def test_anim_mode_mapping():
    assert tui_mod.anim_mode("RUNNING") == "running"
    assert tui_mod.anim_mode("WAITING 3.2s") == "waiting"
    assert tui_mod.anim_mode("PAUSED") == "paused"
    assert tui_mod.anim_mode("STOPPING") == "stopping"
    assert tui_mod.anim_mode("DONE") == "done"
    assert tui_mod.anim_mode("STOPPED") == "aborted"
    assert tui_mod.anim_mode("ABORTED") == "aborted"
    assert tui_mod.anim_mode("whatever") == "running"


def test_no_color_env(monkeypatch):
    monkeypatch.delenv("YTM_TUI_ANIM", raising=False)
    import os

    # no_anim env handled in _ui; check set_visual_options validates
    tui_mod.set_visual_options("matrix")
    assert tui_mod._visual_state["theme"] == "matrix"
    tui_mod.set_visual_options("not-a-theme")
    assert tui_mod._visual_state["theme"] == "matrix"
    tui_mod.set_visual_options("ocean")


class FakeScreen:
    def __init__(self, h: int, w: int) -> None:
        self._h, self._w = h, w
        self.addstr_calls: list[tuple[int, int, str]] = []
        self.attr_calls: list[tuple[int, int, str, int]] = []
        self.children: list[FakeScreen] = []

    def getmaxyx(self):
        return self._h, self._w

    def erase(self):
        """Stub-erase: drop everything drawn so a resize leaves no stale cells."""
        self.addstr_calls.clear()
        self.attr_calls.clear()
        self.children.clear()

    def noutrefresh(self):
        pass

    def derwin(self, nh, nw, y, x):
        child = FakeScreen(nh, nw)
        child._pos = (y, x)
        self.children.append(child)
        return child

    def addstr(self, y, x, text, attr=0):
        self.addstr_calls.append((y, x, str(text)))
        self.attr_calls.append((y, x, str(text), int(attr)))


def _visual_no_color() -> tui_mod.Visual:
    pal = tt.Palette(tt.THEMES["ocean"], tt.TIER_NONE)
    colors = {k: 0 for k in tt.COLOR_KEYS}
    return tui_mod.Visual(tg.GLYPHS["fancy"], pal, colors,
                          lambda idx, bold=False: 0, False, tt.TIER_NONE)


def _fake_state() -> State:
    state = State(mode="test")
    state.apply(type(state).__mro__ and __import__("ytm_offline.events", fromlist=["Event"]).Event(
        "start", {"total": 3, "total_est_bytes": 30000, "max_size_bytes": 12000}))
    ev = __import__("ytm_offline.events", fromlist=["Event"]).Event
    state.apply(ev("track_start", {"video_id": "v1", "title": "Song One",
                                   "artist": "Artist", "album": "Album",
                                   "source": "artist:Artist", "stage": "downloading"}))
    state.apply(ev("track_progress", {"stage": "downloading", "percent": 40.0,
                                      "downloaded_bytes": 4000, "total_bytes": 10000,
                                      "speed": "8.0 MiB/s", "eta_seconds": 12}))
    state.apply(ev("track_done", {"status": "failed", "video_id": "v2",
                                 "title": "Song Two", "error": "demo failure",
                                 "retryable": True, "bytes": 0}))
    state.apply(ev("track_done", {"status": "failed", "video_id": "v3",
                                 "title": "Song Three", "error": "demo failure 2",
                                 "retryable": True, "bytes": 0}))
    state.apply(ev("log", {"message": "hello from the fake log"}))
    state.apply(ev("track_start", {"video_id": "v1", "title": "Song One",
                                   "artist": "Artist", "album": "Album",
                                   "source": "artist:Artist", "stage": "downloading"}))
    return state


def _all_calls(screen: FakeScreen) -> list:
    calls = list(screen.addstr_calls)
    for child in screen.children:
        calls.extend(_all_calls(child))
    return calls


def _desc(screen: FakeScreen) -> list:
    out = []
    for c in screen.children:
        out.append(c)
        out.extend(_desc(c))
    return out


def test_render_smoke_no_exceptions(monkeypatch):
    monkeypatch.setattr(curses, "doupdate", lambda: None)
    frame = {"mode": "running", "phase": 0.13, "thin_phase": 0.44, "breath": 0.5,
             "flash": False, "sweep": None, "history": ()}
    # The UI renders SNAPSHOT copies (plain State.__new__ instances) —
    # rendering the live object would hide snapshot-only crashes.
    state = _fake_state().snapshot()
    vis = _visual_no_color()
    for h, w in ((24, 80), (50, 200)):
        screen = FakeScreen(h, w)
        tui_mod._draw_all(screen, state, vis, None, False, 0, False, frame)
        calls = _all_calls(screen)
        assert calls, f"no drawing happened at {h}x{w}"
        for y, x, text in calls:
            assert 0 <= x < w, (y, x, text)
            assert 0 <= y < h
            assert x + tg.display_width(text) <= w


@pytest.mark.parametrize("mode", ["running", "waiting", "paused", "stopping",
                                  "done", "aborted"])
@pytest.mark.parametrize("glyph", ["fancy", "ascii"])
@pytest.mark.parametrize("theme", ["ocean", "sunset", "matrix", "mono"])
@pytest.mark.parametrize("failed", [False, True])
def test_render_smoke_all_modes_snapshot(monkeypatch, mode, glyph, theme, failed):
    # Bug: the first TUI frame rendered snap.eta_seconds() on a snapshot
    # whose class had no _lock -> AttributeError. Smoke-render every UI
    # mode from a snapshot so snapshot-only breaks can never return.
    monkeypatch.setattr(curses, "doupdate", lambda: None)
    state = _fake_state()
    state.apply(Event("status", {"status": mode.upper()}))
    if failed:
        state.apply(Event("track_done", {"status": "failed", "video_id": "vF",
                                         "title": "Fail Song", "error": "x",
                                         "retryable": False, "bytes": 0}))
    snap = state.snapshot()
    pal = tt.Palette(tt.THEMES[theme], tt.TIER_NONE)
    colors = {k: 0 for k in tt.COLOR_KEYS}
    vis = tui_mod.Visual(tg.GLYPHS[glyph], pal, colors,
                         lambda idx, bold=False: 0, False, tt.TIER_NONE)
    for h, w in ((24, 80), (50, 200)):
        screen = FakeScreen(h, w)
        frame = {"mode": mode, "phase": 0.1, "thin_phase": 0.4, "breath": 0.5,
                 "flash": failed, "sweep": None, "history": ()}
        tui_mod._draw_all(screen, snap, vis, None, False, 0, False, frame)
        calls = _all_calls(screen)
        assert calls, f"nothing drawn: mode={mode} glyph={glyph} theme={theme}"
        for y, x, text in calls:
            assert 0 <= y < h and 0 <= x < w
            assert x + tg.display_width(text) <= w


def test_render_smoke_help_overlay(monkeypatch):
    monkeypatch.setattr(curses, "doupdate", lambda: None)
    frame = {"mode": "waiting", "phase": 0.2, "thin_phase": 0.3, "breath": 0.8,
             "flash": True, "sweep": None, "history": ((1000.0, 100), (1001.0, 900))}
    state = _fake_state()
    vis = _visual_no_color()
    screen = FakeScreen(24, 80)
    tui_mod._draw_all(screen, state, vis, None, True, 0, True, frame)
    texts = " ".join(t for _, _, t in _all_calls(screen))
    assert "help" in texts
    assert "next theme" in texts


def test_spark_rates():
    history = ((1.5, 0), (1.8, 100), (2.5, 300), (3.5, 700))
    rates = tui_mod._spark_rates(history)
    assert rates == [200, 400]  # 1s buckets: 100@1s -> 300@2s -> 700@3s


def test_panels_have_complete_frames(monkeypatch):
    # Bug: panels drew only their top border/title line; side and bottom
    # borders were missing and panels rendered as empty boxes.
    monkeypatch.setattr(curses, "doupdate", lambda: None)
    g = tg.GLYPHS["fancy"]
    state = _fake_state().snapshot()  # failures > 0, current track set
    vis = _visual_no_color()
    screen = FakeScreen(24, 80)
    # show_log=True so the log panel is part of the layout too.
    tui_mod._draw_all(screen, state, vis, None, True, 0, False,
                      {"mode": "running", "phase": 0.1, "thin_phase": 0.4,
                       "breath": 0.5, "flash": False, "sweep": None,
                       "history": ()})
    framed = [c for c in screen.children if c._h >= 3]
    for child in list(screen.children):
        framed.extend(c for c in child.children if c._h >= 3)
    # the body derwin itself has no drawn borders — panels only
    framed = [c for c in framed if c.addstr_calls]
    assert len(framed) >= 3, f"expected several framed panels, {len(framed)}"
    for child in framed:
        h = child._h
        texts_y: dict[int, str] = {}
        for y, x, text in child.addstr_calls:
            if x <= 1:
                texts_y.setdefault(y, text)
        # top border on row 0, bottom border on the last row
        assert texts_y.get(0, "").startswith(g["tl"]), texts_y
        assert texts_y.get(h - 1, "").startswith(g["bl"]), texts_y
        # every interior row starts with the vertical border
        for y in range(1, h - 1):
            assert texts_y.get(y, "").startswith(g["v"]), (y, texts_y)


def test_overall_bar_capped(monkeypatch):
    # Bug: the overall progress bar was the whole terminal width; it must
    # be capped (<= 100 cells wide per item 2 of the spec).
    monkeypatch.setattr(curses, "doupdate", lambda: None)
    state = _fake_state()
    state.apply(Event("track_done", {"status": "downloaded", "video_id": "vA",
                                     "title": "A", "bytes": 100}))
    assert state.total > 0
    vis = _visual_no_color()
    win = FakeScreen(2, 200)
    tui_mod._draw_overall(win, state, vis, {"mode": "running", "phase": 0.1,
                                            "thin_phase": 0.4, "breath": 0.5,
                                            "flash": False, "sweep": None,
                                            "history": ()})
    big = FakeScreen(2, 300)
    tui_mod._draw_overall(big, state, vis, {"mode": "running", "phase": 0.1,
                                            "thin_phase": 0.4, "breath": 0.5,
                                            "flash": False, "sweep": None,
                                            "history": ()})
    texts = [t for _, _, t in win.addstr_calls] + [t for _, _, t in big.addstr_calls]
    width_chars = [t.count(tg.GLYPHS["fancy"]["bar_fill"]) for t in texts]
    assert width_chars, "no bar drawn"
    assert max(width_chars) <= 100
    assert max(width_chars) == 100  # at w=200/300 the cap is reached


def test_waiting_panel_shows_finished_and_next():
    # bug: 'now playing' was empty while WAITING between tracks.
    g = tg.GLYPHS["fancy"]
    state = State(mode="test")
    state.apply(ev.Event("start", {"mode": "test", "total": 10,
                                   "total_est_bytes": 50000}))
    state.apply(ev.Event("track_done", {"status": "downloaded",
                                        "video_id": "v9", "title": "Done One",
                                        "bytes": 1000}))
    state.apply(ev.Event("delay", {"remaining": 4.2,
                                   "finished": "Done One", "next": "Next Two"}))
    snap = state.snapshot()
    win = FakeScreen(6, 100)
    _draw = tui_mod._draw_current
    frame = {"mode": "waiting", "phase": 0.0, "thin_phase": 0.0,
             "breath": 0.5, "flash": False, "sweep": None, "history": ()}
    _draw(win, snap, _visual_no_color(), frame)
    texts = " ".join(t for _, _, t in win.addstr_calls)
    assert "finished: Done One" in texts
    assert "next: Next Two" in texts


def test_delay_event_carries_titles():
    from ytm_offline import batch, events as ev
    monkey = __import__("unittest.mock", fromlist=["patch"])
    with monkey.patch.object(batch, "download_track", autospec=True) as dl:
        dl.return_value = __import__("ytm_offline.download", fromlist=["x"]).DownloadResult(
            status="downloaded", filepath=None)
        seen = []
        entries = [
            {"video_id": "a", "title": "First", "artist": "A", "duration": 5},
            {"video_id": "b", "title": "Second", "artist": "A", "duration": 5},
        ]
        batch.run_batch_download(
            entries, output_root=pathlib.Path(tempfile.gettempdir()) / "delay-test",
            event_callback=seen.append, delay=0.4, mode="test")
    delays = [e for e in seen if e["type"] == "delay"]
    assert delays, "expected delay events"
    first = delays[0]["payload"]
    assert first["finished"] == "First" and first["next"] == "Second"
    state = ev.State()
    for e in seen:
        state.apply(ev.Event(e["type"], e["payload"]))
    assert state.last_finished == "First" and state.next_title == "Second"


def test_empty_waiting_panel_keeps_frame():
    # No last_finished info yet (old manifests) -> only borders, no crash.
    g = tg.GLYPHS["fancy"]
    state = State(mode="test")
    state.apply(ev.Event("delay", {"remaining": 3.0}))
    snap = state.snapshot()
    win = FakeScreen(6, 100)
    frame = {"mode": "waiting", "phase": 0.0, "thin_phase": 0.0,
             "breath": 0.5, "flash": False, "sweep": None, "history": ()}
    tui_mod._draw_current(win, snap, _visual_no_color(), frame)
    texts = [t for _, _, t in win.addstr_calls]
    assert texts[0].startswith(g["tl"]) and texts[-1].startswith(g["bl"])


def test_current_bar_suffix_never_overwrites_right_border():
    # 80 columns, long suffix "123.4 MB/s ETA 99:59:59": the suffix must be
    # truncated so the right border cell at column w-1 is never overwritten.
    state = State(mode="test")
    state.apply(Event("start", {"total": 2, "total_est_bytes": 4000000}))
    state.apply(Event("track_start", {"video_id": "v1", "title": "T", "artist": "A",
                                      "album": "B", "source": "artist:A",
                                      "stage": "downloading"}))
    state.apply(Event("track_progress", {"stage": "downloading", "percent": 40.0,
                                         "downloaded_bytes": 4000, "total_bytes": 10000,
                                         "speed": "123.4 MB/s", "eta_seconds": 359999}))
    snap = state.snapshot()
    win = FakeScreen(6, 80)
    frame = {"mode": "running", "phase": 0.0, "thin_phase": 0.0,
             "breath": 0.5, "flash": False, "sweep": None, "history": ()}
    tui_mod._draw_current(win, snap, _visual_no_color(), frame)
    v = tg.GLYPHS["fancy"]["v"]
    border = [(x, t) for (y, x, t) in win.addstr_calls if y == 4 and t == v]
    assert (79, v) in border, "right border missing at column w-1"
    # The bar line itself ends before the border column.
    line = [t for (y, x, t) in win.addstr_calls if y == 4 and x == 0]
    assert line, "bar line not drawn"
    assert tg.display_width(line[0]) <= 79
    assert "…" in line[0]  # long tail was cut, not allowed to touch the border


# --- space-background bars (gradient without █ seams) ------------------------


def _visual_bar_tables() -> "tui_mod.Visual":
    """Colored-tier visual with hand-made bar pair tables (no real curses)."""
    theme = tt.THEMES["ocean"]
    pal = tt.Palette(theme, tui_theme_tier())
    bar_bg = {p: 100 + p for p in range(len(pal.gradient_exact))}
    bar_edge = {p: 200 + p for p in range(len(pal.gradient_exact))}
    return tui_mod.Visual(
        tg.GLYPHS["fancy"], pal, {k: 0 for k in tt.COLOR_KEYS},
        lambda idx, bold=False: 0, False, tui_theme_tier(),
        bar_bg=bar_bg, bar_edge=bar_edge, track_attr=50,
    )


def tui_theme_tier() -> str:
    return tt.TIER_FIXED


def _win(w: int = 140):
    win = FakeScreen(3, w)
    return win


def test_running_bar_is_spaces_with_crest_attrs():
    vis = _visual_bar_tables()
    body = tg.bar(vis.g, 24, 43.0)  # forces a partial edge glyph (frac 3/8)
    win = _win()
    tui_mod._draw_themed_line(win, 1, " 3/10 ", body, " 43.0%", vis,
                              "running", 0.13, 0.5, None)
    texts = [(t, a) for _, _, t, a in win.attr_calls]
    assert texts[0] == (" 3/10 ", 0)     # prefix drawn plain
    bar = "".join(t for t, _a in texts[1:-1])
    # fill cells become SPACES; the partial edge stays a 1/8 glyph
    assert "\u2588" not in bar
    assert tg.GLYPHS["fancy"]["bar_edge"][2] in bar  # edge at 43% is the 3/8 glyph
    # track run is one grouped addstr of spaces with the track attr
    track_runs = [(t, a) for t, a in texts if a == 50]
    assert len(track_runs) == 1
    assert track_runs[0][0].strip(" ") == ""  # spaces only
    # suffix keeps base_attr (0 here)
    assert texts[-1][0] == " 43.0%" and texts[-1][1] == 0
    # running uses the crest: several distinct gradient bg attrs across fills
    bg_attrs = {a for t, a in texts if 100 <= a < 200}
    assert len(bg_attrs) >= 3


def test_waiting_bar_is_static_gradient():
    vis = _visual_bar_tables()
    body = tg.bar(vis.g, 24, 41.7)
    win = _win()
    tui_mod._draw_themed_line(win, 1, "", body, "", vis,
                              "waiting", 0.0, 0.5, None)
    bg_attrs = [(a, t) for _, _, t, a in win.attr_calls if 100 <= a < 200]
    # clickable data: positions monotonically non-decreasing across...
    spaced = True
    pos_list = [a - 100 for a, _ in bg_attrs]
    assert spaced and all(b >= a for a, b in zip(pos_list, pos_list[1:]))


def test_none_tier_bar_keeps_fill_chars():
    vis = _visual_no_color()
    body = tg.bar(vis.g, 10, 50.0)
    win = _win()
    tui_mod._draw_themed_line(win, 1, "", body, "", vis,
                              "running", 0.13, 0.5, None)
    texts = [t for _, _, t, _a in win.attr_calls]
    assert "\u2588" in "".join(texts)


def test_bar_pairs_fake_curses():
    import types

    calls: list[tuple] = []

    class Cur:
        COLOR_PAIRS = 256

        def init_pair(self, n, fg, bg):
            calls.append((n, fg, bg))

        def color_pair(self, n):
            return n * 10

    cur = Cur()
    grad = {p: 16 + p for p in range(24)}
    bg, edge, track = tui_mod._make_bar_pairs(cur, grad, 232, 1)
    assert len(bg) == 24 and len(edge) == 24 and track is not None
    # 24 bg + 24 edge + 1 track = 49 pairs numbered 1..49
    assert (calls[0]) == (1, 16, 16)          # bg: same grad fg+bg
    assert (calls[1]) == (2, 16, 232)         # edge: fg grad, bg track
    assert calls[48] == (49, 232, 232)        # track pair last
    assert bg[0] == 10 and edge[0] == 20


def test_bar_pairs_out_of_budget_fallback():
    import types

    class Cur:
        COLOR_PAIRS = 3

        def init_pair(self, n, fg, bg):
            pass

        def color_pair(self, n):
            return n

    bg, edge, track = tui_mod._make_bar_pairs(Cur(), {0: 16}, 232, 2)
    assert bg == {} and edge == {} and track is None

def test_braille_graph_flat_baseline_and_rising():
    flat = tg.braille_graph([], 10)
    assert flat == "\u28c0" * 10  # both bottom dots, never a ramp
    rising = tg.braille_graph([0, 0, 10, 40, 160, 640], 3)
    assert all(0x2800 <= ord(c) <= 0x28FF for c in rising)
    assert ord(rising[2]) > ord(rising[0])  # grows toward the right
    assert all(tg.char_width(c) == 1 for c in rising)


def test_header_band_full_width_with_badge(monkeypatch):
    monkeypatch.setattr(curses, "doupdate", lambda: None)
    state = _fake_state().snapshot()
    win = FakeScreen(1, 100)
    tui_mod._draw_header(win, state, _visual_no_color())
    texts = [t for _, _, t in win.addstr_calls]
    joined = "".join(texts)
    assert "ytm-offline" in joined and "RUNNING" in joined
    # every cell of the row is covered: left band + badge + pad
    assert tui_mod.tg.display_width(joined) >= 99
    if len(win.addstr_calls) > 1:
        badge_y, badge_x, badge_text, badge_attr = win.attr_calls[0]
        assert badge_attr != 0


def test_status_chips_and_gauge(monkeypatch):
    monkeypatch.setattr(curses, "doupdate", lambda: None)
    state = _fake_state().snapshot()
    win = FakeScreen(4, 80)
    tui_mod._draw_counters(win, state, _visual_no_color(),
                           {"mode": "running", "phase": 0.1, "thin_phase": 0.4,
                            "breath": 0.5, "flash": False, "sweep": None,
                            "history": ()})
    rows = [" ".join(t for _, _, t in child_calls) for child_calls in
            [[c for c in win.addstr_calls if y == 1] for y in range(4)]]
    body = " ".join(rows)
    assert "downloaded" in body and "archived" in body
    assert "failed" in body and "retries" in body
    assert "/3" in body and ("\u25cf" in body or "\u25cb" in body)


def test_failures_panel_hidden_when_no_failures(monkeypatch):
    monkeypatch.setattr(curses, "doupdate", lambda: None)
    state = State("t")
    state.apply(Event("start", {"total": 3, "total_est_bytes": 3000}))
    state.apply(Event("track_start", {"video_id": "v1", "title": "Song One",
                                      "artist": "Artist", "album": "Album",
                                      "source": "artist:A", "stage": "downloading"}))
    screen = FakeScreen(24, 80)
    tui_mod._draw_all(screen, state.snapshot(), _visual_no_color(), None,
                      False, 0, False,
                      {"mode": "running", "phase": 0.1, "thin_phase": 0.4,
                       "breath": 0.5, "flash": False, "sweep": None,
                       "history": ()})
    assert not any("recent failures" in t for _, _, t in _all_calls(screen))


def test_log_panel_only_tall_terminals(monkeypatch):
    monkeypatch.setattr(curses, "doupdate", lambda: None)
    state = _fake_state().snapshot()
    vis = _visual_no_color()
    frame = {"mode": "running", "phase": 0.1, "thin_phase": 0.4, "breath": 0.5,
             "flash": False, "sweep": None, "history": ()}
    small = FakeScreen(24, 80)
    tui_mod._draw_all(small, state, vis, None, True, 0, False, frame)
    assert not any(
        "log" in t and "\u256d" in t for _, _, t in _all_calls(small))
    tall = FakeScreen(32, 100)
    tui_mod._draw_all(tall, state, vis, None, True, 0, False, frame)
    assert any(
        "log" in t and "\u256d" in t for _, _, t in _all_calls(tall))


def test_overall_line2_braille_graph(monkeypatch):
    import time as time_mod
    monkeypatch.setattr(curses, "doupdate", lambda: None)
    state = _fake_state().snapshot()
    win = FakeScreen(4, 100)
    now = time_mod.monotonic()
    frame = {"mode": "running", "phase": 0.1, "thin_phase": 0.4, "breath": 0.5,
             "flash": False, "sweep": None,
             "history": tuple((now - i, i * 1000) for i in range(60))}
    tui_mod._draw_overall(win, state, vis_ := _visual_no_color(), frame)
    row2 = " ".join(t for y, _, t in win.addstr_calls if y == 2)
    assert "tracks" in row2 and "ETA" in row2
    assert "\u28c0" in row2 or any(0x2800 <= ord(c) <= 0x28FF for c in row2)


def test_content_capped_at_140_columns(monkeypatch):
    monkeypatch.setattr(curses, "doupdate", lambda: None)
    state = _fake_state().snapshot()
    screen = FakeScreen(32, 300)
    tui_mod._draw_all(screen, state, _visual_no_color(), None, False, 0, False,
                      {"mode": "running", "phase": 0.1, "thin_phase": 0.4,
                       "breath": 0.5, "flash": False, "sweep": None,
                       "history": ()})
    for win in screen.children:
        for y, x, t in _all_calls(win):
            assert tui_mod.tg.display_width(t) <= 140

def test_every_glyph_is_one_display_cell():
    for key, table in tg.GLYPHS.items():
        for name, value in table.items():
            for ch in str(value):
                assert tg.char_width(ch) == 1, (key, name, repr(ch))


def test_tui_glyphs_command_smoke(monkeypatch, capsys, tmp_path):
    import argparse
    from ytm_offline import cli

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    args = cli.build_parser().parse_args(["tui-glyphs"])
    assert cli.cmd_tui_glyphs(args) == 0
    out = capsys.readouterr().out
    for group in ("block", "box", "braille", "shapes", "marks"):
        assert group in out
    assert "tier: none" in out  # piped output never carries ANSI colors
    assert "\x1b[" not in out

@pytest.mark.parametrize("rows,cols", [(24, 80), (30, 120), (50, 200), (60, 300)])
def test_render_matrix_all_sizes(rows, cols, monkeypatch):
    monkeypatch.setattr(curses, "doupdate", lambda: None)
    state = _fake_state().snapshot()
    vis = _visual_no_color()
    for mode in ("running", "waiting", "paused", "stopping", "done", "aborted"):
        screen = FakeScreen(rows, cols)
        tui_mod._draw_all(screen, state, vis, None, False, 0, False,
                          {"mode": mode, "phase": 0.1, "thin_phase": 0.4,
                           "breath": 0.5, "flash": False, "sweep": None,
                           "history": ()})
        calls = _all_calls(screen)
        assert calls
        for y, x, t in calls:
            assert 0 <= y < rows and x + tg.display_width(t) <= cols, (rows, cols, y, x, t)
        framed = [c for c in _desc(screen) if c._h >= 3]
        assert len(framed) >= 2, (rows, cols, mode)


# --- layout spacing / centering / full-width bands (bug polish) ---------------


def _frame_d(h: int, w: int, state, show_log: bool = False):
    tests_frame = {
        "mode": "running", "phase": 0.1, "thin_phase": 0.4, "breath": 0.5,
        "flash": False, "sweep": None, "history": (),
    }
    screen = FakeScreen(h, w)
    curses.doupdate = lambda: None
    tui_mod._draw_all(screen, state, _visual_no_color(), None, show_log, 0,
                      False, tests_frame)
    return screen


def _waiting_state(finished="First", nxt=""):
    st = State("test")
    st.apply(Event("start", {"total": 5, "total_est_bytes": 50000}))
    st.apply(Event("track_done", {"status": "downloaded", "video_id": "v1",
                                  "title": finished, "bytes": 1000}))
    st.apply(Event("delay", {"status": "WAITING 7.0s", "remaining": 7.0,
                             "finished": finished, "next": nxt}))
    return st


def _framed_panels(screen):
    """Every panel sub-window (skip the 1-cell body wrapper and empties)."""
    out = []
    for body in screen.children:
        for win in body.children:
            if win.addstr_calls and win._h >= 3:
                out.append(win)
    return out


def _rows(win):
    rows: dict[int, str] = {}
    for y, x, t in win.addstr_calls:
        rows.setdefault(y, "")
        if x == 0:
            rows[y] = t if not rows[y] else rows[y]
    return rows


@pytest.mark.parametrize("size", [(24, 80), (30, 120), (50, 120), (60, 270)])
def test_every_panel_row_has_both_borders(size):
    h, w = size
    state = _fake_state().snapshot()
    screen = _frame_d(h, w, state)
    g = tg.GLYPHS["fancy"]
    for win in _framed_panels(screen):
        rows = _rows(win)
        gw = tg.display_width
        # 4 corners of a complete frame
        assert rows[0].startswith(g["tl"]) and rows[0].endswith(g["tr"])
        assert rows[win._h - 1].startswith(g["bl"]) and \
            rows[win._h - 1].endswith(g["br"])
        for y in range(1, win._h - 1):
            row = rows.get(y, "")
            assert row.startswith(g["v"]), (y, row)
            rights = [t for yy, x, t in win.addstr_calls
                      if yy == y and x == win._w - 1]
            assert rights and rights[0] == g["v"], (y, rights)
            # no row wider than the panel/terminal
            for yy, xx, t in win.addstr_calls:
                if yy == y:
                    assert gw(t) <= win._w - xx


def test_sanitize_title_with_newline():
    state = _fake_state()
    state.apply(Event("track_start", {
        "video_id": "v9", "title": "Broken\nTitle  with   tabs\t!",
        "artist": "Art", "album": "Alb", "source": "artist:Art",
        "stage": "downloading"}))
    snap = state.snapshot()
    screen = _frame_d(30, 120, snap)
    texts = [t for _, _, t in _all_calls(screen)]
    assert not any(any(ord(c) < 32 or ord(c) == 127 for c in t) for t in texts)
    joined = "\n".join(texts)
    assert "Broken" in joined and "Title with tabs !" in joined
    # the broken title renders on one line, nothing blank after it
    lines = [l for l in joined.splitlines() if "Broken" in l]
    assert len(lines) == 1


def test_overall_framed_single_count():
    state = _fake_state().snapshot()  # 0/3
    body = _frame_d(30, 120, state).children[0]
    overall = body.children[0]
    rows = _rows(overall)
    assert rows[0].startswith(tg.GLYPHS["fancy"]["tl"])
    assert rows[overall._h - 1].startswith(tg.GLYPHS["fancy"]["bl"])
    all_text = " ".join(t for _, _, t in overall.addstr_calls)
    assert all_text.count("2/3") == 1
    assert "ETA" in all_text
    bar_text = _rows(overall)[1]
    assert bar_text.count(tg.GLYPHS["fancy"]["bar_fill"]) <= 100


def test_header_and_footer_bands_full_width():
    state = _fake_state().snapshot()
    h, w = 30, 270
    screen = _frame_d(h, w, state)
    hdr = [t for y, x, t in screen.addstr_calls if y == 0]
    ftr = [t for y, x, t in screen.addstr_calls if y == h - 1]
    # header reaches to the right edge, footer starts at x=0 with full width
    assert hdr
    assert any(tui_mod.tg.display_width(left) >= 100 for left in hdr)
    assert ftr and ftr[0].startswith(" p pause/resume")
    assert any(x > 140 for y, x, t in screen.addstr_calls
               if y in (0, h - 1))


def test_centering_follows_formula():
    state = _waiting_state().snapshot()
    h, w = 50, 120
    body = _frame_d(h, w, state).children[0]
    vis = _visual_no_color()
    heights = tui_mod._panel_heights(state, min(w, 140), vis)
    body_h = h - 2
    base = sum(v for _, v in heights) + (len(heights) - 1)
    free = max(0, body_h - base - 2)
    expected_top = round(free * 0.4)
    first_y = body.children[0]._pos[0]
    assert first_y == expected_top
    assert first_y >= 1  # not stuck to the top
    assert body.children[0]._pos[1] == 0
    last = body.children[len(heights) - 1]
    assert last._pos[0] + last._h <= body_h - 1


@pytest.mark.parametrize("rows,cols", [(40, 120), (60, 270)])
def test_centering_spec_at_120x40_and_270x60(rows, cols):
    """Spec: free = (rows-2) - body_height - 2; 40% of free above the body,
    the rest below; never negative; body not stuck to the top."""
    state = _waiting_state().snapshot()
    body = _frame_d(rows, cols, state).children[0]
    vis = _visual_no_color()
    heights = tui_mod._panel_heights(state, min(cols, 140), vis)
    base = sum(v for _, v in heights) + (len(heights) - 1)
    body_h = rows - 2
    free = max(0, body_h - base - 2)
    top_margin = round(free * 0.4)
    assert free > 0
    first_y = body.children[0]._pos[0]
    last = body.children[len(heights) - 1]
    top_blank = first_y
    bottom_blank = body_h - (last._pos[0] + last._h)
    assert top_blank == top_margin, (top_blank, top_margin, free)
    assert top_blank >= 1  # not stuck to the top
    assert bottom_blank == free + 2 - top_margin  # the rest below
    assert bottom_blank >= top_blank



def test_compact_has_no_blank_rows():
    state = _fake_state().snapshot()
    body = _frame_d(24, 80, state).children[0]
    wins = body.children
    ys = [win._pos[0] for win in wins]
    heights = [win._h for win in wins]
    for i in range(len(ys) - 1):
        assert ys[i + 1] == ys[i] + heights[i], (ys, heights)


def test_log_visible_no_centering_and_fills():
    state = _fake_state().snapshot()
    h, w = 50, 120
    body = _frame_d(h, w, state, show_log=True).children[0]
    assert body.children[0]._pos[0] == 0  # no centering
    log = body.children[-1]
    body_h = h - 2
    assert log._pos[0] + log._h == body_h  # fills to the bottom
    assert log._h >= 10


def test_resize_leaves_no_stale_cells():
    state = _fake_state().snapshot()
    screen = FakeScreen(60, 270)
    curses.doupdate = lambda: None
    big_frame = {"mode": "running", "phase": 0.1, "thin_phase": 0.4,
                 "breath": 0.5, "flash": False, "sweep": None, "history": ()}
    tui_mod._draw_all(screen, state, _visual_no_color(), None, False, 0,
                      False, big_frame)
    screen.erase()
    screen._h, screen._w = 24, 80  # simulated resize
    small_frame = dict(big_frame)
    tui_mod._draw_all(screen, state, _visual_no_color(), None, False, 0,
                      False, small_frame)
    texts = [t for _, _, t in _all_calls(screen)]
    assert all(tui_mod.tg.display_width(t) <= 270 for t in texts)
    # nothing remembered from the big layout: the only body child is new
    assert len(screen.children) == 1
    for win in screen.children:
        for pan in win.children:
            assert pan._w <= 80
