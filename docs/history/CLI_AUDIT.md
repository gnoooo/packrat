# CLI Audit — `refactor/cli-uniform`

Branch: `refactor/cli-uniform` (from `dev`).
Scope: the CLI surface, parsing, help text and docs only. No downloader/pipeline, TUI rendering, selection grammar, manifest format or counting changes.

## Current state at a glance

- 15 top-level commands: `auth`, `migrate-layout`, `library`, `download-one`, `sync`, `retry`, `discover`, `discover-phase4`, `discover-queues`, `radio`, `download-manifest`, `artist`, `discover-albums`, `tui-demo`, `tui-glyphs`.
- 8 global options declared on the top-level parser: `--version`, `-v/--verbose`, `--headers`, `--cookies`, `--cookies-from-browser`, `--browser-profile`, `--tui`, `--ascii`, `--singles-dir`.
- ~95 distinct per-command options after de-duplicating the visual flags that are auto-attached to every subparser.
- Visual flags (`--theme`, `--no-anim`, `--no-color`, `--ascii`, `--palette`) are added in a loop at the end of `build_parser()`, so every command inherits them whether it uses them or not.
- Only `sync.py` reads `args.*` directly; every other command handler unpacks explicit `argparse.Namespace` attributes in `cli.py`.

## 1. Option inventory

Legend: **KEEP** / **RENAME** / **MERGE** / **ALIAS** / **REMOVE**.
An option is marked **DEAD** if it is never read anywhere.

### Global / top-level options

| Option | Dest | Type | Default | Env | What it does | Read in | README/tests? | Proposal |
|--------|------|------|---------|-----|--------------|---------|---------------|----------|
| `--version` | — | — | — | — | show version | `argparse` built-in | no | KEEP |
| `-v`, `--verbose` | `verbose` | bool | `False` | — | verbose logging | not read anywhere (DEAD) | no | **REMOVE** or wire to logging; currently dead |
| `--headers` | `headers` | str | `~/.config/ytmusic-composer/browser.json` if it exists | `YTM_HEADERS` | ytmusicapi browser-headers JSON | `cli.py` (`load_ytmusic`, `cmd_*`, `main`) | README + tests | KEEP; add to every command that needs auth via shared group |
| `--cookies` | `cookies` | str | `None` (or `YTM_COOKIES`) | `YTM_COOKIES` | path to Netscape cookies.txt | `cli.py`, `sync.py` | README | KEEP; keep only if cookies-from-browser is not enough (it is used in `download-one` and batch path) |
| `--cookies-from-browser` | `cookies_from_browser` | str | `None` | — | browser name/profile for yt-dlp | `cli.py`, `sync.py` | README | KEEP; fold `--browser-profile` into `BROWSER[:PROFILE]` syntax |
| `--browser-profile` | `browser_profile` | str | `None` | — | Firefox profile name/path | `cli.py`, `sync.py` | no | **REMOVE** (redundant with `BROWSER[:PROFILE]` profile embedded in `--cookies-from-browser`) |
| `--tui` | `tui` | bool | `SUPPRESS` in subcommands | — | use curses TUI | `cli.py` (download commands) | README | KEEP; move to run-control group |
| `--ascii` | `ascii` | bool | `SUPPRESS` in subcommands / top-level `False` | — | ASCII progress bars | `cli.py` (TUI calls), `cmd_tui_glyphs` | README | KEEP; move to display group |
| `--singles-dir` | `singles_dir` | str | `Singles` | — | subfolder for singles | `cmd_migrate_layout`, `cmd_discover`, `cmd_artist`, `cmd_discover_queues` (via `singles_dir` kwarg) | no | **REMOVE** from CLI; make `YTM_SINGLES_DIR` env var with default `Singles` |

### `auth`

| Option | Dest | Type | Default | Env | What it does | Read in | README/tests? | Proposal |
|--------|------|------|---------|-----|--------------|---------|---------------|----------|
| `--theme` | `theme` | str | `ocean` | — | color theme | not read | no | KEEP (display group, harmless) |
| `--no-anim` | `no_anim` | bool | `False` | — | disable animations | `cmd_tui_glyphs` only | no | KEEP (display group) |
| `--no-color` | `no_color` | bool | `False` | — | disable color | `cmd_tui_glyphs` only | no | KEEP (display group) |
| `--ascii` | `ascii` | bool | `SUPPRESS` | — | ASCII glyphs | `cmd_tui_glyphs` | no | KEEP (display group) |
| `--palette` | `palette` | str | `auto` | — | palette tier | `cmd_tui_glyphs` only | no | KEEP (display group) |

### `migrate-layout`

