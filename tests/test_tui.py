"""Pure-function tests for TUI helpers."""

import pytest

from ytm_offline import tui
from ytm_offline.logging_config import redact


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "00:00"),
        (59, "00:59"),
        (60, "01:00"),
        (3661, "1:01:01"),
    ],
)
def test_format_duration(seconds, expected):
    assert tui.format_duration(seconds) == expected


@pytest.mark.parametrize(
    "n,expected",
    [
        (0, "0.0 B"),
        (512, "512.0 B"),
        (2048, "2.0 KB"),
        (2097152, "2.0 MB"),
        (1073741824, "1.0 GB"),
    ],
)
def test_format_bytes(n, expected):
    assert tui.format_bytes(n) == expected


def test_render_bar_unicode():
    bar = tui.render_bar(10, 50)
    assert len(bar) == 10
    assert "\u2588" in bar


def test_render_bar_ascii():
    bar = tui.render_bar(10, 50, ascii_mode=True)
    assert bar == "[#####-----]"


def test_truncate():
    assert tui.truncate("short", 10) == "short"
    assert tui.truncate("a very long string indeed", 12).endswith("\u2026")
    assert len(tui.truncate("a very long string indeed", 12)) == 12


def test_redact():
    msg = "Cookie: ABC123; Authorization: Bearer XYZ; SAPISID=secret"
    out = redact(msg)
    assert "ABC123" not in out
    assert "Bearer XYZ" not in out
    assert "secret" not in out
    assert "Cookie:" in out
    assert "Authorization:" in out
    assert "SAPISID=" in out


def _running_state():
    """A State with a realistic running download applied."""
    from ytm_offline import events as ev

    state = ev.State(mode="artist")
    state.apply(ev.Event("start", {"mode": "artist", "total": 10,
                                  "total_est_bytes": 30000}))
    for i in range(4):
        state.apply(ev.Event("track_start",
                             {"video_id": f"v{i}", "title": f"T{i}",
                              "artist": "A", "album": "B", "source": "s",
                              "stage": "downloading"}))
        state.apply(ev.Event("track_done", {"status": "downloaded",
                                            "bytes": 1000}))
    state.apply(ev.Event("delay", {"remaining": 3.0}))
    return state


def test_snapshot_methods_do_not_touch_a_lock():
    # The UI renders snapshots (plain copies without _lock); every
    # method the UI calls must work on a snapshot or the TUI crashes on
    # its first frame (bug: eta_seconds raised AttributeError on _lock).
    from ytm_offline import events as ev

    state = _running_state()
    snap = state.snapshot()
    assert state.eta_seconds() is not None
    for method in ("eta_seconds", "recent_track_rate"):
        val = getattr(snap, method)()  # must not raise
        assert val is not None or method != "eta_seconds"
    assert snap.recent_track_rate() > 0.0
    assert snap.eta_seconds() == state.eta_seconds()


def test_snapshot_dataclass_replace_required_fields():
    from ytm_offline import events as ev

    state = _running_state()
    snap = state.snapshot()
    # All fields the UI reads must exist on the copy.
    for f in ("mode", "status", "total", "done", "downloaded", "archived",
              "skipped_size", "failed", "retries", "current",
              "total_bytes_downloaded", "log_lines", "recent_failures",
              "elapsed_seconds"):
        assert getattr(snap, f, "MISSING") != "MISSING" or f in (
            "elapsed_seconds",)  # elapsed_seconds always set


def test_ui_crash_stops_worker_gracefully(monkeypatch, tmp_path, capsys):
    # If the UI thread raises, the worker must be told to stop (finishing
    # its current track) and the user gets a short message with the log
    # path instead of a raw traceback.
    monkeypatch.setattr(tui, "supports_tui", lambda: True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tui, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(tui.curses, "wrapper",
                        lambda ui, *a: (_ for _ in ()).throw(
                            RuntimeError("ui boom")))

    seen = {"stopped": False}

    def fake_worker(entries=None, event_callback=None, control=None, **kw):
        import time
        event_callback({"type": "start",
                        "payload": {"mode": "test", "total": 3}})
        for _ in range(200):  # poll like a real worker would
            if control.should_stop():
                seen["stopped"] = True
                return {"summary": "stopped"}
            time.sleep(0.05)
        return {"summary": "done"}

    ret = tui.run_tui(fake_worker, {"entries": [1, 2]})
    assert ret == 1
    assert seen["stopped"] is True
    err = capsys.readouterr().err
    assert "TUI crashed: RuntimeError: ui boom" in err
    assert "full log:" in err
