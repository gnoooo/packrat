"""Tests for the A/S release picker (grammar, sections, selection)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ytm_offline import artists


def test_parse_selection_grammar():
    assert artists.parse_selection("A[1-3],S[2]") == [
        {"letter": "A", "spec": [(1, 3)], "exclude": False},
        {"letter": "S", "spec": [(2, 2)], "exclude": False},
    ]
    assert artists.parse_selection("A[3-]") == [
        {"letter": "A", "spec": [(3, None)], "exclude": False},
    ]
    assert artists.parse_selection("A[1,3]") == [
        {"letter": "A", "spec": [(1, 1), (3, 3)], "exclude": False},
    ]
    assert artists.parse_selection("-S[4]") == [
        {"letter": "S", "spec": [(4, 4)], "exclude": True},
    ]
    assert artists.parse_selection("s") == [
        {"letter": "S", "spec": None, "exclude": False},
    ]
    assert artists.parse_selection("all") == [
        {"letter": "A", "spec": None, "exclude": False},
        {"letter": "S", "spec": None, "exclude": False},
    ]


def test_parse_selection_rejects_unknown_sections():
    for bad in ("T", "T[1]", "L", "R", "D", "X[2]"):
        with pytest.raises(ValueError) as exc:
            artists.parse_selection(bad)
        letter = bad[0].upper()
        assert str(exc.value) == f"unknown section '{letter}' (valid: A, S)"
    with pytest.raises(ValueError):
        artists.parse_selection("")


def _meta(
    row: int,
    title: str,
    *,
    year: str = "2020",
    mtype: str = "album",
    section: str = "A",
    status: str = "kept",
    tracks: int = 10,
    new: int = 3,
    tags: list[str] | None = None,
    bid: str | None = None,
    reason: str | None = None,
) -> dict:
    meta = {
        "row": row,
        "title": title,
        "year": year,
        "type": mtype,
        "section": section,
        "browse_id": bid or f"MPREb_{row}",
        "status": status,
    }
    if status == "kept":
        meta["tracks"] = tracks
        meta["new_tracks"] = new
    else:
        meta["new_tracks"] = new
        meta["tracks"] = tracks
        if reason:
            meta["reason"] = reason
    if tags:
        meta["tags"] = tags
    return meta


def _catalog(releases: list[dict], entries: list[dict] | None = None) -> dict:
    entries = entries or []
    payload = {"artist": "Artist", "entries": entries, "releases": releases, "totals": {}}
    return {
        "manifest_path": "x.manifest.json",
        "payload": payload,
        "artist": "Artist",
        "subscribers": "",
        "label": "Artist",
        "totals": {},
        "entries": entries,
        "_resolve_cache": {},
    }


def _sections(catalog: dict) -> dict:
    rows = artists.build_sections(catalog)
    assert set(rows) == {"A", "S"}
    return rows


def test_section_assignment_from_releases():
    releases = [
        _meta(0, "An Album", mtype="album", section="A", new=2),
        _meta(1, "A Single", mtype="single", section="S", new=1),
        _meta(2, "An EP", mtype="ep", section="S", new=1, tags=["live"], status="excluded"),
        _meta(3, "Old Live Boot", mtype="album", section="A", status="excluded", new=0,
              tags=["live"], reason="live"),
        _meta(4, "Remix Single", mtype="single", section="S", status="excluded", new=0,
              tags=["remix"], reason="remix"),
        _meta(5, "An Album (Deluxe)", mtype="album", section="A", status="excluded", new=0,
              tags=["dup of An Album"], reason="dup of An Album"),
    ]
    catalog = _catalog(releases)
    rows = _sections(catalog)
    a_titles = [r["meta"]["title"] for r in rows["A"]]
    s_titles = [r["meta"]["title"] for r in rows["S"]]
    assert a_titles == ["An Album", "An Album (Deluxe)", "Old Live Boot"]
    assert s_titles == ["A Single", "An EP", "Remix Single"]
    # New releases numbered from 1 per section, newest year first.
    assert [r["n"] for r in rows["A"]] == [1, 2, 3]
    assert [r["n"] for r in rows["S"]] == [1, 2, 3]


def test_section_numbering_newest_first_then_title():
    releases = [
        _meta(0, "Beta", year="2019"),
        _meta(1, "Alpha", year="2019"),
        _meta(2, "Gamma", year="2024"),
    ]
    rows = artists.build_sections(_catalog(releases))
    titles = [r["meta"]["title"] for r in rows["A"]]
    assert titles == ["Gamma", "Alpha", "Beta"]
    assert [r["n"] for r in rows["A"]] == [1, 2, 3]


def test_section_membership_first_section_wins(monkeypatch):
    yt = MagicMock()
    rel = {"title": "Dual", "year": "2020", "browseId": "MPREb_dual"}
    yt.get_artist.return_value = {
        "name": "Artist",
        "subscribers": "1M",
        "albums": {"results": [rel]},
        "singles": {"browseId": "MPAD", "params": "p",
                    "results": [{**rel, "type": "Single"}]},
    }
    yt.get_artist_albums.return_value = [{**rel, "type": "Single"}]
    disc = artists.get_discography(yt, "UCartist")
    rows = [r for r in disc["releases"] if r["browseId"] == "MPREb_dual"]
    assert len(rows) == 1
    assert rows[0]["_section"] == "albums"


def test_default_selection_skips_tagged_and_zero_new():
    releases = [
        _meta(0, "Good Album", new=2),
        _meta(1, "Nothing New", new=0),
        _meta(2, "Tagged Single", mtype="single", section="S", status="excluded",
              new=1, tags=["live"], reason="live"),
        _meta(3, "Live Boot", section="A", status="excluded", new=0, tags=["live"]),
    ]
    rows = artists.build_sections(_catalog(releases))
    assert rows["A"][0]["selected"] is True   # Good Album
    assert rows["A"][1]["selected"] is False  # 0 new
    assert rows["S"][0]["selected"] is False  # tagged
    assert rows["A"][2]["selected"] is False  # tagged


def test_all_rule_includes_tagged_and_minus_excludes():
    releases = [
        _meta(0, "Good Album", new=2),
        _meta(1, "Nothing New", new=0),
        _meta(2, "Tagged Single", mtype="single", section="S", status="excluded",
              new=0, tags=["live"]),
    ]
    catalog = _catalog(releases)
    rows = _sections(catalog)
    picked = artists.chosen_rows(rows, artists.parse_selection("all"))
    assert [r["meta"]["title"] for r in picked] == [
        "Good Album", "Nothing New", "Tagged Single",
    ]
    # all minus one: default zeros out and the tagged single is removable.
    picked = artists.chosen_rows(rows, artists.parse_selection("A[*],S[*],-S[1]"))
    assert [r["meta"]["title"] for r in picked] == ["Good Album", "Nothing New"]
    # Out-of-range silently dropped.
    picked = artists.chosen_rows(rows, artists.parse_selection("A[1-9]"))
    assert [r["meta"]["title"] for r in picked] == ["Good Album", "Nothing New"]


def _kept_entries() -> list[dict]:
    return [
        {"video_id": "a1", "title": "T1", "artist": "Artist", "duration": 100,
         "release_row": 0, "release_type": "album"},
        {"video_id": "a2", "title": "T2", "artist": "Artist", "duration": 100,
         "release_row": 0, "release_type": "album"},
        {"video_id": "s1", "title": "S1", "artist": "Artist", "duration": 100,
         "release_row": 1, "release_type": "single"},
    ]


def _kept_fixture() -> tuple[dict, dict]:
    releases = [
        _meta(0, "An Album", new=2),
        _meta(1, "A Single", mtype="single", section="S", new=1),
    ]
    catalog = _catalog(releases, _kept_entries())
    return catalog, _sections(catalog)


def test_apply_selection_keeps_subset(tmp_path):
    catalog, rows = _kept_fixture()
    out = artists.apply_selection(
        catalog, rows, artists.parse_selection("A[1]"),
        headers_path="/nonexistent", output_root=tmp_path,
        archive_path=tmp_path / "archive",
    )
    assert out is not None
    assert [e["video_id"] for e in out["entries"]] == ["a1", "a2"]
    assert out["totals"]["new_tracks"] == 2
    assert out["payload"]["entries"] == out["entries"]
    assert out["payload"]["releases"][0]["selected"] is True
    assert out["payload"]["releases"][1]["selected"] is False


def test_apply_selection_default_when_no_tokens(tmp_path):
    catalog, rows = _kept_fixture()
    out = artists.apply_selection(
        catalog, rows, [],
        headers_path="/nonexistent", output_root=tmp_path,
        archive_path=tmp_path / "archive",
    )
    assert out is not None
    assert [e["video_id"] for e in out["entries"]] == ["a1", "a2", "s1"]


def test_apply_selection_none_rows_returns_none(tmp_path):
    catalog, rows = _kept_fixture()
    assert artists.apply_selection(
        catalog, rows, artists.parse_selection("A[9]"),
        headers_path="/nonexistent", output_root=tmp_path,
        archive_path=tmp_path / "archive",
    ) is None


def test_apply_selection_tagged_row_uses_run_cache(tmp_path):
    # Tagged releases resolve from _resolve_cache (no network, no headers).
    releases = [
        _meta(0, "Good Album", new=0),
        _meta(1, "Live Boot", section="A", status="excluded", new=0,
              tags=["live"], reason="live", tracks=1),
    ]
    cache = {releases[1]["browse_id"]: [
        {"video_id": "l1", "title": "Live Track", "artist": "Artist",
         "duration_seconds": 120, "track_num": 1},
    ]}
    catalog = _catalog(releases)
    catalog["_resolve_cache"] = cache
    rows = _sections(catalog)
    out = artists.apply_selection(
        catalog, rows, artists.parse_selection("A[2]"),
        headers_path="/MISSING-must-not-be-touched", output_root=tmp_path,
        archive_path=tmp_path / "archive",
    )
    assert out is not None
    assert [e["video_id"] for e in out["entries"]] == ["l1"]
    assert out["entries"][0]["release_type"] == "album"  # live boot is an album kind


def test_print_sections_smoke(capsys):
    catalog, rows = _kept_fixture()
    artists.print_sections(rows, width=100)
    out = capsys.readouterr().out
    assert "A  Albums ·" in out
    assert "S  Singles & EPs ·" in out
    assert "■  1 An Album" in out
    assert "● 2 A Single" not in out


def _plain_rows(n: int = 4) -> list[dict]:
    return [
        _meta(i, f"Rel {i}", year=str(2020 - i), tracks=3, new=i % 2)
        for i in range(n)
    ]


def _columns_of(width: int) -> int:
    return 3 if width >= 100 else 2 if width >= 66 else 1


def _cell_w(width: int) -> int:
    return min((width - 4) // _columns_of(width), 64)


def test_renderer_column_major_and_width(capsys):
    rows = artists.build_sections(_catalog(_plain_rows(4)))
    artists.print_sections(rows, width=80)
    out = capsys.readouterr().out
    lines = out.splitlines()
    # 2 columns at width 80, column-major fill: rows 1 and 3 on line 1.
    s_block = lines[lines.index("A  Albums · 2/4 selected · 2 tracks to download") + 1:]
    # Column-major: line 1 holds rows 1 and 3.
    assert "Rel 0" in s_block[0] and "Rel 2" in s_block[0]
    assert "Rel 1" in s_block[1] and "Rel 3" in s_block[1]
    for line in lines:
        assert artists._dwidth(line.rstrip()) <= 80
    assert "\x1b[" not in out  # no colors off-tty


@pytest.mark.parametrize("width", [40, 66, 80, 100, 120, 200])
def test_renderer_no_line_overflows(width: int, capsys):
    releases = [
        _meta(0, "A Super Long Album Title Which Definitely Needs Truncating Somewhere",
              year="2024", tracks=14, new=5),
        _meta(1, "Budokan 白炎 Live", year="2021", mtype="ep", section="S",
              status="excluded", tracks=8, new=0, tags=["live"], reason="live"),
        _meta(2, "Mid", year="2023", mtype="single", section="S", tracks=2, new=1),
        _meta(3, "Another", year="2020", tracks=5, new=0),
    ]
    rows = artists.build_sections(_catalog(releases))
    artists.print_sections(rows, width=width)
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert len(lines) > 1
    for line in lines:
        assert artists._dwidth(line.rstrip()) <= width
    # truncated titles keep index/year/counter; counter right-aligned.
    assert "2024" in out and "9/14" in out
    assert "\x1b[" not in out  # no-color output reads fine


def test_dw_truncate_never_splits_wide():
    text = "白炎x"  # 2 + 2 + 1 display cells
    truncated = artists._dw_truncate(text, 4)
    assert truncated.endswith("…")
    assert artists._dwidth(truncated) <= 4
    assert artists._dw_truncate(text, 99) == text
    # combining accents count as zero cells
    assert artists._dwidth("cafe\u0301") == 4
    # box glyphs are one display cell each (DELL-ambiguous, not wide)
    assert artists._dwidth("\u25a0") == 1
    assert artists._dwidth("\u25a1") == 1


@pytest.mark.parametrize("width", [40, 66, 80, 100, 120, 200])
def test_renderer_columns(width: int, capsys):
    rows = artists.build_sections(_catalog(_plain_rows(7)))

    def cells_at(width: int) -> int:
        artists.print_sections(rows, width=width)
        out = capsys.readouterr().out
        body = [
            l for l in out.splitlines()
            if l.startswith("\u25a0") or l.startswith("\u25a1")
        ]
        cols = 3 if width >= 100 else 2 if width >= 66 else 1
        cw = min((width - 4) // cols, 64)
        # Count cells on the first body line: every cell except the last
        # is exactly cw + 2 display cells wide.
        bglyph = (cw + 2) * "\u00b7"
        # gaps sit at k*(cw+2)-2 .. k*(cw+2)-1; count boundaries present
        line = body[0]
        n_cells = 1
        for k in range(1, cols):
            gap = line[k * (cw + 2) - 2:k * (cw + 2)]
            if artists._dwidth(line) > k * (cw + 2) + 2 and gap == "  ":
                n_cells += 1
        return n_cells

    assert cells_at(200) == 3
    assert cells_at(120) == 3
    assert cells_at(100) == 3
    assert cells_at(80) == 2
    assert cells_at(66) == 2
    assert cells_at(40) == 1


def test_renderer_column_major_order(capsys):
    rows = artists.build_sections(_catalog(_plain_rows(7)))
    artists.print_sections(rows, width=100)  # 3 columns, 3 lines
    out = capsys.readouterr().out
    lines = [l for l in out.splitlines() if "Rel " in l]
    # numbers read downwards: first line = rows 1, 4, 7
    assert "Rel 0" in lines[0] and "Rel 3" in lines[0] and "Rel 6" in lines[0]
    assert "Rel 1" in lines[1] and "Rel 4" in lines[1]
    assert "Rel 2" in lines[2] and "Rel 5" in lines[2]


def test_renderer_right_aligned_counters(capsys):
    releases = [
        _meta(0, "One", new=2, tracks=10),
        _meta(1, "Two", new=0, tracks=14),
    ]
    rows = artists.build_sections(_catalog(releases))
    artists.print_sections(rows, width=100)  # cell_w = 32
    out = capsys.readouterr().out
    body = [l for l in out.splitlines() if "One" in l or "Two" in l][0]
    cw = min((100 - 4) // 3, 64)
    # counter '8/10' flush with the cell's right edge; fully owned gets 14/14 \u2713
    assert body[:cw].rstrip().endswith("8/10")
    assert body[cw + 2:cw + 40].rstrip().endswith("14/14 \u2713")


def test_renderer_single_track_cells(capsys):
    releases = [
        _meta(0, "Owned Single", mtype="single", section="S", new=0, tracks=1),
        _meta(1, "New Single", mtype="single", section="S", new=1, tracks=1),
    ]
    rows = artists.build_sections(_catalog(releases))
    artists.print_sections(rows, width=100)
    out = capsys.readouterr().out
    line = [l for l in out.splitlines()
            if l.startswith("\u25a0") or l.startswith("\u25a1")][0]
    new_part, owned_part = line.split("New Single 2020", 1)[1].split("Owned Single", 1)
    # owned single-track: only a check mark; new single-track: nothing
    assert owned_part.rstrip().endswith("\u2713")
    assert "\u2713" not in new_part


def test_renderer_fully_owned_dimmed(capsys, monkeypatch):
    releases = [
        _meta(0, "All Owned", new=0, tracks=14),
        _meta(1, "Has New", new=3, tracks=10),
    ]
    rows = artists.build_sections(_catalog(releases))
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    artists.print_sections(rows, width=100)
    out = capsys.readouterr().out
    # fully owned multi-track cell dimmed, selected box green
    assert "\x1b[2m\u25a1" in out and "\x1b[32m\u25a0" in out


def test_renderer_japanese_alignment(capsys):
    releases = [
        _meta(0, "白炎", year="2020", tracks=3, new=1),
        _meta(1, "AB", year="2019", tracks=3, new=1),
    ]
    rows = artists.build_sections(_catalog(releases))
    artists.print_sections(rows, width=80)  # 2 columns, cell width 38
    out = capsys.readouterr().out
    body = [l for l in out.splitlines() if l.startswith("\u25a0")]
    cw = (80 - 4) // 2
    # second cell starts at the same DISPLAY column even with wide chars in cell 1
    second = body[0].index("\u25a0", 3)
    assert artists._dwidth(body[0][:second]) == cw + 2


def test_renderer_header_pluralization(capsys):
    releases = [
        _meta(0, "One", section="S", mtype="single", new=1, tracks=2),
        _meta(1, "Two", section="S", mtype="single", new=0, tracks=3),
        _meta(2, "Three", section="A", status="excluded", new=0, tags=["live"]),
    ]
    rows = artists.build_sections(_catalog(releases))
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        artists.print_sections(rows, width=100)
    out = buf.getvalue().splitlines()
    assert out[0] == ""  # blank line before the first header
    assert out[1] == "A  Albums \u00b7 0/1 selected \u00b7 0 tracks to download"
    s_head = [l for l in out if l.startswith("S ")][0]
    assert s_head == "S  Singles & EPs \u00b7 1/2 selected \u00b7 1 track to download"
    assert "Selected: 1 releases \u00b7 1 track to download" in out


def test_renderer_notes_and_legend(capsys):
    releases = [
        _meta(0, "Good Album", new=2),
        _meta(1, "Tagged Live", section="A", status="excluded", new=4,
              tags=["live"], reason="live"),
    ]
    rows = artists.build_sections(_catalog(releases))
    artists.print_sections(rows, width=200)
    out = capsys.readouterr().out
    assert "owned/total = tracks you own / tracks in release" in out
    # the tagged release with 4 new tracks is unselected by default
    note = [l for l in out.splitlines() if "type 'all' to include them" in l][0]
    assert "1 releases with new tracks" in note
    assert "Selected: 1 releases \u00b7 2 tracks to download" in out


def test_ascii_rendering(capsys):
    releases = [_meta(0, "One", new=2, tracks=10)]
    rows = artists.build_sections(_catalog(releases))
    artists.print_sections(rows, width=100, ascii_mode=True)
    out = capsys.readouterr().out
    assert "[x]" in out and "\u25a0" not in out and "\u2713" not in out
    assert "ok = all owned" in out


def test_no_color_output_plain(capsys, monkeypatch):
    # NO_COLOR suppresses color only — bold stays (it is not a color).
    rows = artists.build_sections(_catalog(_plain_rows(2)))
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    artists.print_sections(rows, width=100)
    out = capsys.readouterr().out
    assert "\x1b[1m" in out
    assert "\x1b[32m" not in out
    assert "\x1b[2m" not in out


def test_bold_headers_with_fake_tty(capsys, monkeypatch):
    releases = [
        _meta(0, "Rel 0", year="2020", tracks=3, new=1),
        _meta(1, "Rel 1", year="2019", tracks=3, new=1,
              section="S", mtype="single"),
    ]
    rows = artists.build_sections(_catalog(releases))
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    artists.print_sections(rows, width=100)
    tty_lines = capsys.readouterr().out.splitlines()

    artists.print_sections(rows, width=100)  # non-TTY render
    plain_lines = capsys.readouterr().out.splitlines()

    # headers carry bold codes; every line keeps its visible width
    assert any(l.startswith("\x1b[1mA  ") for l in tty_lines)
    assert any(l.startswith("\x1b[1mS  ") for l in tty_lines)
    for tty, plain in zip(tty_lines, plain_lines):
        assert artists._dwidth(tty) == artists._dwidth(plain)


def test_non_tty_has_no_ansi(capsys, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    rows = artists.build_sections(_catalog(_plain_rows(2)))
    artists.print_sections(rows, width=100)
    out = capsys.readouterr().out
    assert "\x1b[" not in out


def test_dwidth_strips_ansi():
    assert artists._dwidth("\x1b[1mAB\x1b[0m") == 2
    assert artists._dwidth("\x1b[32m\u25a0\x1b[0m") == 1


def test_min_gap_year_counter(capsys):
    # A full-title cell still keeps >= 2 spaces between year and counter:
    # e.g. "…Distor… 2019  4/4 ✓".
    releases = [
        _meta(0, "Super Distor Distor Distor A Very Long Title Indeed X", year="2019",
              new=0, tracks=4),
    ]
    rows = artists.build_sections(_catalog(releases))
    artists.print_sections(rows, width=90)
    out = capsys.readouterr().out
    line = [l for l in out.splitlines() if "2019" in l][0]
    assert "2019  4/4" in line or "2019   4/4" in line
    # the truncated title ends with the ellipsis before the year
    assert "…" in line.split("2019")[0]


def test_prompt_text_and_truncation():
    from ytm_offline import cli
    assert cli._selection_prompt(200) == (
        "selection> (Enter = recommended | 'all' = everything incl. tagged"
        " | selection set = A[1-3],S[2],-S[4] | 'none' to abort)"
    )
    short = cli._selection_prompt(30)
    assert short.endswith("\u2026")
    assert artists._dwidth(short) <= 30


def test_no_tags_rendered_but_classification_kept(capsys):
    releases = [
        _meta(0, "Base", new=2),
        _meta(1, "Base (Deluxe)", section="A", status="excluded", new=0,
              tags=["live", "dup of Base"], reason="live, dup of Base"),
    ]
    rows = artists.build_sections(_catalog(releases))
    artists.print_sections(rows, width=100)
    out = capsys.readouterr().out
    assert "[live]" not in out and "dup of" not in out and "[remix]" not in out
    # classification still internal: default selection skipped the tagged row
    assert not rows["A"][1]["selected"]


def _cmd_artist_args(tmp_path):
    import argparse
    return argparse.Namespace(
        artists=["BABYMETAL"],
        from_file=None,
        pick=None,
        headers=None if False else "hdrs",
        output=str(tmp_path),
        archive=str(tmp_path / ".archive"),
        include_live=False,
        include_compilations=False,
        include_remixes=False,
        albums_only=False,
        no_singles=False,
        duration_window=False,
        scope="all",
        top_limit=None,
        max_per_album=None,
        max_size=None,
        delay=0.0,
        singles_dir="Singles",
        dry_run=True,
        yes=False,
        tui=False,
        ascii=False,
        cookies=None,
        cookies_from_browser=None,
        browser_profile=None,
    )


def _interactive_env(monkeypatch, tmp_path, releases, users):
    """Assemble a cd_artist dry environment: fake yt, single artist, fed input."""
    import ytm_offline.cli as cli

    monkeypatch.setattr("ytm_offline.cli.YTMusic", lambda *a, **k: MagicMock(name="yt"))
    monkeypatch.setattr(
        "ytm_offline.artists.search_artists",
        lambda yt, spec, limit=6: [{"name": "BABYMETAL", "browseId": "UC1"}],
    )
    monkeypatch.setattr("ytm_offline.cli.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a, **k: users.pop(0))
    released = releases + [
        _meta(1, "A Single", mtype="single", section="S", new=1, tracks=1)
    ]
    catalog = _catalog(released, _kept_entries())
    catalog["manifest_path"] = str(tmp_path / "manifest.json")
    catalog["payload"]["manifest_path"] = catalog["manifest_path"]
    monkeypatch.setattr("ytm_offline.artists.build_catalog", lambda *a, **k: catalog)
    return catalog


def test_artist_start_prompt_quit(monkeypatch, tmp_path):
    import ytm_offline.cli as cli

    releases = [_meta(0, "An Album", new=2)]
    catalog = _interactive_env(
        monkeypatch, tmp_path, releases, ["", "q"]  # selection Enter, then q
    )
    calls = []
    monkeypatch.setattr("ytm_offline.artists.download_artist",
                        lambda **kw: calls.append(kw) or {"summary": {}})
    args = _cmd_artist_args(tmp_path)
    args.dry_run = False
    args.yes = False
    assert cli.cmd_artist(args) == 0
    assert calls == []  # q quits without downloading


def test_artist_start_prompt_reselect_then_start(monkeypatch, tmp_path, capsys):
    import ytm_offline.cli as cli

    releases = [_meta(0, "An Album", new=2)]
    catalog = _interactive_env(
        monkeypatch, tmp_path, releases,
        ["", "S[1]", ""]  # default selection, re-pick the single, start
    )
    downloads = []
    monkeypatch.setattr(
        "ytm_offline.artists.download_artist",
        lambda **kw: downloads.append(kw) or {
            "summary": {"downloaded": 1, "archived": 0, "skipped_size": 0,
                        "failed": 0, "stopped": 0},
            "manifest_path": "m", "m3u8_dir": "p", "m3u8_paths": [],
        },
    )
    args = _cmd_artist_args(tmp_path)
    args.dry_run = False
    args.yes = False
    assert cli.cmd_artist(args) == 0
    assert len(downloads) == 1
    assert downloads[0]["label"] == "Artist"
    out = capsys.readouterr().out
    # initial recap counts both release entries; the reselection (S[1])
    # recomputes to the single's 1 track and re-saves the manifest.
    assert out.count("recap: 3 new tracks") == 1
    assert out.count("recap: 1 new tracks") == 1


def test_artist_yes_skips_start_prompt(monkeypatch, tmp_path):
    import ytm_offline.cli as cli

    releases = [_meta(0, "An Album", new=2)]
    catalog = _interactive_env(
        monkeypatch, tmp_path, releases,
        [""]  # only the selection prompt is answered
    )
    downloads = []
    monkeypatch.setattr(
        "ytm_offline.artists.download_artist",
        lambda **kw: downloads.append(kw) or {
            "summary": {"downloaded": 2, "archived": 0, "skipped_size": 0,
                        "failed": 0, "stopped": 0},
            "manifest_path": "m", "m3u8_dir": "p", "m3u8_paths": [],
        },
    )
    args = _cmd_artist_args(tmp_path)
    args.dry_run = False
    args.yes = True
    assert cli.cmd_artist(args) == 0
    assert len(downloads) == 1


def test_cmd_artist_interactive_scope_has_no_top_song_coverage(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
):
    # fix: pending top songs must not seed owned_keys when the picker
    # decides the download set — scope stays "albums" for the picker flow.
    import ytm_offline.cli as cli

    monkeypatch.setattr("ytm_offline.cli.YTMusic", lambda *a, **k: MagicMock(name="yt"))
    monkeypatch.setattr(
        "ytm_offline.artists.search_artists",
        lambda yt, spec, limit=6: [{"name": "BABYMETAL", "browseId": "UC1"}],
    )
    monkeypatch.setattr("ytm_offline.cli.sys.stdin.isatty", lambda: True)
    captured: dict = {}
    sentinel = RuntimeError("stop before anything real happens")

    def fake_build_catalog(headers, bid, root, archive, scope=None, **kw):
        captured["scope"] = scope
        raise sentinel

    monkeypatch.setattr(cli.artists_module, "build_catalog", fake_build_catalog)
    args = _cmd_artist_args(tmp_path)
    with pytest.raises(RuntimeError, match="stop before"):
        cli.cmd_artist(args)
    assert captured["scope"] == "albums"


def test_cmd_artist_noninteractive_keeps_scope(tmp_path, monkeypatch: pytest.MonkeyPatch):
    import ytm_offline.cli as cli

    monkeypatch.setattr("ytm_offline.cli.YTMusic", lambda *a, **k: MagicMock(name="yt"))
    monkeypatch.setattr(
        "ytm_offline.artists.search_artists",
        lambda yt, spec, limit=6: [{"name": "BABYMETAL", "browseId": "UC1"}],
    )
    monkeypatch.setattr("ytm_offline.cli.sys.stdin.isatty", lambda: False)
    captured: dict = {}
    sentinel = RuntimeError("stop")

    def fake_build_catalog(headers, bid, root, archive, scope=None, **kw):
        captured["scope"] = scope
        raise sentinel

    monkeypatch.setattr(cli.artists_module, "build_catalog", fake_build_catalog)
    with pytest.raises(RuntimeError, match="stop"):
        cli.cmd_artist(_cmd_artist_args(tmp_path))
    assert captured["scope"] == "all"
