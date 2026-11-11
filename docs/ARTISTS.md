# Artists

<p align="center">
  <a href="../README.md">README</a> · <a href="INSTALL.md">Install</a> · <a href="QUICKSTART.md">Quickstart</a> · <a href="COMMANDS.md">Commands</a> · <a href="AUTH.md">Auth</a> · <a href="CONFIGURATION.md">Configuration</a>
</p>
<p align="center">
  <a href="TUI.md">TUI</a> · <a href="DISCOVERY.md">Discovery</a> · <strong>Artists</strong> · <a href="TROUBLESHOOTING.md">Troubleshooting</a> · <a href="DEVELOPMENT.md">Development</a> · <a href="HISTORY.md">History</a>
</p>

## Resolve an artist

```bash
packrat artist "BABYMETAL"
packrat artist "https://music.youtube.com/channel/UC..."
```

Packrat searches YouTube Music and uses an exact name match when possible. If
several artists match, it shows a picker. `--candidate N` chooses the Nth
result non-interactively.

## Many artists

```bash
packrat artist --from-file artists.txt
packrat artist "Artist One" --also "Artist Two" --also "Artist Three"
```

`artists.txt` contains one name per line; blank lines and `#` comments are
ignored.

## Interactive picker

```bash
packrat artist "BABYMETAL"
```

Releases are grouped into `A` (Albums) and `S` (Singles & EPs). The picker
shows ownership and track counts. Selection grammar:

```text
Enter        # recommended default
all          # every release
A[1-3]       # albums 1 through 3
S[2]         # single/EP 2
-S[4]        # exclude item 4
none / q     # cancel
```

## Non-interactive selection

```bash
packrat artist "BABYMETAL" --select "A[1-3],S[*]" --yes
```

Useful options:

```text
--min-duration 90      # ignore shorter tracks
--max-duration 540     # ignore longer tracks
--exclude-singles      # keep albums and EPs
--candidate N          # choose a search result
```

## What gets downloaded

The catalog includes the artist's top songs and release tracks, deduped by
artist + base title. Already-owned tracks are detected from the archive,
filesystem paths and normalized title matching.

Folder layout:

```text
.music/<Artist>/<Album>/<NN - Title>.m4a
.music/<Artist>/Singles/<Title>.m4a
```

The catalog artist is forced as the folder owner, even when a release lists a
featured artist first.

## TUI

```bash
packrat --tui artist "BABYMETAL" --select "A[1-3],S[*]" --yes
```

## Next

- [Commands](COMMANDS.md)
- [Configuration](CONFIGURATION.md)
- [Troubleshooting](TROUBLESHOOTING.md)
