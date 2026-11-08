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
that mirror the YouTube Music artist page and waits for a selection:

```
A  Albums (9) - 7 selected - 59 new
   [x]  1 METAL FORTH (2025) 10t +10  ...
S  Singles & EPs (13) - 6 selected - 7 new
```

- **A** = the page's albums section, **S** = its "Singles & EPs" section
  (EPs live here, marked `EP`, but download into the album folder; only
  `Single` releases go to `music/<Artist>/Singles/`).
- Every release is shown and numbered, newest year first, then title.
  Releases that older runs would have dropped are kept visible with a tag
  at the end of the line — `[live]`, `[remix]`, `[dup of <title>]`, other
  filter-rule tags — and are **not** selected by default. Releases with 0
  new tracks are dimmed and unselected too.
- Tracklists of all releases are resolved up front so counts are exact;
  you'll see a `resolving releases 12/37` progress line.

Selection grammar (letters are case-insensitive):

- `Enter` — recommended (unselected-but-new releases all in)
- `all` — everything including tagged releases
- `A[1-3]`, `A[1,3]`, `A[3-]`, `S[1-10]` — ranges/lists per section
- `A[*]`, `S`, `A` — whole section
- `-S[4]` — exclude; combine freely: `A[1-3],S[2],-S[4]`
- `none` / `q` — abort without touching the manifest

Colors are used only when stdout is a TTY and `NO_COLOR` is unset; the
plain output reads fine without them. The recap, manifest and download
totals always come from one counting function. The `--include-live`,
`--include-remixes`, `--include-compilations`, `--albums-only` and
`--no-singles` flags no longer remove releases from the picker; they just
set the visible tags and leave the choice to you.


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
