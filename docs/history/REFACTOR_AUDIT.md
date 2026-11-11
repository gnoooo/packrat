# REFACTOR_AUDIT.md

Generated during Step 0 audit on branch `refactor`.  
Baseline: 37 tests passing (`/tmp/opencode/ytm_offline_venv/bin/python -m pytest -q`).  
No code changes have been made yet.

---

## 1. Module map

| file | lines | responsibility | imported by |
|------|-------|----------------|-------------|
| `src/ytm_offline/sizes.py` | 44 | `parse_size`, `format_size`, `heuristic_size` (constant 16 000 B/s estimate) | artists, albums, queues, sync, discover, discover_phase4, batch, cli, download |
| `src/ytm_offline/naming.py` | 57 | filename sanitizing (`clean`), `track_filename`, `track_path`, `parse_playlist_id` | albums, artists, download, sync, queues |
| `src/ytm_offline/playlist.py` | 30 | `write_m3u8` (relative paths, mkdir parent) | albums, artists, sync, queues, discover |
| `src/ytm_offline/archive.py` | 55 | `Archive` set of video ids, atomic save via temp+replace | batch, artists, albums, queues, sync, discover, discover_phase4 |
| `src/ytm_offline/manifest.py` | 96 | `Manifest` class, JSON/CSV write, counts | sync, albums, queues, discover, discover_phase4 |
| `src/ytm_offline/logging_config.py` | 107 | `RedactFilter`, `RingBufferHandler`, `redact()`, `setup_logging()` | download, tui |
| `src/ytm_offline/control.py` | 58 | `Control` pause/resume/skip/stop/wait | batch, tui |
| `src/ytm_offline/events.py` | 181 | `Event`, `CurrentTrack`, `State`, apply/snapshot, ETA | tui, batch, download |
| `src/ytm_offline/batch.py` | 169 | canonical `run_batch_download` (sequential, events, save callback, archive, 3-retryable stop) | sync, albums, artists, queues, sync |
| `src/ytm_offline/download.py` | 346 | yt-dlp wrapper, retries, max-size skip, postprocessors, `_YtdlpLogger` | sync, batch, discover |
| `src/ytm_offline/sync.py` | 336 | liked/library/playlist sync, retry, browser-cookie caching, m3u8 output | cli |
| `src/ytm_offline/discover.py` | 716 | Home/Charts/Moods/New Releases discovery, `download_pending` (duplicates batch logic) | cli, discover_phase4 |
| `src/ytm_offline/discover_phase4.py` | 836 | taste-adjacent buckets (deep cuts, adjacent, radio, Last.fm graph/tags), scoring/caps | cli |
| `src/ytm_offline/albums.py` | 376 | album discovery from top liked artists, edition picking | cli |
| `src/ytm_offline/queues.py` | 528 | queue/radio discovery from liked seeds, `parse_video_id` | cli |
| `src/ytm_offline/artists.py` | 883 | artist catalog discovery + `--top-songs`, video-id resolution, playlist writing | cli |
| `src/ytm_offline/lastfm.py` | 65 | `LastFMClient` (LASTFM_API_KEY) | discover_phase4 |
| `src/ytm_offline/tui.py` | 467 | curses UI driven by `events.State` | cli |
| `src/ytm_offline/cli.py` | 1035 | argparse dispatcher, thin command handlers | `__main__` |
| `src/ytm_offline/__init__.py` | 3 | package marker | — |
| `src/ytm_offline/__main__.py` | 4 | `cli.main()` entry point | — |

Tests: `tests/test_albums.py` (86), `tests/test_artists.py` (165), `tests/test_queues.py` (63), `tests/test_sync.py` (97), `tests/test_tui.py` (61).

---

## 2. Duplicated logic

