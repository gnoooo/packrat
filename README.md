# Packrat

Download music from your YouTube Music account for offline use.

It keeps a local `.music/` library, a download archive, JSON manifests and
`.m3u8` playlists. Discovery commands can plan a run first; `download-manifest`
executes it later.

## Setup

1. Install the package:
   ```bash
   pip install -e .
   ```
2. Create your login file:
   ```bash
   packrat login
   ```
   The command explains how to copy the request headers from a logged-in
   `music.youtube.com` browser tab and saves them to
   `~/.config/packrat/browser.json`.
   If you already have a headers file, point to it with
   `--headers /path/to/browser.json` or `PACKRAT_HEADERS`.
3. Deno ≥2.3.0 is recommended. The downloader passes it to yt-dlp as the
   JavaScript runtime for YouTube extraction; without it, some songs may fail.
4. Cookies are optional for public songs and needed for private or
   account-only songs. Use your browser directly:
   ```bash
   export PACKRAT_COOKIES_FROM_BROWSER=firefox
   ```
   or export a `cookies.txt` and set `PACKRAT_COOKIES`.

## Quick start

```bash
packrat login                       # first time only
packrat auth                        # check the login
packrat sync --limit 5 --dry-run    # show what would download
packrat sync --limit 20             # download 20 new Liked Songs
```

`sync` uses your Liked Songs unless you pass a playlist link or
`--source playlist:<link or ID>`.

## Commands

| Command | What it does |
|---------|--------------|
| `login` | create your YouTube Music login file (first time) |
| `auth` | check that your YouTube Music login works |
| `library` | list your playlists (`--top N`) |
| `download-one LINK_OR_ID` | download one song from a link or video ID |
| `sync` | download new songs from Liked Songs (or `--source <playlist link>`) |
| `download-manifest` (`resume`) | finish or retry downloads from a saved plan (defaults to the newest plan); `--failed-only` retries failures |
| `artist` | download an artist's albums and singles, plus `--also` / `--from-file` |
| `discover sections` | popular music from Home / Charts / Moods / New Releases |
| `discover taste` | music similar to what you like (plan only) |
| `discover queues` | playlists based on YouTube Music radio queues |
| `discover albums` | whole albums from artists you like |
| `discover radio SEED` | a radio station based on one song |

Deno is recommended for `download-one` (some songs need it); cookies are
optional for public songs. Browser cookies are needed for private or
account-only songs.

## Environment variables

- `PACKRAT_HEADERS` — YouTube Music login file (created by `packrat login`).
- `PACKRAT_COOKIES` — Netscape `cookies.txt` for yt-dlp.
- `PACKRAT_COOKIES_FROM_BROWSER` — browser/profile spec, e.g. `firefox:default-release`.
- `PACKRAT_OUTPUT` — output root (default: `.music`).
- `PACKRAT_ARCHIVE` — download archive (default: `.packrat_archive`).
- `PACKRAT_SINGLES_DIR` — singles folder inside each artist folder (default: `Singles`).
- `PACKRAT_MANIFEST_FORMAT` — `json` (default) or `json+csv` (JSON plus a CSV sidecar).
- `PACKRAT_TUI_ANIM=0` — disable TUI animations.
- `LASTFM_API_KEY` — enable Last.fm tags/similar artists for `discover taste`.
- `NO_COLOR` — disable colors.

Old `YTM_*` names are still accepted as fallbacks for the new `PACKRAT_*` names.

CLI values override environment values. `--cookies` and
`--cookies-from-browser` are mutually exclusive on the CLI. API keys
(Last.fm) are read only from the environment.

## Common options

`--help` groups each command's options into **Common options**, **First-time
setup**, and **Advanced**, so the everyday flags are listed first.

These work before or after the subcommand, where the command uses them:

- `--output/-o DIR` — folder where songs are saved.
- `--archive FILE` — download history file, so finished songs are not downloaded again.
- `--delay SEC` — seconds to wait between songs.
- `--max-size SIZE` — skip songs bigger than SIZE, e.g. `3GB`.
- `--limit N` — download at most N new songs this run.
- `--dry-run/-n` — show what would be downloaded, without downloading.
- `--yes/-y` — don't ask before downloading.
- `--tui` — show the full-screen progress view.

`--cookies-from-browser` follows yt-dlp syntax:
`BROWSER[+KEYRING][:PROFILE][::CONTAINER]`, e.g. `firefox:default-release`.

The default library folder is `.music/`. Change it per command with
`--output/-o DIR`, or for every run with `PACKRAT_OUTPUT=DIR`. Existing libraries
created with the old default can be used with `--output music` (or by moving
the folder to `.music`). `.music/` is ignored by git.

Every batch download command prints the resolved absolute output folder and
download history paths. If the archive does not exist but the output folder already
contains audio, it warns and asks even with `--yes`; hidden
`--force-new-archive` skips that check.

## Examples

```bash
packrat auth
packrat library --top 10
packrat sync --limit 5 --dry-run
packrat sync --limit 20
packrat download-one "https://music.youtube.com/watch?v=..."
packrat download-manifest --manifest .music/playlists/liked.manifest.json --failed-only
packrat artist "BABYMETAL" --select "A[1-3],S[*]" --yes
packrat artist --from-file artists.txt --also "Another Artist"
packrat discover sections --sources home,charts --dry-run
packrat discover radio <link-or-id> --dry-run
```

## TUI

Add `--tui` to `sync`, `download-manifest`, `artist`, `discover queues`,
`discover albums`, or `discover radio`:

```bash
packrat --tui sync --source liked
```

Keys: `p` pause/resume, `s` skip, `q` quit, `l` hide/show the log, `a`
animations, `t` theme, `?` help. The now-playing card uses
`artist@ARTIST#ALBUM`; the log is shown by default on terminals at least 30
rows tall. See [docs/TUI.md](docs/TUI.md) for rendering details.

Visual flags on TUI commands: `--theme ocean|sunset|matrix|mono`,
`--palette auto|fixed|basic`, `--no-anim`, `--no-color`, `--ascii`.

## Artist picker

`artist` shows the discography as two sections, `A` (Albums) and `S`
(Singles & EPs), and waits for a selection. `--select` skips the picker:

```bash
packrat artist "BABYMETAL" --select "A[1-3],S[*]" --yes
```

Grammar: `Enter` recommended, `all` everything, `A[1-3]`, `S[2]`, `-S[4]`,
`none`/`q` abort. `--candidate N` chooses the Nth search result when a name is
ambiguous. `--yes` skips the start prompt.

## Deprecated aliases

Command aliases keep working with one stderr line. Removal target: **v0.3.0**.

| Alias | Replacement |
|-------|-------------|
| `retry` | `download-manifest --failed-only` |
| `discover-phase4` | `discover taste` |
| `discover-queues` | `discover queues` |
| `discover-albums` | `discover albums` |
| `radio` | `discover radio` |

Option aliases were removed in v0.2.0. Use the primary names shown in
`--help` (`--candidate`, `--sources`, `--per-album`, `--per-queue`,
`--top-artists`, `--skip-previous`, `--cookies-from-browser BROWSER:PROFILE`,
`PACKRAT_SINGLES_DIR`, `PACKRAT_MANIFEST_FORMAT`).

## Auth note

`ytmusicapi` uses the JSON headers file. `yt-dlp` uses a Netscape
`cookies.txt` or `--cookies-from-browser`. If you do not pass either, the app
infers the browser from the headers file's User-Agent and reuses that browser's
cookie jar once per run; this is what lets age-restricted tracks download.
Never commit auth files; they are gitignored.