| Option | Dest | Type | Default | Env | What it does | Read in | README/tests? | Proposal |
|--------|------|------|---------|-----|--------------|---------|---------------|----------|
| `--output`, `-o` | `output` | str | `music` | — | output root | `cmd_migrate_layout` | test_migrate.py | KEEP (run-control group) |
| `--dry-run` | `dry_run` | bool | `False` | — | print plan | `cmd_migrate_layout` | test_migrate.py | KEEP |
| `--undo` | `undo` | bool | `False` | — | undo last migration | `cmd_migrate_layout` | no | KEEP (maintenance command only) |
| `--yes` | `yes` | bool | `False` | — | skip confirm | `cmd_migrate_layout` | no | KEEP (run-control group) |
| visual flags | — | — | — | — | see `auth` | mostly not read | no | KEEP via display group |

### `library`

| Option | Dest | Type | Default | Env | What it does | Read in | README/tests? | Proposal |
|--------|------|------|---------|-----|--------------|---------|---------------|----------|
| `--limit` | `limit` | int | `25` | — | max playlists to list | `cmd_library` | no | **RENAME** to `--max` or `--top`? Same flag is used with a different meaning elsewhere. Propose `--top N` for listing cap to free `--limit` for the unified download cap. |
| visual flags | — | — | — | — | — | mostly not read | no | KEEP via display group |

### `download-one`

| Option | Dest | Type | Default | Env | What it does | Read in | README/tests? | Proposal |
|--------|------|------|---------|-----|--------------|---------|---------------|----------|
| `video_id` positional | `video_id` | str | required | — | video ID to download | `cmd_download_one` | README | KEEP |
| `--output`, `-o` | `output` | str | `music` | — | output root | `cmd_download_one` | README | KEEP |
| `--cookies` | `cookies` | str | `None` (`YTM_COOKIES`) | `YTM_COOKIES` | cookie file | `cmd_download_one` | README | KEEP (auth group) |
| `--cookies-from-browser` | `cookies_from_browser` | str | `None` | — | browser cookies | `cmd_download_one` | README | KEEP (auth group) |
| `--browser-profile` | `browser_profile` | str | `None` | — | profile | `cmd_download_one` | no | **REMOVE** (merge into `--cookies-from-browser`) |
| visual flags | — | — | — | — | — | mostly not read | no | KEEP via display group |

### `sync`

| Option | Dest | Type | Default | Env | What it does | Read in | README/tests? | Proposal |
|--------|------|------|---------|-----|--------------|---------|---------------|----------|
| `--source` | `source` | str | required | — | `liked` or `playlist:<id/url>` | `cli.py`, `sync.py` | README | KEEP; maybe rename to `--from` for consistency? Keep `--source` (clear). |
| `--limit` | `limit` | int | `None` | — | max tracks to **resolve** | `sync.py` (`resolve_tracks`) | no | **BEHAVIOUR CHANGE**: make `--limit` the max **new** tracks to download (after archive dedupe), not resolution cap. Move resolution cap to a rarely-used env var or a new `--resolve-limit` if needed. |
| `--output`, `-o` | `output` | str | `music` | — | output root | `cli.py`, `sync.py` | README | KEEP |
| `--archive` | `archive` | str | `.ytm_offline_archive` | — | archive file | `cli.py`, `sync.py` | no | KEEP; add env `YTM_ARCHIVE`? |
| `--delay` | `delay` | float | `7.0` | — | seconds between downloads | `cli.py`, `sync.py` | no | KEEP |
| `--max-size` | `max_size` | str | `None` | — | skip larger tracks | `cli.py`, `sync.py` | no | KEEP |
| `--cookies` | `cookies` | str | `None` (`YTM_COOKIES`) | `YTM_COOKIES` | cookie file | `cli.py`, `sync.py` | README | KEEP |
| `--cookies-from-browser` | `cookies_from_browser` | str | `None` | — | browser cookies | `cli.py`, `sync.py` | README | KEEP |
| `--browser-profile` | `browser_profile` | str | `None` | — | profile | `cli.py`, `sync.py` | no | **REMOVE** |
| `--dry-run` | `dry_run` | bool | `False` | — | write manifest only | `cli.py`, `sync.py` | no | KEEP; add `-n` short if no clash |
| `--manifest-format` | `manifest_format` | str | `json` | — | `json` or `csv` | `sync.py` | no | **REMOVE** from CLI; env `YTM_MANIFEST_FORMAT={json,csv}` |
| `--tui` | `tui` | bool | `SUPPRESS` | — | curses UI | `cli.py` | README | KEEP |
| `--ascii` | `ascii` | bool | `SUPPRESS` | — | ASCII bars | `cli.py` | README | KEEP |
| visual flags | — | — | — | — | — | mostly not read | no | KEEP via display group |

### `retry`

This command is a thin wrapper around a manifest with failed/pending entries. It is fully covered by `download-manifest --failed-only`.

