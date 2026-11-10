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


def test_latest_log_path_returns_default_when_empty(monkeypatch):
    monkeypatch.setattr("glob.glob", lambda pattern: [])
    assert tui._latest_log_path() == "logs/"


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


def test_supports_tui_requires_stdin_and_stdout(monkeypatch):
    # Piping stdin must fall back to plain logging, not enter curses.
    monkeypatch.setattr(tui.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(tui.sys.stdin, "isatty", lambda: False)
    assert tui.supports_tui() is False
    monkeypatch.setattr(tui.sys.stdin, "isatty", lambda: True)
    assert tui.supports_tui() is True


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


def test_batch_start_carries_est_bytes(monkeypatch):
    # bug: TUI showed 'downloaded 9.3 MB / est 0.0 B' — start event must
    # carry the manifest estimate (same numbers as summarize_entries).
    from ytm_offline import batch, events as ev

    monkeypatch.setattr(
        batch,
        "download_track",
        lambda *a, **k: __import__("ytm_offline.download", fromlist=["x"]).DownloadResult(
            status="downloaded", filepath=None
        ),
    )
    entries = [
        {"video_id": f"v{i}", "title": f"T{i}", "artist": "A",
         "duration": 100, "est_size": est}
        for i, est in enumerate((1000, 2000, 3000), start=1)
    ]
    seen = []
    batch.run_batch_download(
        entries, output_root=ev.__file__ and __import__("pathlib").Path(
            __import__("tempfile").gettempdir()) / "batch-est-test",
        event_callback=lambda d: seen.append(d), delay=0.0, mode="test",
    )
    start = [d for d in seen if d["type"] == "start"][0]
    assert start["payload"]["total_est_bytes"] == 6000
    state = ev.State()
    for d in seen:
        state.apply(ev.Event(d["type"], d["payload"]))
    assert state.total_est_bytes == 6000


# --- item 10: non-daemon worker, graceful quit, force exit -------------------


class _FakeWin:
    """Bare curses-window stand-in recording addstr text."""

    def __init__(self, h, w):
        self._maxyx = (h, w)
        self.writes = []

    def getmaxyx(self):
        return self._maxyx

    def addstr(self, y, x, text, attr=0):
        self.writes.append(text)


def test_worker_quit_mid_track_completes_and_saves(tmp_path):
    # q mid-track: the non-daemon worker must finish its unwind (the real
    # worker writes archive/manifest/m3u8 and cleans temp files) before
    # _join_worker returns.
    import threading
    import time

    from ytm_offline import events as ev2
    from ytm_offline.control import Control

    state = ev2.State(mode="test")
    control = Control()
    container = {"return_value": None, "exception": None}
    saved = tmp_path / "state.txt"

    def fake_worker(event_callback=None, control=None, **kw):
        event_callback({"type": "start",
                        "payload": {"mode": "test", "total": 1}})
        for _ in range(40):  # ~2 s of work, polls should_stop like real workers
            if control.should_stop():
                break
            time.sleep(0.05)
        saved.write_text("completed")  # archive/manifest/m3u8 written here
        return {"summary": "stopped"}

    thread = threading.Thread(target=tui._worker_target,
                              args=(fake_worker, {}, state, control, container))
    thread.start()
    time.sleep(0.4)  # quit mid-work
    control.request_stop()
    tui._join_worker(thread)
    assert thread.is_alive() is False
    assert saved.read_text() == "completed"  # state/manifest saved on quit
    assert container["exception"] is None
    assert container["return_value"] == {"summary": "stopped"}


def test_footer_shows_finishing_when_stopping():
    from ytm_offline import events as ev

    state = ev.State(mode="test")
    state.apply(ev.Event("status", {"status": "STOPPING", "reason": "user quit"}))
    win = _FakeWin(1, 60)
    tui._draw_footer(win, state, None, 0)
    assert "finishing the current track" in win.writes[0]
    assert "quit" in win.writes[0]


def test_footer_default_is_keys():
    from ytm_offline import events as ev

    state = ev.State(mode="test")
    state.apply(ev.Event("status", {"status": "RUNNING"}))
    win = _FakeWin(1, 60)
    tui._draw_footer(win, state, None, 0)
    assert " p pause/resume" in win.writes[0]


def test_join_worker_second_ctrl_c_force_exit(monkeypatch, capsys):
    # A second Ctrl-C while waiting for the worker must print the partial
    # file warning and force-exit instead of hanging forever.
    import threading

    def raise_kbd(self, timeout=None):
        raise KeyboardInterrupt()

    monkeypatch.setattr(threading.Thread, "join", raise_kbd)
    monkeypatch.setattr(tui.os, "_exit", lambda code: (_ for _ in ()).throw(
        SystemExit(code)))
    t = threading.Thread(target=lambda: None)
    t.start()
    with pytest.raises(SystemExit):
        tui._join_worker(t, "logs/x.log")
    err = capsys.readouterr().err
    assert "partial file may remain" in err
    assert "logs/x.log" in err
