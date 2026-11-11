# TUI details

The curses UI is optional. Use `--tui` on `sync`, `download-manifest`,
`artist`, `discover queues`, `discover albums`, or `discover radio`.

Keys: `p` pause/resume, `s` skip current track, `q` quit after the current
track, `l` toggle the log panel (height ≥ 30 rows), `a` toggle animations,
`t` cycle theme, `?` show help, Up/Down and PgUp/PgDn scroll the log.

## Layout

Full-width header band (`♪ ytm-offline › <mode>` + status badge + elapsed),
overall panel with a gradient bar and a braille throughput graph, a now-playing
window, a status panel with chips, a failures panel when needed, and a key-chip
footer. Content is capped at 140 columns and centered.

All panels are complete rounded frames. Strings are sanitized before curses:
control characters become spaces, runs of spaces collapse, and leading/trailing
spaces are stripped. The drawn area is recomputed every render and on resize.

## Themes and palettes

Four themes: `ocean` (default), `sunset`, `matrix`, `mono`.

`--palette`:
- `auto` — best tier the terminal supports.
- `custom` — exact RGB via `init_color` (needs 256-color curses and
  `can_change_color()`).
- `fixed` — nearest xterm-256 color.
- `basic` — plain attributes.

The terminal palette is restored on exit and on a UI crash. For tmux use
`tmux-256color` and `set -g default-terminal "tmux-256color"` in
`~/.tmux.conf`.

`--no-anim` disables waves/spinner (`YTM_TUI_ANIM=0`). `--no-color` is
monochrome (`NO_COLOR`). `--ascii` uses ASCII-only glyphs.

`ytm-offline ui glyphs` prints the glyph groups, a theme gradient strip and the
selected palette tier.

## Artist picker layout

When stdout is a TTY, `artist` lists releases in two sections that mirror the
YouTube Music artist page.

- 3 columns at width ≥ 100, 2 at 66–99, 1 below.
- Cell: `■/□ <index> <title…> <year> <owned/total>`.
- Only the title is truncated with `…`.
- Fully owned multi-track releases show `14/14 ✓` and are dimmed.
- Single-track releases show `✓` when owned and nothing when new.
- Green `■` selected, dim `□` otherwise; only on a TTY with `NO_COLOR` unset.
- `--ascii` renders `[x]`/`[ ]` and `ok`/`|`.
- Tagged releases stay numbered, unselected by default; `all` includes them.
- The prompt is truncated with `…` only when the terminal is narrower than the
  text.

The listing is display only. Classification, counts and default selection stay
in `artists.py`.