| Option | Dest | Type | Default | Env | What it does | Read in | README/tests? | Proposal |
|--------|------|------|---------|-----|--------------|---------|---------------|----------|
| `--manifest` | `manifest` | str | required | — | manifest path | `sync.py` (`run_retry`) | no | KEEP, but on `download-manifest --manifest PATH --failed-only` |
| run-control options | — | — | — | — | same as `sync` | `sync.py` | no | KEEP on `download-manifest` |
| `--tui`, `--ascii`, visual | — | — | — | — | — | `cli.py` | no | KEEP on `download-manifest` |
| **command itself** | — | — | — | — | retry failed tracks | `cli.py` | no | **REMOVE** command; fold into `download-manifest --failed-only` |

### `discover`

| Option | Dest | Type | Default | Env | What it does | Read in | README/tests? | Proposal |
|--------|------|------|---------|-----|--------------|---------|---------------|----------|
| `--source` | `source` | str | `home,charts,moods,new_releases` | — | comma-separated sources | `cli.py` | no | KEEP; rename command to `discover sections` |
| `--country` | `country` | str | `FR` | — | country for charts | `cli.py` | no | KEEP |
| `--quota` | `quota` | int | `10` | — | max tracks per source | `cli.py` | no | KEEP (or `--per-source`) |
| `--per-container` | `per_container` | int | `5` | — | max tracks per container | `cli.py` | no | KEEP |
| `--per-artist` | `per_artist` | int | `3` | — | max tracks per artist per source | `cli.py` | no | KEEP |
| `--min-duration` | `min_duration` | int | `90` | — | min track duration | `cli.py` | no | KEEP; add to shared selection group |
| `--max-duration` | `max_duration` | int | `540` | — | max track duration | `cli.py` | no | KEEP; add to shared selection group |
| `--output`, `-o` | `output` | str | `music` | — | output root | `cli.py` | no | KEEP |
| `--archive` | `archive` | str | `.ytm_offline_archive` | — | archive file | `cli.py` | no | KEEP |
| `--delay` | `delay` | float | `7.0` | — | seconds between downloads | `cli.py` | no | KEEP |
| `--max-size` | `max_size` | str | `None` | — | skip larger tracks | `cli.py` | no | KEEP |
| `--dry-run` | `dry_run` | bool | `False` | — | write manifest without downloading | `cli.py` | no | KEEP; default should become **execute** after recap |
| `--singles-dir` | `singles_dir` | str | `Singles` | — | singles folder | passed to `run_discover` | no | **REMOVE** -> env |
| visual flags | — | — | — | — | — | `cli.py` | no | KEEP via display group |

### `discover-phase4`

| Option | Dest | Type | Default | Env | What it does | Read in | README/tests? | Proposal |
|--------|------|------|---------|-----|--------------|---------|---------------|----------|
| `--quota-deep` | `quota_deep` | int | `12` | — | deep-cut tracks | `cli.py` | no | KEEP; rename to `--deep` inside `discover taste` |
| `--quota-adjacent` | `quota_adjacent` | int | `10` | — | adjacent-artist tracks | `cli.py` | no | KEEP; rename to `--adjacent` |
| `--quota-radio` | `quota_radio` | int | `10` | — | radio tracks | `cli.py` | no | KEEP; rename to `--radio` |
| `--quota-external` | `quota_external` | int | `5` | — | external-graph tracks | `cli.py` | no | KEEP; rename to `--external` |
| `--quota-tag` | `quota_tag` | int | `5` | — | tag-search tracks | `cli.py` | no | KEEP; rename to `--tag` |
| `--per-container` | `per_container` | int | `2` | — | max per container | `cli.py` | no | KEEP |
| `--per-artist` | `per_artist` | int | `3` | — | max per artist | `cli.py` | no | KEEP |
| `--min-duration`, `--max-duration` | — | int | 90/540 | — | duration filter | `cli.py` | no | KEEP |
| `--output`, `--archive`, `--max-size` | — | — | — | — | standard run-control | `cli.py` | no | KEEP via shared group |
| `--include-phase3` | `include_phase3` | bool | `True` | — | dedupe against phase 3 manifest | `cli.py` | no | **FLIP DEFAULT** and rename to `--no-phase3`; current help says default true but the flag is `--include-phase3` which is confusing |
| `--dry-run` | `dry_run` | bool | `False` | — | currently required (command only supports dry-run) | `cli.py` | no | KEEP; remove the "dry-run only" restriction later |
| visual flags | — | — | — | — | — | `cli.py` | no | KEEP |
| **command name** | — | — | — | — | — | — | no | **RENAME** to `discover taste` (or `discover phase4`); current name leaks internal phase naming |

### `discover-queues`