| # | what | locations | note |
|---|------|-----------|------|
| 1 | Edition/base-title stripping + edition-word list | `albums.py:28` `_EDITION_WORDS`, `albums.py:41` `_UNWANTED_ALBUM_WORDS`, `albums.py:52-58` `_album_base_title` vs `artists.py:36` `_EDITION_WORDS`, `artists.py:220-225` `_base_title` | Same regex `re.sub(r"[([].*?[])\]", "", title)` and almost identical word tuples. `albums.py` adds `revisited`, `re-recording`; `artists.py` adds `remix`. Should be one `normalize.py` function. |
| 2 | Track-key dedupe `(normalize(artist), normalize(title))` | `discover.py:96` `_track_key`, `albums.py:100/105/226/256`, `queues.py:99/137/243/251`, `artists.py:329/413/599/627`, `discover_phase4.py:64` | Every command rebuilds the same key. |
| 3 | Normalizing a raw ytmusicapi track into internal dict | `sync.py:28` `_normalize_track`, `discover.py:132` `_to_track`, `queues.py:146-158` inline dict, `artists.py:498-509` `resolve_release_tracks`, `albums.py:284-291` `manifest.add(...)` inline | Field names differ (`videoId` vs `video_id`, `duration_seconds` vs `duration`, `trackNumber` vs `track_num`). |
| 4 | Size estimation from yt-dlp info | `download.py:148` `_estimate_size` vs `sync.py:86` `_estimate_size` | Identical bodies. |
| 5 | Constant heuristic size | `sizes.py:42` `heuristic_size` (16000 B/s) vs `discover_phase4.py:33/71` `_EST_BYTES_PER_SECOND`/`_heuristic_size` | Same constant redefined. |
| 6 | m3u8 entry dict shape `{path, duration, title, artist}` | `albums.py:267-273`, `sync.py:104-114` `_m3u8_from_entries`, `queues.py:263-270`, `discover.py:676-682`, `artists.py:767-772` | Same dict constructed repeatedly. |
| 7 | Duration parsing `H:MM:SS` → seconds | `discover.py:41` `_parse_duration_string` vs `artists.py:487` `_parse_duration` | Slightly different; only `artists.py` handles the empty/invalid case explicitly. |
| 8 | Video-id / URL parsing | `queues.py:374-387` `parse_video_id` (bare id only) vs `artists.py:52/108-124` `_VIDEO_ID_RE`/`looks_like_video_id`/`extract_video_id`/`resolve_video` (URL-aware, resolves artist) | Overlapping but different regexes and behaviour. |
| 9 | Subscriber-count parsing | `artists.py:55` `parse_subscribers` | Only one copy, but the parser relies on raw strings that ytmusicapi no longer returns as full text; needs robust fallback. |
| 10 | Manifest JSON write outside `Manifest` class | `artists.py:800-802` `json.dumps(payload, indent=2)` + `write_text`; `sync.py:158` `json.dumps({"entries": current_entries}, ...)`; `sync.py:100` `json.dumps(video_ids, ...)`; `discover_phase4.py:229` profile json | `Manifest.write` exists but is not always used. |
| 11 | `output_root.mkdir(parents=True, exist_ok=True)` | `cli.py:196/278/351/508/665/730`, `sync.py:138/199/277`, `albums.py:179/307`, `queues.py:192/261/304/473`, `artists.py:403/586/776/800`, `discover_phase4.py:228/655`, `manifest.py:77`, `archive.py:36` | Repeated everywhere. |
| 12 | Retry/stop/3-consecutive-retryable rule | `batch.py:25` canonical `run_batch_download` vs `discover.py:617-716` `download_pending` | `discover.py` reimplements the loop inline with `print`s. |
| 13 | Browser cookie caching | `sync.py:86-101` `_cache_browser_cookies` | Only used by `cmd_discover`; other commands rely on yt-dlp `--cookies-from-browser` directly. |
| 14 | Format-bytes/duration helpers | `tui.py` `format_bytes`, `format_duration` vs `sizes.py` `format_size` | `tui.format_bytes` duplicates byte formatting. |

---

## 3. Dead code, stubs, unused options

