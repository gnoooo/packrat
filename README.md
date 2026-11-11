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
- `YTM_COOKIES_FROM_BROWSER` — browser/profile spec for yt-dlp, e.g. `firefox` or `firefox:default-release`.
- `YTM_OUTPUT` — default output root (default: `music`).
- `YTM_ARCHIVE` — default download archive (default: `.ytm_offline_archive`).
- `YTM_SINGLES_DIR` — singles subfolder inside each artist folder (default: `Singles`).
- `YTM_MANIFEST_FORMAT` — `json` (default) or `json+csv` (JSON source of truth plus a CSV sidecar).
- `YTM_TUI_ANIM=0` — disable TUI animations.
- `NO_COLOR` — disable colors.

CLI values override environment values. `--cookies` and `--cookies-from-browser` are mutually exclusive on the CLI; a CLI value of one kind overrides an environment value of the other kind. API keys (Last.fm) are read only from the environment, never from the command line.

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

Add `--tui` to `sync`, `download-manifest`, `discover albums`, or `discover queues` for a curses interface:
```bash
ytm-offline --headers ~/.config/ytmusic-composer/browser.json --tui sync --source liked
```

Keys in the TUI:
- `p` pause / resume
- `s` skip current track
- `q` quit after the current track
- `l` toggle log panel (tall terminals, height ≥ 30 rows)
- `a` toggle animations
- `t` cycle color theme
- `?` show key help
- Up/Down / PgUp/PgDn scroll the log

The layout: full-width header band (`♪ ytm-offline › <mode>` + colored status badge + `⏱` elapsed), the overall panel with a space-background gradient bar (≤ 100 cells) and a braille throughput graph of the last 60 s (flat `⣀` baseline when there is no data yet), a framed now-playing window (finished + next track while waiting between tracks), a status panel with chips (`✓ downloaded`, `○ archived`, `✗ failed`, `↻ retries`, error gauge `●○○ c/3`), a failures panel only when something failed, and a key-chip footer band. Content is capped at 140 columns and centered.

Rendering details: all panels are complete rounded frames — content rows are `│` + padded text + `│` to the exact inner width (bar rows keep a gap before the right border instead of full padding); the help overlay is a plain list, not a framed panel. All strings are sanitized before they reach curses (control characters like `\n`/`\r`/`\t` become a space, runs of spaces collapse, leading/trailing whitespace is stripped), so odd titles can't blank lines. The overall panel shows the wave bar + percent on line 1 and the single `done/total tracks · bytes · ETA · speed` line + graph on line 2. Panels sit between the header and footer with one blank row between each. Vertically they are centered by the spec: `body_height = sum(panel heights) + gaps`, `free = (rows − 2) − body_height − 2`, and 40% of `free` goes above the body (the rest below, never negative) on terminals with ≥ 30 rows; no centering when the log panel is visible or below 30 rows (compact, panels packed). The drawn area is recomputed every render and on resize (`erase()` first, so no stale cells survive).

### Themes and visual flags

Four color themes (default `ocean`): `ocean` (deep blue → cyan → white), `sunset` (purple → red → gold → cream), `matrix` (dark → green → white), `mono` (gray → white). Every subcommand accepts:

- `--theme <name>` — pick the theme.
- `--palette auto|custom|fixed|basic` (default `auto`) — `custom` redefines exact RGB colors via `init_color` (needs 256-color curses + `can_change_color()`); `fixed` maps every gradient step to the nearest xterm-256 color (perceptual redmean distance, cube + grays); `basic` is the plain-attr fallback. Auto picks the best tier your terminal supports; the terminal palette is restored on exit (also on a UI crash), including via tmux (`tmux-256color`, `set -g default-terminal "tmux-256color"` in `~/.tmux.conf`).
- `--no-anim` — static colors, no waves/spinner (env: `YTM_TUI_ANIM=0`).
- `--no-color` — monochrome, characters only (env: `NO_COLOR`).
- `--ascii` — ASCII-only glyphs (bars, box borders, spinner).

`ytm-offline ui glyphs` prints the glyph groups (block/box/braille/shapes/marks), a theme gradient strip and which palette tier was selected.

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

After each recap the run waits on
`Enter = start | type another selection | q = quit`: Enter starts the
download (with `--tui` in the curses UI), a new selection re-picks for
that artist, `q` quits without downloading. `--yes` skips both prompts.

