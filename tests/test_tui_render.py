"""Renderer tests: state→animation mapping and a FakeScreen smoke test."""

from __future__ import annotations

import curses

from ytm_offline import tui_glyphs as tg
from ytm_offline import tui_theme as tt
from ytm_offline import tui as tui_mod
from ytm_offline.events import State


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
        self.children: list[FakeScreen] = []

    def getmaxyx(self):
        return self._h, self._w

    def erase(self):
        pass

    def noutrefresh(self):
        pass

    def derwin(self, nh, nw, y, x):
        child = FakeScreen(nh, nw)
        self.children.append(child)
        return child

    def addstr(self, y, x, text, attr=0):
        self.addstr_calls.append((y, x, str(text)))


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
    return state


def _all_calls(screen: FakeScreen) -> list:
    calls = list(screen.addstr_calls)
    for child in screen.children:
        calls.extend(_all_calls(child))
    return calls


def test_render_smoke_no_exceptions(monkeypatch):
    monkeypatch.setattr(curses, "doupdate", lambda: None)
    frame = {"mode": "running", "phase": 0.13, "thin_phase": 0.44, "breath": 0.5,
             "flash": False, "sweep": None, "history": ()}
    state = _fake_state()
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
