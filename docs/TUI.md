# TUI details

The curses UI is optional. Use `--tui` on `sync`, `download-manifest`,
`artist`, `discover queues`, `discover albums`, or `discover radio`.

Keys: `p` pause/resume, `s` skip current track, `q` quit after the current
track, `l` toggle the log panel (height ≥ 30 rows), `a` toggle animations,
`t` cycle theme, `?` show help, Up/Down and PgUp/PgDn scroll the log.

## Layout

A rounded outer frame surrounds the whole TUI. Inside it, a foreground-only header
(`♪ packrat › <mode>` + status + elapsed), a centered stack of rounded
cards (OVERALL, NOW PLAYING, STATUS, RECENT FAILURES, LOG), and a
foreground-only key footer. Header and footer use no background colour.
Content is capped at 200 columns inside the frame.

The overall bar is a full-width HSL gradient: each tile advances hue by one
degree, saturation fades toward the theme highlight, and lightness rises on a
square-root curve so the head is visible early. With `init_color` support the
palette uses 100 exact HSL steps (kept under tmux's 256-pair limit);
otherwise it falls back to 64 xterm-256
steps. Filled tiles are full blocks using the gradient as foreground; the block
covers the cell, so the active head is a solid gradient tile. The unfilled
track is a quarter-height (`▂`) foreground gradient. Fill and track share the
same full-width gradient mapping, so colours line up at the boundary and the
head is never forced to white. OVERALL and NOW PLAYING use exactly the same
renderer. The log card is shown by default on terminals
at least 30 rows tall and is sized to its content; `l` hides it. On shorter
terminals the cards are centered vertically.

The current track is shown as `artist@ARTIST#ALBUM` (the album suffix is
omitted when there is no album). Internal source ids such as `artist:X` or
`sync:liked` are displayed with the same `@` form; a source equal to the
current artist is not repeated.

All panels are complete rounded frames. Strings are sanitized before curses:
control characters become spaces, runs of spaces collapse, and leading/trailing
spaces are stripped. The drawn area is recomputed every render and on resize.

## Themes and palettes

Four themes: `ocean` (default), `sunset`, `matrix`, `mono`.

`--palette`:
- `auto` — exact-RGB smooth palette when supported; inside tmux this uses
  OSC 4 palette slots (tmux 3.7+), else nearest xterm-256.
- `fixed` — force nearest xterm-256 (disable exact palette changes).
- `basic` — plain attributes.

For tmux use `tmux-256color` and `set -g default-terminal "tmux-256color"` in
`~/.tmux.conf`.

`--no-anim` disables the remaining status animations (`PACKRAT_TUI_ANIM=0`). `--no-color` is
monochrome (`NO_COLOR`). `--ascii` uses ASCII-only glyphs.



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