| Option | Dest | Type | Default | Env | What it does | Read in | README/tests? | Proposal |
|--------|------|------|---------|-----|--------------|---------|---------------|----------|
| `--seed-count` | `seed_count` | int | `12` | — | liked-track seeds | `cli.py` | no | KEEP; inside `discover queues` |
| `--hop-depth` | `hop_depth` | int | `1` | — | queue hop depth | `cli.py` | no | KEEP |
| `--hop-width` | `hop_width` | int | `3` | — | second-hop seeds | `cli.py` | no | KEEP |
| `--per-artist-per-queue` | `per_artist_per_queue` | int | `3` | — | per artist per queue | `cli.py` | no | KEEP (or `--per-artist`) |
| `--per-queue-limit` | `per_queue_limit` | int | `25` | — | max tracks per queue | `cli.py` | no | KEEP |
| `--total-cap` | `total_cap` | int | `200` | — | total tracks | `cli.py` | no | KEEP (or `--limit`) |
| `--min-duration`, `--max-duration` | — | int | 90/540 | — | duration filter | `cli.py` | no | KEEP |
| `--random-seed` | `random_seed` | int | `None` | — | reproducibility seed | `cli.py` | no | KEEP |
| standard run-control | — | — | — | — | output, archive, delay, max-size | `cli.py` | no | KEEP via shared group |
| auth options | — | — | — | — | cookies | `cli.py` | no | KEEP via auth group |
| `--download` | `download` | bool | `False` | — | download after building manifest | `cli.py` | no | **REMOVE**; default to execute after recap, use `--dry-run` to stop at plan |
| `--singles-dir` | `singles_dir` | str | `Singles` | — | singles folder | passed to `discover_queues` | no | **REMOVE** -> env |
| **command name** | — | — | — | — | — | — | no | **RENAME** to `discover queues` |

### `radio`

| Option | Dest | Type | Default | Env | What it does | Read in | README/tests? | Proposal |
|--------|------|------|---------|-----|--------------|---------|---------------|----------|
| `seed` positional | `seed` | str | required | — | video id or URL | `cli.py` | no | KEEP; under `discover radio SEED` |
| `--limit` | `limit` | int | `50` | — | max tracks in radio | `cli.py` | no | KEEP (resolution cap; could be unified with execution `--limit` if radio implements post-plan download cap) |
| `--per-artist` | `per_artist` | int | `3` | — | max tracks per artist in radio | `cli.py` | no | KEEP |
| `--min-duration`, `--max-duration` | — | int | 90/540 | — | duration filter | `cli.py` | no | KEEP |
| standard run-control + auth | — | — | — | — | — | `cli.py` | no | KEEP via shared groups |
| `--download` | `download` | bool | `False` | `SUPPRESS` | hidden compat flag | not read | no | **REMOVE** |
| `--dry-run` | `dry_run` | bool | `False` | — | list radio without downloading | `cli.py` | no | KEEP; current default is to download without recap/confirm — **behaviour change** to plan → confirm → execute |
| **command name** | — | — | — | — | — | — | no | **RENAME** to `discover radio` |

### `download-manifest`

| Option | Dest | Type | Default | Env | What it does | Read in | README/tests? | Proposal |
|--------|------|------|---------|-----|--------------|---------|---------------|----------|
| `--manifest` | `manifest` | str | required | — | manifest path | `cli.py`, `sync.py` (via folded retry) | tests | KEEP |
| `--output`, `-o` | `output` | str | `None` | — | output root (default parent of `playlists/`) | `cli.py` | no | KEEP |
| `--yes` | `yes` | bool | `False` | — | skip confirm | `cli.py` | tests | KEEP |
| `--tui`, `--ascii`, run-control, auth | — | — | — | — | — | `cli.py` | tests | KEEP via shared groups |
| `--failed-only` | — | — | — | — | new option to replace `retry` | would be read in `cli.py` | no | **ADD**; folds `retry` command into this one |
| `--dry-run` | — | — | — | — | print pending summary and stop | not currently present | no | **ADD** for consistency |

### `artist`

