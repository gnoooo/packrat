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
    assert "A  Albums (" in out
    assert "S  Singles & EPs (" in out
    assert "[x]  1 An Album" in out


def _plain_rows(n: int = 4) -> list[dict]:
    return [
        _meta(i, f"Rel {i}", year=str(2020 - i), tracks=3, new=i % 2)
        for i in range(n)
    ]


def test_renderer_column_major_and_width(capsys):
    rows = artists.build_sections(_catalog(_plain_rows(4)))
    artists.print_sections(rows, width=80)
    out = capsys.readouterr().out
    lines = out.splitlines()
    # cell width ~26 clamp, gap 2 -> 2 columns at width 80; column-major
    # fill: line 1 = rows 1 and 3, line 2 = rows 2 and 4.
    s_block = lines[lines.index("A  Albums (4) - 2 selected - 2 new") + 1:]
    assert "Rel 0" in s_block[0] and "Rel 2" in s_block[0]
    assert "Rel 1" in s_block[1] and "Rel 3" in s_block[1]
    for line in lines:
        assert artists._dwidth(line.rstrip()) <= 80
    assert "\x1b[" not in out  # no colors off-tty


@pytest.mark.parametrize("width", [40, 80, 120, 200])
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
    # truncated titles keep counts (counts never truncated); wide-char
    # splitting is covered by test_dw_truncate_never_splits_wide
    assert "(2024)" in out and "14t" in out
    assert "\x1b[" not in out  # no-color output reads fine


def test_dw_truncate_never_splits_wide():
    text = "白炎x"  # 2 + 2 + 1 display cells
    truncated = artists._dw_truncate(text, 4)
    assert truncated.endswith("…")
    assert artists._dwidth(truncated) <= 4
    assert artists._dw_truncate(text, 99) == text
    # combining accents count as zero cells
    assert artists._dwidth("cafe\u0301") == 4


def test_renderer_japanese_alignment(capsys):
    releases = [
        _meta(0, "白炎", year="2020", tracks=3, new=1),
        _meta(1, "AB", year="2019", tracks=3, new=1),
    ]
    rows = artists.build_sections(_catalog(releases))
    artists.print_sections(rows, width=60)
    out = capsys.readouterr().out
    for line in out.splitlines():
        assert artists._dwidth(line.rstrip()) <= 60
    # 2 columns, column-major: one line holds both cells; the second cell
    # starts at the same DISPLAY column on every line even though cell 1
    # contains two wide characters.
    body = [l for l in out.splitlines() if l.startswith("[x]")]
    assert len(body) == 1
    second = body[0].index("[x]", 3)
    assert artists._dwidth(body[0][:second]) == 28  # cell 26 + gap 2


def test_renderer_header_counts():
    releases = [
        _meta(0, "One", new=2),
        _meta(1, "Two", new=0),
        _meta(2, "Three", section="A", status="excluded", new=0, tags=["live"]),
    ]
    rows = artists.build_sections(_catalog(releases))
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        artists.print_sections(rows, width=100)
    head = buf.getvalue().splitlines()[0]
    assert head == "A  Albums (3) - 1 selected - 2 new"


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
