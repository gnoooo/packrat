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

- `YTM_HEADERS` — path to the ytmusicapi browser-headers JSON file.
- `YTM_COOKIES` — path to the Netscape cookies.txt file for yt-dlp.

These can be overridden with `--headers` and `--cookies`.

## Quick start

Validate auth:
```bash
ytm-offline --headers ~/.config/ytmusic-composer/browser.json auth
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
- `?` show key help
- Up/Down / PgUp/PgDn scroll the log

If stdout is not a TTY or the terminal is too small, the tool falls back to plain logging. Use `--ascii` for ASCII progress bars.

## Auth note

`ytmusicapi` and `yt-dlp` need different credential shapes:
- `ytmusicapi` uses the JSON headers file.
- `yt-dlp` uses a Netscape `cookies.txt` or `--cookies-from-browser`.

Pass the latter with `--cookies` or `--cookies-from-browser`. Never commit auth files; they are gitignored.