| Option | Dest | Type | Default | Env | What it does | Read in | README/tests? | Proposal |
|--------|------|------|---------|-----|--------------|---------|---------------|----------|
| `artists` positional | `artists` | str | required, repeatable | — | name/channel id/URL | `cli.py` | README | KEEP; repeatable target list |
| `--from-file` | `from_file` | str | `None` | — | file with artist specs | `cli.py` | no | KEEP; generalise as repeatable target mechanism? It already is. |
| `--pick` | `pick` | int | `None` | — | pick Nth candidate from search | `cli.py` | no | **RENAME** to `--candidate N` (`-c`?) to avoid giving `--pick` two meanings |
| `--include-live` | `include_live` | bool | `False` | — | tag live releases as selected by default | `cli.py` -> `artists.build_catalog` | README | **REMOVE**; default behaviour (tagged, deselected) is already the safe default; interactive `all` covers the override |
| `--include-compilations` | `include_compilations` | bool | `False` | — | tag compilations as selected | `cli.py` | README | **REMOVE** (same reason) |
| `--include-remixes` | `include_remixes` | bool | `False` | — | tag remix/instrumental as selected | `cli.py` | README | **REMOVE** (same reason) |
| `--albums-only` | `albums_only` | bool | `False` | — | tag non-albums as excluded | `cli.py` | README | **REMOVE** (same reason) |
| `--no-singles` | `no_singles` | bool | `False` | — | tag singles as excluded | `cli.py` | README | **REMOVE** (same reason) |
| `--duration-window` | `duration_window` | bool | `False` | — | keep only 90–540s tracks | `cli.py` | README | **REMOVE** in favour of `--min-duration`/`--max-duration` shared selection options |
| `--scope` | `scope` | str | `all` | — | `top-songs`, `albums`, `all` | `cli.py` | README | **RENAME** to `--set`? Or keep `--scope` as a command-specific knob. Propose keep for now but document; it only affects artist. |
| `--top-songs` | `scope` | str | `top-songs` | — | shorthand for `--scope top-songs` | `cli.py` | no | **ALIAS** of `--scope top-songs` |
| `--top-limit` | `top_limit` | int | `None` | — | max top songs | `cli.py` | no | KEEP (only meaningful with `--scope top-songs`) |
| `--max-per-album` | `max_per_album` | int | `None` | — | cap kept versions per album in top songs | `cli.py` | no | KEEP |
| `--yes`, `--dry-run`, run-control, auth | — | — | — | — | standard | `cli.py` | README (dry-run) | KEEP via shared groups |
| `--select` | — | str | `None` | — | **NEW**: selection expression to skip the interactive picker | would be read in `cmd_artist` | no | **ADD**; e.g. `--select "A[1-3],S[*]"`. Makes `--pick`/`--include-*` less necessary. |
| `--singles-dir` | `singles_dir` | str | `Singles` | — | singles folder | `cli.py` -> `artists.build_catalog` | no | **REMOVE** -> env |

### `discover-albums`

| Option | Dest | Type | Default | Env | What it does | Read in | README/tests? | Proposal |
|--------|------|------|---------|-----|--------------|---------|---------------|----------|
| `--artist-top-k` | `artist_top_k` | int | `15` | — | top liked artists to consider | `cli.py` | no | KEEP; inside `discover albums` |
| `--albums-per-artist` | `albums_per_artist` | int | `2` | — | max albums per artist | `cli.py` | no | KEEP |
| `--include-singles` | `include_singles` | bool | `False` | — | include singles/EPs | `cli.py` | no | KEEP (or map to `--select` if grammar supports release-kind filtering) |
| `--min-duration`, `--max-duration` | — | int | 90/540 | — | duration filter | `cli.py` | no | KEEP |
| standard run-control + auth | — | — | — | — | — | `cli.py` | no | KEEP via shared groups |
| `--download` | `download` | bool | `False` | — | download after building manifest | `cli.py` | no | **REMOVE**; default execute, `--dry-run` to plan |
| **command name** | — | — | — | — | — | — | no | **RENAME** to `discover albums` |

### `tui-demo`

| Option | Dest | Type | Default | Env | What it does | Read in | README/tests? | Proposal |
|--------|------|------|---------|-----|--------------|---------|---------------|----------|
| `--seconds` | `seconds` | float | `60.0` | — | target simulated duration | `cmd_tui_demo` | README | KEEP; under `ui demo` |
| visual flags | — | — | — | — | — | `cmd_tui_demo` | README | KEEP |
| **command name** | — | — | — | — | — | — | README | **RENAME** to `ui demo` |

### `tui-glyphs`

| Option | Dest | Type | Default | Env | What it does | Read in | README/tests? | Proposal |
|--------|------|------|---------|-----|--------------|---------|---------------|----------|
| visual flags | — | — | — | — | — | `cmd_tui_glyphs` | README | KEEP |
| **command name** | — | — | — | — | — | — | README | **RENAME** to `ui glyphs` |

## 2. Conflicts and inconsistencies

### (a) Same flag, different meaning

- `--limit`
  - `library`: number of playlists to display.
  - `sync`: max tracks to **resolve** from the source.
  - `radio`: max tracks to build in the radio.
  - Proposed unified meaning: max **new** tracks to download in this run (applied after dedupe + selection). Resolution caps get different names or env vars.
- `--pick`
  - `artist`: choose the Nth search candidate.
  - There is no second consumer today, but the name is generic and collides conceptually with "picking" releases in the interactive UI. Proposed rename: `--candidate N`.
- `--dry-run`
  - `sync`, `discover`, `discover-phase4`, `artist`, `radio`: plan/manifest only, do not download.
  - `migrate-layout`: print file moves, do not move.
  - Meaning is consistent enough ("do not mutate"), but `download-manifest` lacks it.
- `--yes`
  - `artist`: skip both the selection prompt and the start prompt.
  - `download-manifest`: skip the "download now?" confirm.
  - `migrate-layout`: skip move confirmation.
  - Meaning is consistent ("accept default, skip interactive confirms").
- `--download`
  - `discover-queues` / `discover-albums`: switch from plan to execute.
  - `radio`: hidden suppressed legacy flag, unused.
  - This is backwards from the proposed model where execution is the default and `--dry-run` stops at the plan.

