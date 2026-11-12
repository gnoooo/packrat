# Commands

<p align="center">
  <a href="../README.md">README</a> · <a href="INSTALL.md">Install</a> · <a href="QUICKSTART.md">Quickstart</a> · <strong>Commands</strong> · <a href="AUTH.md">Auth</a> · <a href="CONFIGURATION.md">Configuration</a>
</p>
<p align="center">
  <a href="TUI.md">TUI</a> · <a href="DISCOVERY.md">Discovery</a> · <a href="ARTISTS.md">Artists</a> · <a href="TROUBLESHOOTING.md">Troubleshooting</a> · <a href="DEVELOPMENT.md">Development</a> · <a href="HISTORY.md">History</a>
</p>

## Global shape

```bash
packrat [global options] <command> [command options]
```

Common options such as `--output`, `--limit`, `--dry-run` and `--yes` may go before or after the command. Run `packrat <command> --help` for the exact set.

## Commands

| Command | Purpose |
|---|---|
| `packrat login` | Create the YouTube Music headers file. |
| `packrat auth` | Verify the headers file. |
| `packrat logout` | Delete the saved headers file. |
| `packrat doctor` | Check dependencies, credentials and an optional canary. |
| `packrat export-metadata` | Export JSON/CSV metadata from a saved plan. |
| `packrat library` | List playlists (`--top N`). |
| `packrat download-one LINK_OR_ID` | Download one song and verify it. |
| `packrat sync` | Sync Liked Songs or a playlist. |
| `packrat download-manifest` | Resume/retry a saved plan; alias `resume`. |
| `packrat artist` | Download an artist catalog or selected releases. |
| `packrat discover sections` | Home, Charts, Moods, New Releases. |
| `packrat discover taste` | Taste-adjacent discovery plan. |
| `packrat discover queues` | Radio-queue playlists. |
| `packrat discover albums` | Albums from artists you like. |
| `packrat discover radio SEED` | One radio station. |

## Common options

| Option | Meaning |
|---|---|
| `--output/-o DIR` | Output root; default `.music`. |
| `--archive FILE` | Download history; default `.packrat_archive`. |
| `--delay SEC` | Pause between tracks; default `7.0`. |
| `--max-size SIZE` | Skip tracks larger than SIZE, e.g. `3GB`. |
| `--limit N` | Download at most N new tracks. |
| `--dry-run/-n` | Plan only. |
| `--yes/-y` | Skip confirmation. |
| `--tui` | Full-screen progress view. |
| `--headers FILE` | Login headers file. |
| `--cookies FILE` | Netscape cookie jar for yt-dlp. |
| `--cookies-from-browser SPEC` | Browser cookie extraction, e.g. `firefox:default-release`. |
| `-v/--verbose` | More logging. Repeat for more detail. |

`--cookies` and `--cookies-from-browser` are mutually exclusive.

## sync

```bash
packrat sync
packrat sync --source liked --limit 20
packrat sync --source "playlist:https://music.youtube.com/playlist?list=..."
packrat sync --dry-run --yes
```

Without `--source`, `sync` uses Liked Songs. 
It fetches the full source first, then applies archive/dedupe/limit logic.

## doctor

```bash
packrat doctor
packrat doctor --online
```

Local checks cover Python/dependencies, ffmpeg/ffprobe/Deno, the login file and its permissions. 
`--online` also calls YouTube Music and runs a small yt-dlp canary, which is the quickest way to detect a YouTube-side regression.

## export-metadata

```bash
packrat export-metadata
packrat export-metadata --manifest .music/playlists/liked.manifest.json --format csv
packrat export-metadata --file /tmp/metadata.json
```

Writes a normalized document with `video_id`, `title`, `artist`, `album`,
`track_num`, `duration_seconds`, `release_year`, `release_type`, `status`,
`path` and `source`.

## download-manifest / resume

```bash
packrat download-manifest
packrat download-manifest --manifest .music/playlists/liked.manifest.json
packrat download-manifest --failed-only
packrat resume --limit 5
```

With no `--manifest`, the newest plan in the output folder is used. 
If the manifest records an output folder, that folder is used unless `--output` overrides it.

## download-one

```bash
packrat download-one "https://music.youtube.com/watch?v=..."
packrat download-one dQw4w9WgXcQ  # id of music, e.g. after '=' and before '&'
```

Downloads, verifies the file with ffprobe/mutagen, and prints metadata.

## artist

```bash
packrat artist "BABYMETAL"
packrat artist "BABYMETAL" --select "A[1-3],S[*]" --yes
packrat artist --from-file artists.txt --also "Another Artist"
```

See [Artists](ARTISTS.md) for the picker and selection grammar.

## discover

```bash
packrat discover sections --sources home,charts --dry-run
packrat discover taste --quota 8
packrat discover queues --seed-count 12 --hop-depth 1
packrat discover albums --top-artists 15 --exclude-singles
packrat discover radio "https://music.youtube.com/watch?v=..."
```

Discovery writes a plan; download it with `download-manifest` or pass `--yes` when prompted.

See [Discovery](DISCOVERY.md).

## Deprecated command aliases

| Alias | Replacement |
|---|---|
| `retry` | `download-manifest --failed-only` |
| `discover-phase4` | `discover taste` |
| `discover-queues` | `discover queues` |
| `discover-albums` | `discover albums` |
| `radio` | `discover radio` |

Aliases print one stderr notice. Option aliases were removed in v0.2.0.
