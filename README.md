# ytm-offline

Download music from your YouTube Music account for offline use.

It keeps a local `music/` library, a download archive, JSON manifests and
`.m3u8` playlists. Discovery commands can plan a run first; `download-manifest`
executes it later.

## Setup

1. Install a JavaScript runtime. Deno ≥2.3.0 is required for `download-one`;
   batch extraction can also use Node ≥22.
2. Create a ytmusicapi browser-headers file **at the path the CLI reads**:
   ```bash
   ytmusicapi browser --file ~/.config/ytmusic-composer/browser.json
   ```
   Or write it anywhere and set `YTM_HEADERS=/path/to/file.json`.
3. For yt-dlp, either export a Netscape `cookies.txt` or use a browser:
   ```bash
   export YTM_COOKIES_FROM_BROWSER=firefox
   ```
4. Install the package:
   ```bash
   pip install -e .
   ```

## Quick start

```bash
ytm-offline auth
ytm-offline sync --source liked --limit 5 --dry-run
ytm-offline sync --source liked --limit 20
```

## Commands

| Command | What it does |
|---------|--------------|
| `auth` | validate the ytmusicapi headers |
| `library` | list library playlists (`--top N`) |
| `download-one VIDEO` | download and verify one track |
| `sync --source SOURCE` | sync `liked` or `playlist:<id/url>` |
| `download-manifest` | resume a manifest; `--failed-only` retries failures |
| `artist` | one artist, plus `--also` / `--from-file` |
| `discover sections` | Home / Charts / Moods / New Releases |
| `discover taste` | plan taste-adjacent tracks (no download) |
| `discover queues` | playback-queue playlists |
| `discover albums` | whole albums from top liked artists |
| `discover radio SEED` | auto radio from one track |
| `ui demo` | TUI simulator |
| `ui glyphs` | print glyphs, theme and palette |

`migrate-layout` is a hidden maintenance command with `--dry-run`, `--yes`,
`--undo`.

## Environment variables

- `YTM_HEADERS` — ytmusicapi browser-headers JSON.
- `YTM_COOKIES` — Netscape `cookies.txt` for yt-dlp.
- `YTM_COOKIES_FROM_BROWSER` — browser/profile spec, e.g. `firefox:default-release`.
- `YTM_OUTPUT` — output root (default: `music`).
- `YTM_ARCHIVE` — download archive (default: `.ytm_offline_archive`).
- `YTM_SINGLES_DIR` — singles folder inside each artist folder (default: `Singles`).
- `YTM_MANIFEST_FORMAT` — `json` (default) or `json+csv` (JSON plus a CSV sidecar).
- `YTM_TUI_ANIM=0` — disable TUI animations.
- `NO_COLOR` — disable colors.

CLI values override environment values. `--cookies` and
`--cookies-from-browser` are mutually exclusive on the CLI. API keys
(Last.fm) are read only from the environment.

## Common options

These work before or after the subcommand, where the command uses them:

- `--output/-o DIR` — output root.
- `--archive FILE` — download archive.
- `--delay SEC` — seconds between downloads.
- `--max-size SIZE` — skip larger tracks, e.g. `3GB`.
- `--limit N` — max **new** tracks downloaded this run, after dedupe and selection.
- `--dry-run/-n` — plan only; write the manifest, download nothing.
- `--yes/-y` — skip confirmation prompts.
- `--tui` — curses UI on commands that support it.

`--cookies-from-browser` follows yt-dlp syntax:
`BROWSER[+KEYRING][:PROFILE][::CONTAINER]`, e.g. `firefox:default-release`.

Every download command prints the resolved absolute `output:` and `archive:`
paths. If the archive does not exist but the output folder already contains
audio, it warns and asks even with `--yes`; hidden `--force-new-archive` skips
that check.

## Examples

```bash
ytm-offline auth
ytm-offline library --top 10
ytm-offline sync --source liked --limit 5 --dry-run
ytm-offline download-manifest --manifest music/playlists/liked.manifest.json --failed-only
ytm-offline artist "BABYMETAL" --select "A[1-3],S[*]" --yes
ytm-offline artist --from-file artists.txt --also "Another Artist"
ytm-offline discover sections --sources home,charts --dry-run
ytm-offline discover radio <video-id> --dry-run
ytm-offline ui glyphs
```

## TUI

Add `--tui` to `sync`, `download-manifest`, `artist`, `discover queues`,
`discover albums`, or `discover radio`:

```bash
ytm-offline --tui sync --source liked
```

Keys: `p` pause/resume, `s` skip, `q` quit, `l` log panel, `a` animations,
`t` theme, `?` help. See [docs/TUI.md](docs/TUI.md) for rendering details.

Visual flags on TUI commands: `--theme ocean|sunset|matrix|mono`,
`--palette auto|custom|fixed|basic`, `--no-anim`, `--no-color`, `--ascii`.

## Artist picker

`artist` shows the discography as two sections, `A` (Albums) and `S`
(Singles & EPs), and waits for a selection. `--select` skips the picker:

```bash
ytm-offline artist "BABYMETAL" --select "A[1-3],S[*]" --yes
```

Grammar: `Enter` recommended, `all` everything, `A[1-3]`, `S[2]`, `-S[4]`,
`none`/`q` abort. `--candidate N` chooses the Nth search result when a name is
ambiguous. `--yes` skips the start prompt.

## Deprecated aliases

These keep working with one stderr line. Removal target: **v0.2.0**.

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
| `--quota-deep` etc. | `--deep` etc. |
| `--per-queue-limit` | `--per-queue` |
| `--include-phase3` | `--no-phase3` |
| `--browser-profile P` | `--cookies-from-browser BROWSER:P` |
| `--singles-dir` | `YTM_SINGLES_DIR` |
| `--manifest-format` | `YTM_MANIFEST_FORMAT` |
| `--download` | default behaviour; use `--dry-run` to plan |
| `--total-cap` | `--limit` on `discover queues` |
| `--source` (discover sections) | `--sources` |
| `--per-container` (discover taste) | `--per-album` |
| `--include-live`, `--include-compilations`, `--include-remixes`, `--albums-only`, `--no-singles`, `--duration-window` | hidden compatibility aliases |

## Auth note

`ytmusicapi` uses the JSON headers file. `yt-dlp` uses a Netscape
`cookies.txt` or `--cookies-from-browser`. Never commit auth files; they are
gitignored.