### (b) Same meaning, different flag

- `--albums-only` / `--no-singles` / `--include-singles` all express "which release kinds to consider". They appear on different commands with different defaults. Proposed unification: drive release-kind filtering through the shared `--select` grammar where possible, or keep a single `--include-singles` bool on `discover albums` only.
- `--per-artist` vs `--per-artist-per-queue`: both cap tracks per artist, but in different contexts. Propose keep distinct because contexts differ, or collapse to `--per-artist` everywhere with documented per-command semantics.
- `--duration-window` (artist) vs `--min-duration`/`--max-duration` (discover/radio/queues/albums). Same meaning (filter track duration) but different interface. Propose remove `--duration-window` and use `--min-duration`/`--max-duration` everywhere.

### (c) Same flag, different default or type

- `--limit` defaults differ: `library=25`, `radio=50`, `sync=None`.
- `--per-artist` defaults differ: `discover=3`, `discover-phase4=3`, `radio=3`.
- `--min-duration`/`--max-duration` defaults are identical (90/540) across commands that have them; good.
- `--dry-run` is a boolean everywhere; consistent.
- `--output` defaults to `music` except `download-manifest` where it is `None` (computed from manifest path).

### (d) Options that exist only by accident

- `--verbose` is declared globally but never read.
- `--browser-profile` duplicates profile syntax that belongs inside `--cookies-from-browser`.
- `--download` on `radio` is suppressed and unused.
- `--include-live`, `--include-compilations`, `--include-remixes`, `--albums-only`, `--no-singles` on `artist` are all rendered moot by the new picker (they only change which releases are pre-selected; the default "tagged but deselected" already covers the common case).
- `--top-songs` is just a `--scope` shorthand.
- `--singles-dir` is a layout policy, not a per-run choice.
- `--manifest-format` only affects `sync`; better as an env var.

## 3. Global option positions and display options

### Global options accepted only BEFORE the subcommand

The following top-level options are **not** added to subparsers, so `ytm-offline sub --headers ...` currently fails:

- `--headers`
- `--cookies`
- `--cookies-from-browser`
- `--browser-profile`
- `--singles-dir`
- `--version`
- `-v/--verbose`

The target design requires these to work both before and after the subcommand. The standard argparse pattern is:

1. Define shared parent parsers in `cli_args.py`.
2. Add them to the top-level parser **and** to every subparser with `parents=[...]`.
3. Use `default=argparse.SUPPRESS` on the subparser copies so a value given before the subcommand is not overwritten by a subparser default.

### Display / TUI options

These options are currently **missing from the top-level help** and are instead auto-attached to every subparser:

- `--theme`
- `--no-anim`
- `--no-color`
- `--palette`

`--ascii` and `--tui` are on the top-level parser but also redeclared (with `SUPPRESS`) on several subcommands. The result is a lot of duplication in the help output.

Proposed grouping in `cli_args.py`:

- `Authentication`: `--headers`, `--cookies`, `--cookies-from-browser`.
- `Run control`: `--output`, `--archive`, `--delay`, `--max-size`, `--limit`, `--dry-run`, `--yes`, `--tui`.
- `Selection`: `--select`, `--min-duration`, `--max-duration`, plus command-specific knobs.
- `Display`: `--theme`, `--no-anim`, `--no-color`, `--ascii`, `--palette`.

Env vars already respected by the TUI code: `NO_COLOR`, `YTM_TUI_ANIM=0`. These should be documented in the display group help text.

## 4. Proposed final CLI

This is a mock-up for discussion, not the final implementation.

### `ytm-offline -h`

```
usage: ytm-offline [-h] [--version] [-v] [--headers HEADERS]
                   [--cookies COOKIES | --cookies-from-browser BROWSER[:PROFILE]]
                   [--tui] [--ascii] [--theme THEME] [--no-anim] [--no-color]
                   [--palette {auto,custom,fixed,basic}]
                   {auth,library,download-one,sync,download-manifest,artist,discover,ui,maintenance} ...

Download music from your YouTube Music account for offline use.

options:
  -h, --help            show this help message and exit
  --version             show program's version number and exit
  -v, --verbose         verbose logging

Authentication:
  --headers PATH        ytmusicapi browser-headers JSON file
                        (env: YTM_HEADERS; default: ~/.config/ytmusic-composer/browser.json)
  --cookies FILE        Netscape cookies.txt for yt-dlp (env: YTM_COOKIES)
  --cookies-from-browser BROWSER[:PROFILE]
                        browser/profile for yt-dlp --cookies-from-browser
                        (env: YTM_COOKIES_FROM_BROWSER)

Display:
  --theme THEME         color theme: ocean/sunset/matrix/mono (default: ocean)
  --no-anim             disable TUI animations (env: YTM_TUI_ANIM=0)
  --no-color            disable colors (env: NO_COLOR)
  --ascii               use ASCII progress bars
  --palette {auto,custom,fixed,basic}
                        color tier (default: auto)

commands:
  auth                  validate ytmusicapi auth
  library               list library playlists
  download-one          download and verify one track by video ID
  sync                  sync liked songs or a playlist
  download-manifest     resume/failed downloads from an existing manifest
  artist                download one or more artists' full catalogs
  discover              discover tracks (sections, taste, queues, albums, radio)
  ui                    TUI helpers (demo, glyphs)
  maintenance           layout/tools (migrate-layout)
```

