# ytm-offline

Download music from your YouTube Music account for offline use.

## Phase 1 status

Project skeleton and auth validation are in place. The next gate is downloading one track from your own library with your cookies + Deno ≥2.3.0 before building the rest.

## Setup

1. Install a supported JS runtime (required for real batch downloads):
   - Deno ≥2.3.0 (recommended)
   - Node ≥22.0.0 also works, but the current gate checks for Deno.
2. Create a ytmusicapi browser-headers auth file:
   ```bash
   python -m ytmusicapi browser
   ```
   This writes `headers_auth.json`.
3. Export a Netscape `cookies.txt` from the same browser profile, or use `--cookies-from-browser firefox`.
4. Install the package:
   ```bash
   pip install -e .
   ```

## Environment variables

- `YTM_HEADERS` — path to the ytmusicapi browser-headers JSON file (defaults to `~/.config/ytmusic-composer/browser.json` when it exists).
- `YTM_COOKIES` — path to the Netscape cookies.txt file for yt-dlp.

These can be overridden with `--headers` and `--cookies`.

## Quick start

Validate auth (`--headers` defaults to `~/.config/ytmusic-composer/browser.json` when it exists, or set the `YTM_HEADERS` env var):
```bash
ytm-offline auth
```

List library playlists:
```bash
ytm-offline --headers ~/.config/ytmusic-composer/browser.json library
```

Download and verify one track:
```bash
ytm-offline \
  --headers ~/.config/ytmusic-composer/browser.json \
  --cookies ~/.config/ytmusic-composer/cookies.txt \
  download-one <video_id>
```

## TUI

Add `--tui` to `sync`, `retry`, `discover-albums --download`, or `discover-queues --download` for a curses interface:
```bash
ytm-offline --headers ~/.config/ytmusic-composer/browser.json --tui sync --source liked
```

Keys in the TUI:
- `p` pause / resume
- `s` skip current track
- `q` quit after the current track
- `l` toggle log panel
- `a` toggle animations
- `t` cycle color theme
- `?` show key help
- Up/Down / PgUp/PgDn scroll the log

### Themes and visual flags

Four color themes (default `ocean`): `ocean` (cyan→white), `sunset` (orange→white), `matrix` (green→white), `mono` (gray→white). Every subcommand accepts:

- `--theme <name>` — pick the theme (visible only with a 256-color `$TERM`).
- `--no-anim` — static colors, no waves/spinner (env: `YTM_TUI_ANIM=0`).
- `--no-color` — monochrome, characters only (env: `NO_COLOR`).
- `--ascii` — ASCII-only glyphs (bars, box borders, spinner).

The animated renderer needs a 256-color terminal. Under tmux, use `tmux-256color` (`set -g default-terminal "tmux-256color"` in `~/.tmux.conf`).

## Artist picker

```bash
ytm-offline artist "BABYMETAL" --dry-run
```

When stdout is a TTY the artist command lists the discography in two sections
that mirror the YouTube Music artist page and waits for a selection. The
listing is DISPLAY only — classification stays internal and the grammar,
counts and default selection are what they were.

```
subscribers: 3.7M

A  Albums · 7/9 selected · 57 tracks to download
■  1 METAL FORTH 2025               3/10   □  3 10 BABYMETAL BUDOKAN 2021     0/13
□  2 THE OTHER ONE 2023            14/14 ✓

S  Singles & EPs · 1/22 selected · 1 track to download
□  1 from me to u 2025                  ✓

owned/total = tracks you own / tracks in release   ✓ = all owned   ■ selected   □ not selected
1 releases with new tracks are unselected by default (live / remix / alternate versions): type 'all' to include them
Selected: 1 releases · 7 tracks to download
selection> (Enter = recommended | 'all' = everything incl. tagged | selection set = A[1-3],S[2],-S[4] | 'none' to abort)
```

### Layout

- **Blank lines**: one before every section header (also the first, right
  after the `subscribers:` line) and one before the legend; nothing else
  between legend, the note and the `Selected:` total.
- **Headers are bold** (`\x1b[1m…\x1b[0m`) on a TTY — bold is not a color, so
  it stays on with `NO_COLOR` or `TERM=dumb` gets plain text; piped output
  has no escape codes at all. Width measuring ignores ANSI codes.
- **Columns**: 3 at terminal width >= 100, 2 at 66–99, 1 below; cell width =
  `(width − 2·gap) / columns` (gap 2) capped at 64 per cell. Column-major
  (numbers read downwards); the width is re-read on every render.
- **Cell**: `■/□ <index right-aligned> <title…> <year> <counter right-aligned>`.
  The counter is `owned/total` (`3/10`); a fully owned multi-track release
  shows `14/14 ✓` and its whole cell dimmed; a single-track release shows only
  `✓` when owned and nothing when new. Only the TITLE is ever truncated with
  `…` (index, box, year and counter never are); at least two spaces separate
  the year from the counter.
- **Colors**: green `■` when selected, dim `□` otherwise; only on a TTY with
  `NO_COLOR` unset. With `--ascii` (or a non-UTF-8 terminal) the boxes render
  as `[x]`/`[ ]` and `✓`/`·` become `ok`/`|`.
- **Prompt**: `selection> (Enter = recommended | 'all' = everything incl.
  tagged | selection set = A[1-3],S[2],-S[4] | 'none' to abort)` — one line
  under the cursor, truncated with `…` only when the terminal is narrower
  than the text.
- **Sections**: `A` = the page's albums section, `S` = "Singles & EPs"
  (EPs live in S but download into the album folder; only `Single` releases
  go to `music/<Artist>/Singles/`). Numbering is per section from 1, newest
  year first, then title. Headers read
  `S  Singles & EPs · 1/22 selected · 1 track to download`.
- Tags (live/remix/alternate versions) are **not** shown in the cells
  anymore; releases that would have been dropped (live, remix, edition
  duplicates) stay numbered, unselected by default, and the note line under
  the listing tells you they exist (`type 'all' to include them`).
- Tracklists of all releases are resolved up front so counters are exact;
  you'll see a `resolving releases 12/37` progress line.
- Under the listing: a legend line, the "unselected by default" note (only
  when it applies) and the current selection total, each truncated to the
  terminal width.

Selection grammar (letters are case-insensitive):

- `Enter` — recommended (unselected-but-new releases all in)
- `all` — everything including the tagged releases
- `A[1-3]`, `A[1,3]`, `A[3-]`, `S[1-10]` — ranges/lists per section
- `A[*]`, `S`, `A` — whole section
- `-S[4]` — exclude; combine freely: `A[1-3],S[2],-S[4]`
- `none` / `q` — abort without touching the manifest

The recap, manifest and download totals always come from one counting
function (`summarize_entries`/`owned_key`). The `--include-live`,
`--include-remixes`, `--include-compilations`, `--albums-only` and
`--no-singles` flags no longer remove releases from the picker; the
differing release still gets its own number and the choice is yours.


### Try it without downloading

```bash
ytm-offline tui-demo --seconds 60
```

Simulates a ~40-track batch (with a pause/resume window, failures and a final DONE) through the same state/event path. No network, no files, no yt-dlp. Quit with `q`.

If stdout is not a TTY or the terminal is too small, the tool falls back to plain logging.

## Auth note

`ytmusicapi` and `yt-dlp` need different credential shapes:
- `ytmusicapi` uses the JSON headers file.
- `yt-dlp` uses a Netscape `cookies.txt` or `--cookies-from-browser`.

Pass the latter with `--cookies` or `--cookies-from-browser`. Never commit auth files; they are gitignored.
