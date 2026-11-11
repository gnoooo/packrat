# ytm_offline — Phase 0 technology watch

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
- **Supported yt-dlp cookie path:** `--cookies-from-browser firefox` (with optional `--browser-profile <name>`). Exported `cookies.txt` files are not reliable for YouTube account auth in this setup and are no longer the recommended path.

## Extraction runtime (yt-dlp / YouTube)

- Modern yt-dlp extraction needs an external JS runtime + `yt-dlp-ejs`:  
  https://github.com/yt-dlp/yt-dlp/issues/15012 (accessed 2026-11-06)  
  https://github.com/yt-dlp/yt-dlp-ejs/wiki (accessed 2026-11-06)
- Supported runtimes: Deno ≥2.3.0 (recommended/default), Node ≥22.0.0, QuickJS, Bun (deprecated).
- Environment at project root: Python 3.14.8 OK; ffmpeg 7.1.5 OK; Node v20.20.2 present but too old; Deno missing.
- Without a JS runtime the `web` client is omitted and format availability is limited. A smoke test with the `visionos` client did download a public-domain track successfully, but logged-in / Music content will likely need a runtime.
- **Decision:** for real batch downloads, warn/exit unless a supported JS runtime is available. The gate after Phase 1 is: download one track from the user’s own library with their cookies + JS runtime before building the rest.

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
- Output layout: `music/<Artist>/<Album>/<title>.m4a`.
- Generate relative-path `.m3u8` playlists per source playlist.
- Support `--max-size` with up-front size estimates (best-effort from yt-dlp format metadata).
- Dry-run produces a manifest in JSON or CSV instead of downloading.

## End-to-end smoke test

- Location: temp venv `/tmp/opencode/ytm_test`
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
- Auth: validated with `ytm-offline auth` using the user's `browser.json`.
- First liked-song download (`AcuMa23mhHM`) succeeded with:
  - Player client: `visionos`
  - Container/codec: M4A / AAC
  - Duration: 184.21 s
  - Tags: title, artist, album, genre, year, comment (track URL)
  - Cover art: embedded PNG
- No 403 / bot-check / missing-format errors.
- Gate passed; ready for Phase 2.

## Decisions summary

1. Auth: browser-header JSON only; yt-dlp gets cookies via `--cookies-from-browser firefox` (optional `--browser-profile`). Exported `cookies.txt` is not supported as the primary path.
2. Downloads: yt-dlp Python API, one invocation per track with archive, dry-run, configurable delay, and backoff.
3. Tagging: mutagen after download.
4. JS runtime: required for real batch work; warn/exit otherwise.
5. Layout: `music/<Artist>/<Album>/<title>.m4a` plus `.m3u8` playlists.
6. PO token provider: optional documented fallback.
7. Explore/Home discovery: deferred to later phases.

## CLI refactor decisions (2026-11-11)

- Shared option groups live in `src/ytm_offline/cli_args.py`; deprecated names and
  old `argparse.Namespace` mappings live only in `src/ytm_offline/cli_compat.py`.
- Global options work before or after the subcommand. Subparser copies use
  `default=argparse.SUPPRESS` so a value given before the subcommand is not
  overwritten.
- JSON is the manifest source of truth. `YTM_MANIFEST_FORMAT=json+csv` (and the
  deprecated `csv` value) writes JSON plus a CSV sidecar; JSON loading is
  unchanged.
- `--cookies-from-browser` uses yt-dlp's documented
  `BROWSER[+KEYRING][:PROFILE][::CONTAINER]` syntax. `--browser-profile` is a
  deprecated fold-in alias.
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
- `migrate-layout` has its own parser and a visible `--undo`.
- `discover sections` uses `--sources`; `--source` is a hidden alias.
- `discover taste` uses `--per-album`; `--per-container` is a hidden alias.
- Deprecated option aliases are hidden from help.
- Invalid `YTM_HEADERS` exits 2 and names the variable.
- `--theme` has explicit choices.