### `ytm-offline artist -h`

```
usage: ytm-offline artist [-h] [--headers HEADERS]
                          [--cookies FILE | --cookies-from-browser BROWSER[:PROFILE]]
                          [-n] [-y] [--limit N] [--max-size SIZE] [--delay SEC]
                          [--output DIR] [--archive FILE] [--tui] [--ascii]
                          [--select EXPR] [--candidate N] [--scope {top-songs,albums,all}]
                          [--top-limit N] [--max-per-album N]
                          [--min-duration SEC] [--max-duration SEC]
                          [--theme THEME] [--no-anim] [--no-color] [--palette TIER]
                          artists [artists ...]

Discover and download one or more artists' full catalogs.

positional arguments:
  artists               artist name, channelId, or channel URL

options:
  -h, --help            show this help message and exit
  -y, --yes             skip the interactive prompts and use the default selection
  -n, --dry-run         write the manifest only, do not download

Selection:
  --select EXPR         non-interactive selection, e.g. A[1-3],S[*]
  --candidate N         pick the Nth search candidate when the name is ambiguous
  --scope {top-songs,albums,all}
                        which part of the catalog to resolve (default: all)
  --top-limit N         max top songs (only with --scope top-songs)
  --max-per-album N     max kept versions per album (only with --scope top-songs)
  --min-duration SEC    skip shorter tracks (default: 90)
  --max-duration SEC    skip longer tracks (default: 540)

Run control:
  --output DIR, -o DIR  output root directory (default: music)
  --archive FILE        download archive file (default: .ytm_offline_archive)
  --delay SEC           seconds between downloads (default: 7.0)
  --max-size SIZE       skip tracks estimated larger than SIZE (e.g. 3GB)
  --limit N             max new tracks to download this run
  --tui                 use the curses TUI
  --ascii               use ASCII progress bars in the TUI

Authentication:
  --headers PATH        (env: YTM_HEADERS)
  --cookies FILE        (env: YTM_COOKIES)
  --cookies-from-browser BROWSER[:PROFILE]
                        (env: YTM_COOKIES_FROM_BROWSER)

Display:
  --theme THEME         color theme (default: ocean)
  --no-anim             disable TUI animations
  --no-color            disable colors
  --palette {auto,custom,fixed,basic}

examples:
  ytm-offline artist "BABYMETAL" --dry-run
  ytm-offline artist "BABYMETAL" --select "A[1-3],S[*]" --yes
  ytm-offline artist --from-file artists.txt --candidate 1
```

### `ytm-offline sync -h`

```
usage: ytm-offline sync [-h] [--headers HEADERS]
                        [--cookies FILE | --cookies-from-browser BROWSER[:PROFILE]]
                        [-n] [-y] [--limit N] [--max-size SIZE] [--delay SEC]
                        [--output DIR] [--archive FILE] [--tui] [--ascii]
                        [--theme THEME] [--no-anim] [--no-color] [--palette TIER]
                        --source SOURCE

Sync liked songs or a playlist to disk.

options:
  -h, --help            show this help message and exit
  --source SOURCE       liked or playlist:<id/url>
  -y, --yes             skip the confirmation prompt
  -n, --dry-run         write the manifest only, do not download

Run control:
  --output DIR, -o DIR  output root directory (default: music)
  --archive FILE        download archive file (default: .ytm_offline_archive)
  --delay SEC           seconds between downloads (default: 7.0)
  --max-size SIZE       skip tracks estimated larger than SIZE
  --limit N             max new tracks to download this run (after dedupe)
  --tui                 use the curses TUI
  --ascii               use ASCII progress bars in the TUI

Authentication/Display as above.

examples:
  ytm-offline sync --source liked --limit 50 --yes
  ytm-offline sync --source playlist:PLID --dry-run
```

### `ytm-offline discover -h`

```
usage: ytm-offline discover [-h] <sections|taste|queues|albums|radio> ...

Discover new tracks from YouTube Music and beyond.

commands:
  sections (default)    Home/Charts/Moods/New Releases
  taste                 taste-adjacent tracks beyond YT Music sections
  queues                playback-queue playlists from liked-track seeds
  albums                whole albums from top liked artists
  radio                 auto radio built from one track URL/id
```

### `ytm-offline discover sections -h`