| location | issue | action |
|----------|-------|--------|
| `artists.py` `resolve_candidates` (~127-135) | Function is defined but `cmd_artist` now inlines its own resolution logic; orphaned. | Remove or reuse in CLI. |
| `artists.py` `owned_key_misses` (~468-478) | Was a stub; now a thin disk-check wrapper around `owned_file_exists`. | Keep after refactor but consolidate into selection/owned module. |
| `cli.py` `cmd_download` (~stub) | Returns 1 with no implementation. | Remove or document as TODO. |
| `cli.py` `cmd_discover_phase4` hardcodes `dry_run=True` (line 377) even though parser accepts `--dry-run` | Command only supports dry-run; either remove the flag or support execution. | Clarify behavior. |
| `discover.py` `download_pending` | Duplicates batch logic and uses `print`. | Replace with `run_batch_download` or pipeline. |
| `discover.py` | Uses `sys.stderr` at line 690 but never imports `sys` at module top (`import sys` is missing). | Bug; fix immediately. |
| `albums.py` `_album_track_path` (if still present) | Thin wrapper around `track_path`; check whether it is used. | Remove if unused. |
| `queues.py` `build_radio` | Possibly shares logic with `discover_queues`; verify overlap. | Merge if duplicate. |
| `_NEEDS_HEADERS` in `cli.py` | Does not include `download-one`/`download`, but those handlers access `args.headers`. | Add or default safely. |

---

## 4. Inconsistencies

| area | current state | target |
|------|---------------|--------|
| **Output paths / manifest filenames** | `albums.manifest.json`, `queues.manifest.json`, `discover.manifest.json`, `artist-{label}.manifest.json`, `radio-{label}.manifest.json`, `{source}.manifest.{fmt}` | One naming convention, one `manifest.py` writer. |
| **Logging vs print** | `sync.py` uses logger; `discover.py` `download_pending` uses `print`; CLI handlers use `print`; batch uses events. | Route all output through logging or a single output helper. |
| **Return types** | `run_sync`/`run_retry` return `int`; `discover_albums`/`discover_queues`/`build_catalog` return dicts; `batch.py` returns dict. | Standardize on result objects or small dataclasses. |
| **Path types** | Mostly `pathlib`, but some `str(path)` conversions leak into manifest entries. | Keep `pathlib` until serialization. |
| **Time/size estimates** | `sizes.heuristic_size` = 16 kB/s; `batch`/`events` track real rate for ETA; `sync` uses yt-dlp `filesize_approx`; no unified throughput estimate. | Single estimator that uses measured throughput (Step 1 characterization + archive timestamps). |
| **Command flow** | `sync`/`albums`/`queues`/`artist` write manifest and may download in one run; `discover` writes manifest but `download_pending` is separate; `radio` can do both; `discover-phase4` dry-run only. | Every discovery command → plan/dry-run manifest → `download-manifest <path> [--yes] [--tui]`. |
| **Flag meanings** | `--limit` differs per command; `--download` required for queues/albums but not others; `--dry-run`/`--tui` inconsistent. | Same flag means same thing everywhere. |
| **Video-id input** | `queues.py` accepts bare ids; `artists.py` accepts URLs + bare ids + resolves to artist. | One parser in `normalize.py`/`api.py`. |
| **Field names** | Raw `videoId`/`duration_seconds` vs internal `video_id`/`duration`. | One model schema in `models.py`. |
| **Subscriber display** | `? subscribers` was shown when subscriber text missing; now backfilled but still inconsistent. | Explicit "subscribers unknown" everywhere. |

---

## 5. Risk list

These places can silently change behavior when refactored:

1. **Dedupe key order** — `albums.py`, `queues.py`, `artists.py` currently seed `owned_keys` in different orders (e.g. top-songs first in artist). Changing order changes which duplicate is kept.
2. **Edition stripping** — Consolidating `_album_base_title` and `_base_title` could change which album variant is selected if word lists are merged naively.
3. **Track normalization** — ytmusicapi rows have many shapes; a single `_to_track` must reproduce every current field extraction exactly.
4. **Duration parsing** — Replacing the two duration parsers with one must handle `duration_seconds` int, `"3:41"`, `"6+ hours"`, and `None` exactly as current code does.
5. **m3u8 relative paths** — `playlist.py` computes paths relative to the playlist file; changing base dir logic breaks existing playlists.
6. **Archive atomic save** — `archive.py` uses temp+replace; any refactor must preserve atomicity to avoid corruption on interrupt.
7. **3-retryable stop rule** — Moving `discover.py:download_pending` to `batch.py` must preserve the exact stop/reset semantics.
8. **Cookie redaction** — `logging_config.py` mutates `record.msg` and `record.args`. Replacing with a safer filter must not leak cookies in TUI/log ring buffer.
9. **Artist top-songs merge** — `build_catalog` currently puts top entries first and seeds `owned_keys` from them. Changing this would re-download top songs in `--scope all`.
10. **Subscriber ranking** — `search_artists` now backfills from `get_artist`; removing this backfill would change candidate ordering and `--pick` recommendations.

---

## 6. Test coverage gaps

| area | gap |
|------|-----|
| `download.py` | No tests for yt-dlp option building, retries, max-size skip, postprocessors, redaction. |
| `batch.py` | No tests for stop-after-3-retryable, event emission, save-callback ordering, resume after interruption. |
| `cli.py` | No tests for argument parsing, `_NEEDS_HEADERS`, command wiring, dry-run approval flow, video-id interpretation. |
| `discover.py` | No direct tests; heavy broad `except Exception` paths not exercised. |
| `discover_phase4.py` | No tests; scoring/cap logic not exercised. |
| `albums.py` | Only one happy-path smoke test; edition picking not tested. |
| `sync.py` | Run paths (liked/playlist) not tested; retry/resume not tested. |
| `manifest.py` | CSV write, load, pending filter not tested. |
| `archive.py` | Save failure, update/add, atomicity not tested. |
| Golden/snapshot | No checked-in expected manifest/m3u8 output. |
| Redaction | Only one `redact()` test; no test for cookie path/header redaction in manifests/logs/TUI. |
| Resume | No test that a rerun after a partial download skips already-downloaded files and continues the manifest. |

---

## 7. Target architecture

Keep it simple: no framework, no new runtime dependencies, one responsibility per module.

| module | responsibility |
|--------|----------------|
| `models.py` | `Track`, `Release`, `Artist`, `ManifestEntry`, `RunState` dataclasses/TypedDicts. One manifest schema, version field. |
| `normalize.py` | Single source of truth: artist/title normalization, filename sanitizing, edition/version stripping, duplicate key, video-id parsing. |
| `api.py` | ytmusicapi wrapper: auth, retries, pagination, response parsing. Converts raw API responses into `models`. All field access to raw dicts lives here. |
| `archive.py` | Keep current `Archive` (set + atomic save). Maybe add `update()` helper. |
| `manifest.py` | Keep current `Manifest`; ensure all commands use it; add schema version. |
| `playlists.py` | m3u8 generation only (extract from `playlist.py` + inline dict builders). |
| `sizes.py` | Keep `parse_size`/`format_size`; add throughput-based estimator in Step 1. |
| `sources/` | One module per discovery source: `liked.py`, `playlist.py`, `home.py`, `charts.py`, `moods.py`, `albums.py`, `queues.py`, `radio.py`, `artist.py`, `phase4.py`. Each returns a list of `Track` candidates with provenance. No downloading, no printing. |
| `selection.py` | Pure functions: music-only filter, duration window, dedupe vs owned/archive, per-artist/album/queue caps, quotas. |
| `downloader.py` | The only code that calls yt-dlp: option building, cookies, JS runtime, progress hooks, backoff, atomic writes, tagging, ffprobe checks. |
| `pipeline.py` | One `run(pipeline_id, manifest, **kwargs)` used by every command: estimate -> confirm -> download -> update archive/manifest/m3u8 incrementally -> report. |
| `tui.py` | Curses UI reading `events.State`; no downloader code prints; plain logging mode remains default. |
| `cli.py` | Argparse only; thin handlers: `sources -> selection -> pipeline`. Unify flag names and help text. |
| `config.py` | Paths, defaults, env vars (`YTM_HEADERS`, `YTM_COOKIES`, `LASTFM_API_KEY`) in one place; central redaction helper. |
| `logging_config.py` | One `setup_logging()`; redaction filter everywhere; replace stray `print()` with logging or an explicit output function. |

