# Configuration

<p align="center">
  <a href="../README.md">README</a> · <a href="INSTALL.md">Install</a> · <a href="QUICKSTART.md">Quickstart</a> · <a href="COMMANDS.md">Commands</a> · <a href="AUTH.md">Auth</a> · <strong>Configuration</strong>
</p>
<p align="center">
  <a href="TUI.md">TUI</a> · <a href="DISCOVERY.md">Discovery</a> · <a href="ARTISTS.md">Artists</a> · <a href="TROUBLESHOOTING.md">Troubleshooting</a> · <a href="DEVELOPMENT.md">Development</a> · <a href="HISTORY.md">History</a>
</p>

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `PACKRAT_HEADERS` | `~/.config/packrat/browser.json` | ytmusicapi login headers. |
| `PACKRAT_COOKIES` | - | Netscape cookie jar for yt-dlp. |
| `PACKRAT_COOKIES_FROM_BROWSER` | - | Browser/profile spec. |
| `PACKRAT_OUTPUT` | `.music` | Music library root. |
| `PACKRAT_ARCHIVE` | `.packrat_archive` | Finished video IDs. |
| `PACKRAT_SINGLES_DIR` | `Singles` | Singles folder inside each artist. |
| `PACKRAT_MANIFEST_FORMAT` | `json` | `json` or `json+csv`. |
| `PACKRAT_TUI_ANIM` | enabled | `0` disables TUI animation. |
| `LASTFM_API_KEY` | - | Enables Last.fm discovery extras. |
| `NO_COLOR` | - | Disable colors. |

CLI values override environment values. Old `YTM_*` names are accepted as fallbacks for the `PACKRAT_*` names.

## Paths

| Path | Meaning |
|---|---|
| `.music/` | Default library root. |
| `.packrat_archive` | Download history. |
| `.music/playlists/*.manifest.json` | Saved plans. |
| `.music/playlists/*.m3u8` | Generated playlists. |
| `~/.config/packrat/browser.json` | Login headers. |
| `logs/packrat-*.log` | Run logs. |

Change the library per command:

```bash
packrat sync --output /srv/music
```

Or for every run:

```bash
export PACKRAT_OUTPUT=/srv/music
```

## Manifest as source of truth

For a sync, the saved manifest is authoritative: a `downloaded` entry with a valid file is skipped, a missing/corrupt 
file is retried, and the legacy archive is only a fallback when the manifest has no entry. 
The archive remains a fast index and a compatibility path.

Manifest and archive writes are atomic (`tempfile` + `os.replace` + `fsync`).

## Credentials

`packrat login` stores headers at `~/.config/packrat/browser.json` with mode `0600` and creates the config directory as `0700`. 
`packrat logout` deletes the file. Browser cookie jars extracted by Packrat are temporary `0600` files and are removed at exit. 
Logs redact cookies, authorization headers and Google session cookies.

## Archive

The archive prevents re-downloading finished tracks. 
If you delete it but the output folder already contains audio, Packrat warns and asks before 
starting a new history, even with `--yes`. `--force-new-archive` skips that check.

Back up `.packrat_archive` together with `.music/`.

## Download pacing

- `--delay 7` is the safe default between tracks.
- `--delay 0` is faster but may trigger YouTube rate limits.
- `--max-size 3GB` skips oversized tracks.
- `--limit N` caps new downloads.

## Manifests

`--manifest-format json` writes a JSON plan. `json+csv` writes JSON plus a CSV sidecar for spreadsheets. 
A plan is written before downloads and updated as tracks finish, so `download-manifest` can resume it.

## TUI configuration

```bash
packrat --tui --theme sunset sync
packrat --tui --palette fixed sync
packrat --tui --no-anim --ascii sync
```

See [TUI](TUI.md).

## Legacy compatibility

- Old command `ytm-offline` still invokes Packrat.
- Old `YTM_*` environment variables are fallbacks.
- Old config path is read if the new one is absent.
- Existing `.ytm_offline_archive` is used if `.packrat_archive` is absent.