```
usage: ytm-offline discover sections [-h] [--source SRCS] [--country CC]
                                     [--quota N] [--per-container N]
                                     [--per-artist N] [--min-duration SEC]
                                     [--max-duration SEC] [-n] [-y] [--limit N]
                                     [--max-size SIZE] [--delay SEC]
                                     [--output DIR] [--archive FILE] [--tui] [--ascii]
                                     ...
```

## 5. Old → new mapping

| Old | New | Status |
|-----|-----|--------|
| `ytm-offline migrate-layout` | `ytm-offline maintenance migrate-layout` | RENAME command (old as hidden alias) |
| `ytm-offline retry` | `ytm-offline download-manifest --manifest PATH --failed-only` | REMOVE command; option on `download-manifest` |
| `ytm-offline discover` | `ytm-offline discover sections` | RENAME (old alias) |
| `ytm-offline discover-phase4` | `ytm-offline discover taste` | RENAME (old alias) |
| `ytm-offline discover-queues` | `ytm-offline discover queues` | RENAME (old alias) |
| `ytm-offline discover-albums` | `ytm-offline discover albums` | RENAME (old alias) |
| `ytm-offline radio` | `ytm-offline discover radio` | RENAME (old alias) |
| `ytm-offline tui-demo` | `ytm-offline ui demo` | RENAME (old alias) |
| `ytm-offline tui-glyphs` | `ytm-offline ui glyphs` | RENAME (old alias) |
| `--browser-profile` | folded into `--cookies-from-browser BROWSER[:PROFILE]` | REMOVE option |
| `--singles-dir` | `YTM_SINGLES_DIR` env var | REMOVE option |
| `--manifest-format` | `YTM_MANIFEST_FORMAT` env var | REMOVE option |
| `--verbose` | (wire to logger or remove) | REMOVE/IMPLEMENT |
| `--pick` | `--candidate N` | RENAME |
| `--duration-window` | `--min-duration 90 --max-duration 540` | REMOVE option (use shared flags) |
| `--include-live` | (no replacement; default picker handles it) | REMOVE |
| `--include-compilations` | (no replacement) | REMOVE |
| `--include-remixes` | (no replacement) | REMOVE |
| `--albums-only` | (no replacement) | REMOVE |
| `--no-singles` | (no replacement) | REMOVE |
| `--top-songs` | `--scope top-songs` | ALIAS |
| `--download` (discover-queues/albums) | default behaviour (use `--dry-run` to plan) | REMOVE flag |
| `--include-phase3` | `--no-phase3` with inverted semantics | RENAME + default flip |
| `--limit` (library) | `--top N` or `--max N` | RENAME on `library` |
| `--limit` (sync/radio) | `--limit` (max new tracks to download) | BEHAVIOUR CHANGE |

## 6. Hypotheses checked

- `--browser-profile` is redundant with `--cookies-from-browser BROWSER[:PROFILE]`: **confirmed**. The current code already splits `cookies_from_browser` and `browser_profile` into a tuple for yt-dlp; embedding the profile in the cookie-browser string is the yt-dlp native convention.
- `--include-live/--include-remixes/--include-compilations/--albums-only/--no-singles` are redundant with `--select`/picker: **mostly confirmed**. These flags only change whether tagged releases are pre-selected; the new picker already shows everything and deselects tagged releases by default. The interactive `all` selection covers the "include" case.
- `--top-songs/--scope/--top-limit` are dead/unused: **partially confirmed**. `--scope` and `--top-limit` are used for the `top-songs` artist mode, but `--top-songs` is just a `--scope` shorthand. `--top-limit` is meaningful only when `--scope top-songs`.
- `--singles-dir` is a layout setting: **confirmed**. It affects folder naming, not what is downloaded.
- `retry` duplicates `download-manifest` on failed entries: **confirmed**. `download-manifest` already handles `pending` entries and can easily grow a `--failed-only` filter.
- `discover-phase4` leaks an internal name: **confirmed**. `discover taste` is friendlier and hides the implementation phase.

## 7. Open questions for approval

1. Do you accept removing the `artist` include/exclude flags (`--include-live`, `--include-compilations`, `--include-remixes`, `--albums-only`, `--no-singles`) and relying on the picker/`all` selection?
2. Do you accept the default-flow change for `discover-queues`, `discover-albums`, and `radio` from "plan unless `--download`" to "execute after recap unless `--dry-run`"?
3. Do you accept folding `--browser-profile` into `--cookies-from-browser BROWSER[:PROFILE]`?
4. Do you accept moving `--singles-dir` and `--manifest-format` out of the CLI into env vars?
5. Do you accept the command renaming to the `discover <sub>` / `ui <sub>` / `maintenance <sub>` structure, with old names as hidden deprecated aliases?
6. Should `--limit` be kept as a display cap on `library` with a different name (e.g. `--top`), or should `library` also use a separate mechanism?
7. Should `download-one` keep `--cookies` as an explicit option, or should it also require `--cookies-from-browser`?

---

STOP — awaiting approval before Phase 1 (foundations: `cli_args.py`, validators, global-anywhere, env precedence tests).