---

## 8. Migration plan (approved variant)

User-approved changes to the original plan:

- `download-manifest <manifest> [--yes] [--tui]` is implemented **right after the Step 1 safety net**, before any other refactors, as small standalone commits. It must read every existing manifest format (queues, albums, artist, discover, phase4, liked/sync/radio) without regenerating dry-runs and be resumable.
- Step order is: **Step 1 (safety net) → Step 2 (normalize.py) → Step 4 (selection+owned-dedupe) → Step 5 (pipeline) → Step 6 (CLI unification) → Step 7 (defect fixes)**. The `api.py` + `models.py` conversion (original Step 3) is **last and optional**; do it only if the rest is green and stable, otherwise keep raw-dict handling unchanged. No big-bang rewrite.
- Risks #1 and #9 get **characterization tests before they are touched**: owned-key seeding order per command, and `artist --scope all` not re-downloading top songs.
- Throughput estimate uses a small run-history file (`runs.jsonl`: tracks, seconds, bytes per run) because `Archive` only stores video ids; keep Archive format backward compatible and show "~N min (X s/track)", falling back to an explicit labeled default when no history exists.
- Manifest schema versioning: add a `version` field, load old manifests with missing version as v1, add a test.
- Security: temp Netscape cookies file from `_cache_browser_cookies` must be created with `0600` permissions and deleted on Ctrl-C/crash (try/finally + signal handling); clarify which commands use it and make them consistent; add redaction tests for tracebacks, manifests, log files and TUI strings.
- Plan-only commands (`discover-phase4`, `discover`) produce a manifest only and are executed via `download-manifest`; remove the hardcoded `dry_run=True` confusion in `cmd_discover_phase4` and remove/alias the unimplemented `cmd_download` stub.
- Keep the `import sys` fix and dead-code removal as the very first small commits.

Each bullet below is an atomic commit that must keep the test suite green.

### Step 0 — First small commits (no behavior change)
1. Fix the missing `import sys` in `discover.py` (line ~690) as a standalone bug-fix commit.
2. Remove dead code: orphaned `artists.resolve_candidates` and unimplemented `cli.cmd_download` stub (replace `download` subparser with `download-manifest` later).

### Step 1 — Safety net (before moving code)
3. Run tests, record baseline (37 passing).
4. Add characterization tests for normalization, dedupe, naming/layout, manifest schema, m3u8 content, archive semantics, estimate math, CLI parsing, redaction.
5. Add targeted tests for risk #1 (owned-key seeding order per command) and risk #9 (artist `--scope all` does not re-download top songs).
6. Add tests that old manifests, archive and m3u8 from a copy of the existing `music/` folder still load identically.
7. Add manifest-version test: missing version loads as v1.
8. Generate golden manifest/m3u8 files from a fully mocked run of each command in dry-run mode and check them in.

### Step 1b — `download-manifest` command
9. Implement `download-manifest <manifest> [--yes] [--tui]` that reads queues/albums/artist/discover/phase4/liked/radio manifests and resumes download via `run_batch_download` (or the existing type-specific download function where one exists). Keep existing playlist layouts where possible.
10. Add tests + smoke commands for `download-manifest` on each manifest type.

### Step 2 — Normalize + models (pure refactor, no behavior change)
11. Create `normalize.py`: `_normalize`, `clean_filename`, `track_key`, `strip_edition`, `parse_video_id`, `looks_like_video_id`, `extract_video_id`, `parse_subscribers`, `parse_duration`.
12. Replace duplicated edition/base-title code in `albums.py`/`artists.py` with `normalize.strip_edition`.
13. Replace duplicated track-key construction everywhere with `normalize.track_key`.
14. Replace duration parsing in `discover.py` and `artists.py` with `normalize.parse_duration`.
15. Replace video-id parsing in `queues.py` and `artists.py` with `normalize.parse_video_id`/`looks_like_video_id`.