The recap, manifest and download totals always come from one counting
function (`summarize_entries`/`owned_key`). The `--include-live`,
`--include-remixes`, `--include-compilations`, `--albums-only` and
`--no-singles` flags no longer remove releases from the picker; the
differing release still gets its own number and the choice is yours. They are
now hidden compatibility aliases; use `--select` for non-interactive selection.


### Try it without downloading

```bash
ytm-offline ui demo --seconds 60
```

Simulates a ~40-track batch (with a pause/resume window, failures and a final DONE) through the same state/event path. No network, no files, no yt-dlp. Quit with `q`.

If stdout is not a TTY or the terminal is too small, the tool falls back to plain logging.

## CLI reference

```text
ytm-offline [global options] <command> [command options]

Commands:
  auth                  validate ytmusicapi auth
  library               list library playlists (--top N)
  download-one VIDEO    download and verify one track
  sync --source SOURCE  sync liked songs or a playlist
  download-manifest     resume a manifest; --failed-only retries failures
  artist                one artist (or --from-file / --also)
  discover sections     Home/Charts/Moods/New Releases
  discover taste        taste-adjacent discovery
  discover queues       playback-queue playlists
  discover albums       whole albums from top liked artists
  discover radio SEED   auto radio from one track
  ui demo               TUI simulator
  ui glyphs             print glyphs/theme/palette
```

Global options work before or after the subcommand. Run-control options are
`--output/-o`, `--archive`, `--delay`, `--max-size`, `--limit`, `--dry-run/-n`,
`--yes/-y`, `--tui`, and `--ascii`.

`--limit N` is the maximum number of **new** tracks downloaded in one run. It is
applied after archive dedupe and selection, uniformly across `sync`, `artist`,
`download-manifest`, and `discover {sections,queues,albums,radio}`. `library`
uses `--top N` for its display cap.

`--cookies-from-browser` follows yt-dlp's syntax:
`BROWSER[+KEYRING][:PROFILE][::CONTAINER]`, e.g.
`firefox:default-release` or `firefox:/abs/path/to/profile`.

Every download command prints the resolved absolute `output:` and `archive:`
paths. If the archive does not exist but the output folder already contains
audio, the command warns and asks for confirmation even with `--yes`; the
hidden `--force-new-archive` skips that check.

### Examples

```bash
ytm-offline auth
ytm-offline library --top 10
ytm-offline sync --source liked --limit 5 --dry-run
ytm-offline sync --source liked --limit 20 --yes
ytm-offline download-manifest --manifest music/playlists/liked.manifest.json --failed-only
ytm-offline artist "BABYMETAL" --select "A[1-3],S[*]" --yes
ytm-offline artist --from-file artists.txt --also "Another Artist"
ytm-offline discover sections --dry-run
ytm-offline discover radio <video-id> --dry-run
ytm-offline ui glyphs
```

### Deprecated aliases and removal target

These keep working with one stderr deprecation line. Removal target: **v0.2.0**.

| Alias | Replacement |
|-------|-------------|
| `retry` | `download-manifest --failed-only` |
| `discover-phase4` | `discover taste` |
| `discover-queues` | `discover queues` |
| `discover-albums` | `discover albums` |
| `radio` | `discover radio` |
| `tui-demo` | `ui demo` |
| `tui-glyphs` | `ui glyphs` |
| `--pick` | `--candidate` |
| `--top-songs` | (removed; top-songs mode is not reachable through the picker) |
| `--quota-deep` etc. | `discover taste --deep` etc. |
| `--per-queue-limit` | `--per-queue` |
| `--include-phase3` | `--no-phase3` |
| `--browser-profile P` | `--cookies-from-browser BROWSER:P` |
| `--singles-dir` | `YTM_SINGLES_DIR` |
| `--manifest-format` | `YTM_MANIFEST_FORMAT` |
| `--download` | default behaviour; use `--dry-run` to plan only |
| `--total-cap` | `--limit` on `discover queues` |
| `--include-live`, `--include-compilations`, `--include-remixes`, `--albums-only`, `--no-singles`, `--duration-window` | hidden compatibility aliases; prefer `--select` / `--min-duration` / `--max-duration` |

Removed without alias: `--scope`, `--top-limit`, `--max-per-album` (the
top-songs path is unreachable through the A/S picker).

## Auth note

`ytmusicapi` and `yt-dlp` need different credential shapes:
- `ytmusicapi` uses the JSON headers file.
- `yt-dlp` uses a Netscape `cookies.txt` or `--cookies-from-browser`.

Pass the latter with `--cookies` or `--cookies-from-browser`. Never commit auth files; they are gitignored.
