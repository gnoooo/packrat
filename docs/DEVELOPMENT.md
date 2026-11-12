# Development

<p align="center">
  <a href="../README.md">README</a> · <a href="INSTALL.md">Install</a> · <a href="QUICKSTART.md">Quickstart</a> · <a href="COMMANDS.md">Commands</a> · <a href="AUTH.md">Auth</a> · <a href="CONFIGURATION.md">Configuration</a>
</p>
<p align="center">
  <a href="TUI.md">TUI</a> · <a href="DISCOVERY.md">Discovery</a> · <a href="ARTISTS.md">Artists</a> · <a href="TROUBLESHOOTING.md">Troubleshooting</a> · <strong>Development</strong> · <a href="HISTORY.md">History</a>
</p>

## Architecture

![Packrat data flow](images/architecture.png)

```text
src/packrat/
  cli.py, cli_args.py         # CLI parsing and command dispatch
  sync.py, batch.py           # track lists, manifests, download loop
  download.py                 # one-track yt-dlp wrapper + idempotency
  integrity.py                # mutagen audio integrity check
  security.py                 # credential permissions/removal
  services/                   # credentials, diagnostics, metadata export
  artists.py                  # artist catalog + picker
  discover*.py, queues.py     # discovery planners
  tui.py, tui_theme.py        # curses renderer and palette
  archive.py, manifest.py     # state on disk
  paths.py                    # config/state locations
tests/                        # pytest suite, network blocked
docs/                         # user documentation and images
dev/hooks/                    # shared git hooks
```

## Tests

```bash
source .venv/bin/activate
python -m pytest
```

The suite blocks real network connections (`tests/conftest.py`). Use mocks or
local fixtures for anything that would hit YouTube Music.

## Releases and CI

The release workflow is [`.github/workflows/release.yml`](../.github/workflows/release.yml).
It runs on every push to `master` (and can be started manually):

1. `uv lock --check`
2. `uv sync --frozen --extra dev`
3. `uv run --frozen pytest`
4. build wheel + sdist
5. create `SHA256SUMS`
6. attest build provenance with GitHub's keyless attestation
7. create/push a tag
8. publish a GitHub release

### Stable vs snapshot releases

- If the remote does not have `v<version>` yet, the push creates the **stable**
  release `v<version>`.
- Otherwise the push creates a **prerelease snapshot**
  `v<version>.dev<run-number>+<short-sha>`.

Bump the version and lockfile together:

```bash
./dev/bump-version.sh 0.4.0
git add pyproject.toml uv.lock
git commit -m "release: 0.4.0"
git push github master
```

The workflow uses `uv.lock`; commit it whenever dependencies or the project
version change.

### Version/tag guard

The shared hooks include a `pre-push` check:

```bash
./dev/hooks/install.sh
```

It rejects a manually pushed `v*` tag when:

- the tag does not match the `pyproject.toml` version (stable tags must match
  exactly; prerelease tags must share the base version);
- `uv.lock` is stale or has a different `packrat` version;
- the tag already exists on the remote.

You can run the same check directly:

```bash
python3 dev/check_version.py v0.4.0
```

The release workflow repeats the tag/version check, so a bypassed local hook
still cannot publish an inconsistent stable tag.

### Provenance

No long-lived signing key is required. The workflow uses GitHub's keyless build-provenance attestation:

```yaml
permissions:
  contents: write
  id-token: write
  attestations: write
```

After a release, verify an artifact with:

```bash
gh attestation verify dist/packrat-<version>-py3-none-any.whl \
  --repo gnoooo/packrat
```

`SHA256SUMS` is still published for simple integrity checks. 
A checksum is not an authenticity guarantee; the GitHub attestation is the provenance record.
No GPG secrets or public key file are needed.

### Release artifacts

```text
packrat-<version>-py3-none-any.whl
packrat-<version>.tar.gz
SHA256SUMS
```

Tags and releases are immutable; never move a published tag.

## Local bare mirror

In my configuration, I have a local cloud with Nextcloud, and it can be messy syncing a `.git` folder. 
That's why the `local` remote points at `~/Nextcloud/Documents/git/packrat.git`. 
Shared hooks mirror commits, amended history and merges in the background:

```bash
./dev/hooks/install.sh
```

`core.hooksPath` becomes `dev/hooks`. A failed push is silent; the next commit
retries all branches. Tags are not mirrored.

## Documentation

- [README.md](../README.md): landing page and navbar
- `docs/*.md`: focused guides
- `docs/images/`: TUI screenshots and architecture diagram

## Verification and credentials

- `integrity.verify_audio()` checks size, parseability and duration before a file is treated as complete.
- `download_track()` skips a valid existing destination and quarantines invalid files before retrying.
- `security.secure_file()` / `secure_directory()` enforce owner-only modes; `logout` removes saved headers.
- `doctor` is the regression canary for YouTube/yt-dlp changes.

## Performance notes

- Sync/discovery use duration-based size estimates; they do not run yt-dlp
  once per track before downloading.
- Artist catalogs resolve kept releases with a small worker pool and skip
  excluded releases for non-interactive downloads.
- Downloads are still sequential; the default 7 s delay is deliberate pacing.

## Release checklist

```bash
python -m pytest
packrat --version
git status
```

Keep credentials, `~/Music/PackratMidden/`, `~/.config/packrat/archive` and logs out of commits.
