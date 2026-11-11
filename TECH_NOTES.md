# Packrat — Phase 0 technology watch

Date: 2026-11-06  
Scope: choose and pin the stack for a YouTube Music offline downloader CLI.

## Pinned dependencies

- `ytmusicapi==1.12.3` (released 2026-09-16, requires Python >=3.10)
- `yt-dlp[default]==2026.10.26`
- `mutagen`

## Authentication

- `ytmusicapi`’s documented/recommended method is browser-header / cookie auth:  
  https://ytmusicapi.readthedocs.io/en/stable/setup/browser.html (accessed 2026-11-06)
- OAuth is brittle and now effectively requires personal Google Cloud credentials:  
  https://github.com/sigma67/ytmusicapi/discussions/682 (accessed 2026-11-06)
- **Decision:** support only browser-header auth. The user supplies a JSON headers file exported from `ytmusicapi` setup.
- `yt-dlp` **cannot** consume that headers JSON directly. It needs a Netscape `cookies.txt` or `--cookies-from-browser`.
- **Supported yt-dlp cookie path:** `--cookies-from-browser BROWSER[:PROFILE]` (yt-dlp's `BROWSER[+KEYRING][:PROFILE][::CONTAINER]` syntax). Exported `cookies.txt` files are not reliable for YouTube account auth in this setup and are no longer the recommended path. The old `--browser-profile` flag was removed in 0.2.0; use the `:PROFILE` part instead.

## Extraction runtime (yt-dlp / YouTube)

- Modern yt-dlp extraction needs an external JS runtime + `yt-dlp-ejs`:  
  https://github.com/yt-dlp/yt-dlp/issues/15012 (accessed 2026-11-06)  
  https://github.com/yt-dlp/yt-dlp-ejs/wiki (accessed 2026-11-06)
- Supported runtimes: Deno ≥2.3.0 (recommended/default), Node ≥22.0.0, QuickJS, Bun (deprecated). **This project configures Deno only** (`js_runtimes: {"deno": {}}` in `download.py` and `cli.py`).
- Environment at project root: Python 3.14.8 OK; ffmpeg 7.1.5 OK; Node v20.20.2 present but too old; Deno missing.
- Without a JS runtime the `web` client is omitted and format availability is limited. A smoke test with the `visionos` client did download a public-domain track successfully, but logged-in / Music content will likely need a runtime.
- **Decision (revised):** `download-one` refuses to run without Deno; batch downloads configure Deno and let yt-dlp fall back to its limited clients when it is missing. The gate after Phase 1 is: download one track from the user’s own library with their cookies + JS runtime before building the rest.

## PO tokens and client selection

- PO tokens are increasingly enforced for GVS/player/subtitles on some clients/contexts:  
  https://github.com/yt-dlp/yt-dlp-wiki/blob/main/PO%20Token%20Guide.md (accessed 2026-11-06)
- Current recommendation if defaults fail: use a PO Token Provider plugin (e.g., `bgutil-ytdlp-pot-provider`) together with the `mweb` client.
- **Decision:** default to whatever yt-dlp recommends today. Expose `--po-token-provider` / `--client` config as a documented fallback if 403s appear.

## Rate limits / politeness

- YouTube limits: roughly 300 videos/hour as guest, ~2000 videos/hour for accounts.
- Recommendation is a 5–10 s delay between downloads.
- **Decision:** implement per-track `--delay` (default 7 s), a persistent download archive, and exponential backoff on failures. No daily/hourly sleep logic.

## Download strategy

- Use the **yt-dlp Python API**, not per-track subprocess calls.
- Prefer native M4A/AAC; do not transcode unless the source has no suitable audio-only format.
- Output layout: `.music/<Artist>/<Album>/<title>.m4a`.
- Generate relative-path `.m3u8` playlists per source playlist.
- Support `--max-size` with up-front size estimates (best-effort from yt-dlp format metadata).
- Dry-run produces a manifest in JSON or CSV instead of downloading.

## End-to-end smoke test

- Location: temp venv `/tmp/opencode/packrat_test`
- Packages: `ytmusicapi==1.12.3`, `yt-dlp[default]==2026.10.26`, `mutagen`
- Track: `https://www.youtube.com/watch?v=WW9uzJ5xbrY` (public-domain)
- Result: M4A audio + thumbnail + metadata download succeeded with `visionos` client, no supported JS runtime installed.
- Caveat: this validates the audio pipeline, **not** logged-in YouTube Music downloads.

## Alternatives / fallback

- `yt-dlp` directly with `--cookies` can extract some YTM playlists, including the `LM` liked-music playlist, but it is far weaker for Home/Explore/Radio/library management.
- No viable replacement for `ytmusicapi` was identified.
- **Decision:** keep `ytmusicapi` as the primary navigator. Explore/Home crawling and discovery are backlog items for later phases, not Phase 1.

## Gate result (2026-11-06)

- Environment: Deno 2.9.5 is installed and meets the ≥2.3.0 requirement.
- Auth: validated with `packrat auth` using the user's `browser.json`.
- First liked-song download (`AcuMa23mhHM`) succeeded with:
  - Player client: `visionos`
  - Container/codec: M4A / AAC
  - Duration: 184.21 s
  - Tags: title, artist, album, genre, year, comment (track URL)
  - Cover art: embedded PNG
- No 403 / bot-check / missing-format errors.
- Gate passed; ready for Phase 2.

## Decisions summary

1. Auth: browser-header JSON only; yt-dlp gets cookies via `--cookies-from-browser BROWSER[:PROFILE]`. Exported `cookies.txt` is not supported as the primary path.
2. Downloads: yt-dlp Python API, one invocation per track with archive, dry-run, configurable delay, and backoff.
3. Tagging: mutagen after download.
4. JS runtime: Deno is configured for extraction; `download-one` exits when it is missing, batch commands let yt-dlp degrade to the limited clients.
5. Layout: `.music/<Artist>/<Album>/<title>.m4a` plus `.m3u8` playlists.
6. PO token provider: optional documented fallback.
7. Explore/Home discovery: deferred to later phases.

## CLI refactor decisions (2026-11-11)

- Shared option groups live in `src/packrat/cli_args.py`; deprecated names and
  old `argparse.Namespace` mappings live only in `src/packrat/cli_compat.py`.
- Global options work before or after the subcommand. Subparser copies use
  `default=argparse.SUPPRESS` so a value given before the subcommand is not
  overwritten.
- JSON is the manifest source of truth. `PACKRAT_MANIFEST_FORMAT=json+csv` (and the
  deprecated `csv` value) writes JSON plus a CSV sidecar; JSON loading is
  unchanged.
- `--cookies-from-browser` uses yt-dlp's documented
  `BROWSER[+KEYRING][:PROFILE][::CONTAINER]` syntax.
- `--cookies` and `--cookies-from-browser` are mutually exclusive on the CLI.
  A CLI value overrides an environment value of the other kind.
- `--limit` means max new tracks per run after dedupe/selection. `discover
  radio` and `discover queues` request at least 2x the limit (minimum 100) from
  the API before applying it.
- Artist top-songs mode is not reachable through the A/S picker, so `--scope`,
  `--top-limit` and `--max-per-album` were removed from the CLI.
- Deprecated aliases have a documented removal target of v0.2.0 in README.

### CLI uniformity pass

- Parent parsers are now narrow per command: headers-only commands do not accept
  cookies, cookies-only commands do not accept headers, and `--tui` only exists
  where the TUI is used.
- `download-one` only accepts `--output` plus cookie auth.
- `download-manifest --dry-run` stops after the recap; `--failed-only` selects
  entries with status `failed`.
- `-v` configures DEBUG logging.
- Artist `--min-duration`/`--max-duration` now reach the catalog filter.
- `discover taste` is plan-only; unused run-control options were removed.
- `discover sections` uses `--sources`; `--source` is a hidden alias.
- `discover taste` uses `--per-album`; `--per-container` is a hidden alias.
- Deprecated option aliases are hidden from help.
- Invalid `PACKRAT_HEADERS` exits 2 and names the variable.
- `--theme` has explicit choices.

### Clarity pass

- `normalize.py` now owns artist/album names, duration parsing, dedupe keys
  and edition stripping. `discover.py` no longer acts as a utility module.
- `artist_playlists.py` owns artist playlist writing, manifest saving and the
  artist batch download.
- `sizes.estimate_size()` is the single size estimator.
- `playlist.m3u8_entry()` is the single m3u8 entry shape.
- Removed the dead `cmd_retry`.
- README was shortened; deep TUI rendering details moved to `docs/TUI.md`.
- Old audit documents were removed to keep the tree small.
- `mutagen` is pinned.
- Added focused tests for `download.py`, `batch.py`, `sizes.py` and
  `discover_phase4.py`.

## Bug-fix pass (2026-11-11, after the layout refactor)

- `discover taste` no longer crashes with a missing `family_cap` argument; the
  cap is exposed as `--family-cap N` (default 0 = off). `_bucket_tag_search`
  now returns the same `(selected, candidates, funnel)` shape as the other
  buckets.
- Browser-cookie temp files go to the system temp directory instead of the
  hard-coded `/tmp/opencode`; the file stays 0600 and is still removed on
  exit/SIGINT/SIGTERM.
- The redaction filter mutates the `LogRecord` in place. Returning a
  replacement record only works on Python ≥3.12, while the package supports
  3.10+.
- `sync --limit` fetches the full source and applies the cap after archive
  dedupe (it used to cap the API fetch, so archived pages could hide new
  tracks). The manifest is always written as `<source>.manifest.json`;
  `json+csv` is a format, not a filename extension.
- `download-one --cookies-from-browser` parses the yt-dlp
  `BROWSER[:PROFILE]` tuple instead of passing the raw string as a browser
  name.
- `download_albums` keeps previously downloaded manifest entries when saving;
  `download_pending` rebuilds the discover playlist from the whole manifest,
  so resumed runs keep earlier tracks.
- `owned_file_exists` checks the legacy `<Artist>/<Single>/NN - title.m4a`
  layout again.
- `download-manifest` requires `--output` when the manifest is outside a
  `playlists/` folder instead of silently writing to its grandparent.
- `run_batch_download` skips video IDs already present in the archive and
  emits an `archived` event, so resuming a manifest no longer re-downloads
  tracks recorded earlier.
- `sizes.estimate_size` prefers yt-dlp's exact `filesize` over
  `filesize_approx`; `discover` now uses that shared estimator.
- `download_artist` writes progress to the manifest path it was given
  (`download-manifest --manifest <custom name>`) instead of always deriving
  `artist-<label>.manifest.json`.

## Beginner UX pass (2026-11-11)

- Added `packrat login`: it prints plain instructions, asks for the
  browser request headers through `ytmusicapi.auth.browser.setup_browser`,
  writes the login file with mode 0600, validates it, and removes a blank file
  if setup fails. `--headers`/`--file` choose the destination.
- `--help` now groups options into **Common options**, **First-time setup**,
  **Advanced**, and **Advanced display**; advanced commands such as `artist`
  and `discover taste` keep their specialist knobs under **Advanced**.
- `sync` defaults to Liked Songs, accepts a pasted playlist link or `Liked`
  in any case, and asks before downloading in a terminal (`--yes` skips).
- `download-one` accepts links as well as IDs, no longer requires cookies, and
  warns instead of blocking when Deno is missing.
- `discover`/`ui` with no subcommand print their help; developer commands are
  hidden from the main help. `resume` is an alias for `download-manifest`, and
  both use the newest saved plan when `--manifest` is omitted.
- Renamed jargon flags: `--no-phase3` -> `--skip-previous`,
  `--artist-top-k` -> `--top-artists`.
- Error messages use plain language ("Not downloading", "Saved plan not
  found", login setup steps) instead of `ValueError:`, `PACKRAT_HEADERS`, `TTY`
  and internal `discover_command` names.

## Reduction pass (2026-11-11)

- Removed `migrate-layout` (one-time maintenance), the `ui demo`/`ui glyphs`
  developer commands, the stale `docs/history/` audits, legacy option aliases,
  and the custom-RGB palette tier. The TUI itself and every download/discovery
  command remain.
- Shared one manifest-download helper for albums/queues, one bucket finisher
  for `discover taste`, and removed the dead top-songs branch, unused
  parameters and test-only helpers.
- Result: ~17,993 -> ~16,225 Python lines (about 10%), 561 -> 502 tests, with
  all remaining tests passing. Command aliases (`retry`, `radio`, ...) still
  work; option aliases were removed in 0.2.0.

## TUI redesign (2026-11-11)

- Header/footer are foreground-only text inside a rounded outer frame (no
  background bands); cards use dim borders and accent
  uppercase titles.
- Overall and now-playing bars share one renderer. Filled tiles are full blocks using the gradient as foreground (the block
  covers the cell), so the active head is a solid gradient tile; the track is
  a quarter-height (`▂`) foreground gradient. Both share one full-width
  position mapping, so the boundary is continuous and the head is not white. Hue advances one degree per tile. Inside tmux, the exact palette is set
  with OSC 4 because tmux-256color lacks `initc`. The exact palette gets 100
  HSL slots when supported (under tmux's 256-pair limit); otherwise a
  64-step xterm-256 fallback.
- Now playing uses `artist@ARTIST#ALBUM`; internal sources are rendered as
  `artist@X` / `sync@liked`, and a duplicate artist source is hidden.
- Status chips only show non-zero counters; failures are red, retries yellow,
  and the retry streak uses filled/empty dots.
- The log card is shown by default on terminals >= 30 rows and sized to its
  content; cards are centered with 2-row gaps on tall terminals.


## Auth and artist-folder fixes (2026-11-11)

- Age-gated yt-dlp failures were caused by no cookie jar reaching yt-dlp:
  `browser.json` only authenticates `ytmusicapi`, and the old `cookies.txt`
  was stale. The CLI now infers the browser from the headers User-Agent and
  extracts its cookies once per run when no explicit cookie option is given.
- Artist catalogs now force the catalog artist on every entry, so a featured
  artist listed first (e.g. FRDP: Sheena Ringo before ATARASHII GAKKO!) no
  longer owns the folder. Album discovery also canonicalizes aliases from the
  artist page (NEW SCHOOL LEADERS -> ATARASHII GAKKO!).
- ANSI colour escapes from yt-dlp are stripped before file/ring/TUI logging,
  so the log panel shows `ERROR: ...`, not `[0;31mERROR:[0m`.

## ytmusicapi channel parser workaround (2026-11-11)

`ytmusicapi==1.12.3` assumes every `musicCarouselShelfRenderer` on an artist
page has a title header. Some pages now include an untitled video carousel,
which raises `KeyError('header')` in `get_artist`. `artists.py` installs a
small parser wrapper that drops only those headerless carousel rows, so artist
catalogs keep working without patching the installed package.

## Download startup performance (2026-11-11)

- Sync and discovery no longer run `yt-dlp` extraction once per track just to
  estimate file sizes. Estimates use the duration already returned by YouTube
  Music; the real `--max-size` check happens in `download_track`, where the
  actual format size is known. This removes one full network extraction per
  track before the first download.
- Artist catalog resolution skips tracklist fetches for excluded releases in
  non-interactive downloads. Interactive pickers still resolve them because
  the picker displays their track counts.
- Kept artist releases are resolved with a small thread pool (4 clients, one
  `YTMusic` per worker). The old 0.5 s serial pause is not applied in parallel
  mode.
- Remaining download cost is dominated by per-track yt-dlp extraction
  (webpage/player/JS challenge), the default 7 s inter-track delay, and
  sequential downloads. `--delay 0` removes the deliberate pacing; parallel
  downloads are not enabled because the TUI models one active track.