### Step 4 — Selection + owned-dedupe
16. Create `selection.py` with `filter_music_only`, `duration_window`, `dedupe`, `cap_per_artist`, `cap_per_album`, `apply_quota`.
17. Move owned-key / archive checks from `albums.py`, `artists.py`, `queues.py`, `discover.py` into `selection.py`.
18. Update `artist` top-songs vs catalog merge to use `selection.dedupe`.

### Step 5 — Pipeline
19. Merge `download.py` `_estimate_size` and `sync.py` `_estimate_size` into `downloader.py`/`sizes.py`.
20. Replace `discover.py:download_pending` with `pipeline.py` / `run_batch_download`.
21. Create `pipeline.py` `run_plan()` and `run_download()`; migrate `sync`, `albums`, `queues`, `artist`, `radio`, `discover` to call it.
22. Add measured-throughput estimate using `runs.jsonl` in `sizes.py`/`pipeline.py`.

### Step 6 — CLI unification
23. Unify common flags (`--max-size`, `--delay`, `--dry-run`, `--yes`, `--limit`) in one parent parser.
24. Change discovery commands to produce manifests only; remove per-command `--download` switches from `discover-queues`/`discover-albums`/`radio`.
25. Alias or remove the `download` subparser; keep `download-manifest` as the single execution path.
26. Make temp cookie caching consistent across all commands that need it (sync, retry, discover, etc.) with `0600` permissions and robust cleanup.

### Step 7 — Known defect fixes (each in its own commit)
27. Fix #1: `artist` processes exactly one target by default; extra targets require `--also` or `--from-file`; print "Will process: ..." and confirm before resolving.
28. Fix #2: one source of truth for new/owned counts; reconcile per-release lines with summary totals; top-songs uses same disk+archive dedupe.
29. Fix #3: time estimate from measured throughput, shown as "~N min (X s/track)".
30. Fix #4: subscriber counts parse real format or fall back explicitly; no "?" ranking.
31. Fix any other clearly-buggy behavior found during migration.

### Step 8 — Static checks + docs
32. Add ruff config to `pyproject.toml` and a `[dev]` extras group with `ruff` and a lenient type checker (pyright or mypy).
33. Run ruff, fix findings; run type checker, document remaining issues.
34. Update `README.md` with commands, flags, architecture overview.
35. Update `TECH_NOTES.md` with new architecture, throughput estimation and run-history notes.
36. Final full test run, golden-output diff, and report.

### Step 9 — Optional API/models conversion (only if green and stable)
37. Create `models.py` with `Track`, `Release`, `Artist`, `ManifestEntry`, `RunState`.
38. Optionally create `api.py` wrapper around `YTMusic` with retry/pagination and raw-to-model conversion.
39. Convert source modules to use models only if it reduces complexity; otherwise leave raw-dict handling unchanged.

---

## 9. Known defects to fix during migration

Per the task description, these are allowed behavior changes and must be fixed in isolated commits with tests:

1. **`artist` single target by default** — extra positional targets should require `--also` or `--from-file`; print the interpreted target(s) and confirm before resolving the catalog.
2. **Count reconciliation** — per-release "0 new" lines vs summary totals must agree; one source of truth for `new`/`owned`/`in-run duplicates`. Top-songs must use the same normalized artist/title dedupe vs disk + archive.
3. **Throughput-based time estimate** — estimate must use measured throughput from previous runs (archive timestamps or rolling average), displayed as "~N min (X s/track)".
4. **Subscriber / "?" fields** — parse the real ytmusicapi format (e.g. "3.86M") and fall back explicitly to "subscribers unknown"; ranking must not depend on "?".
5. **Other bugs found** — e.g. `discover.py` missing `import sys` (line 690); list and fix each in its own commit.

---

## 10. Immediate next steps

1. Approve this audit.
2. I will start Step 1 (safety net + characterization tests + golden snapshots) on the `refactor` branch.
3. Each subsequent step will be reported briefly before continuing.
