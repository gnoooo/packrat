# Discovery

<p align="center">
  <a href="../README.md">README</a> · <a href="INSTALL.md">Install</a> · <a href="QUICKSTART.md">Quickstart</a> · <a href="COMMANDS.md">Commands</a> · <a href="AUTH.md">Auth</a> · <a href="CONFIGURATION.md">Configuration</a>
</p>
<p align="center">
  <a href="TUI.md">TUI</a> · <strong>Discovery</strong> · <a href="ARTISTS.md">Artists</a> · <a href="TROUBLESHOOTING.md">Troubleshooting</a> · <a href="DEVELOPMENT.md">Development</a> · <a href="HISTORY.md">History</a>
</p>

Discovery commands build a **plan** first. You can inspect it, then download it with `download-manifest` or confirm immediately.

## Workflow

```bash
packrat discover sections --sources home,charts --dry-run
packrat download-manifest
```

Or let the command ask to download at the end:

```bash
packrat discover sections --sources home,charts
```

## Sources

| Command | What it finds |
|---|---|
| `discover sections` | Home, Charts, Moods, New Releases. |
| `discover taste` | Deep catalog, similar artists, radio, Last.fm/tag discovery. |
| `discover queues` | Tracks from YouTube Music radio queues. |
| `discover albums` | Albums from your top artists. |
| `discover radio SEED` | One radio station from a song link/ID. |

## Common options

Not every subcommand accepts every option; `discover taste` is plan-only and
`discover albums`/`queues`/`radio` have their own subset. Run
`packrat discover <subcommand> --help` for the exact list.

```text
--quota N              songs per source/category
--min-duration SEC     default 90
--max-duration SEC     default 540
--per-artist N         default 3
--per-album N          default 2
--limit N              cap downloads
--dry-run              plan only
--yes                  download without asking
```

`discover sections`:

```bash
packrat discover sections \
  --sources home,charts,moods,new_releases \
  --country FR --quota 10
```

`discover taste`:

```bash
packrat discover taste --quota 8 --deep 12 --adjacent 10 --radio 10
```

`discover queues`:

```bash
packrat discover queues --seed-count 12 --hop-depth 1 --hop-width 3
```

`discover albums`:

```bash
packrat discover albums --top-artists 15 --albums-per-artist 2 --exclude-singles
```

`discover radio`:

```bash
packrat discover radio "https://music.youtube.com/watch?v=..."
```

## Dedupe and archive

Discovery filters out tracks already in the archive and dedupes with
album-aware release keys. `--skip-previous` skips tracks from the previous
discovery plan.

## Last.fm

`discover taste` can use Last.fm tags/similar artists when
`LASTFM_API_KEY` is set:

```bash
export LASTFM_API_KEY=...
packrat discover taste --external 5 --tag 5
```

## TUI

`discover queues`, `discover albums` and `discover radio` support `--tui`.
