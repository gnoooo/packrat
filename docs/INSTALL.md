# Install

<p align="center">
  <a href="../README.md">README</a> · <strong>Install</strong> · <a href="QUICKSTART.md">Quickstart</a> · <a href="COMMANDS.md">Commands</a> · <a href="AUTH.md">Auth</a> · <a href="CONFIGURATION.md">Configuration</a>
</p>
<p align="center">
  <a href="TUI.md">TUI</a> · <a href="DISCOVERY.md">Discovery</a> · <a href="ARTISTS.md">Artists</a> · <a href="TROUBLESHOOTING.md">Troubleshooting</a> · <a href="DEVELOPMENT.md">Development</a> · <a href="HISTORY.md">History</a>
</p>

## Requirements

- **Python 3.11+**
- **pip**
- **ffmpeg**: used by yt-dlp for m4a/metadata/thumbnail post-processing.
- **Deno ≥ 2.3**: recommended; yt-dlp uses it for YouTube's JavaScript challenges. Without it some tracks fail.
- Optional: **tmux 3.7+** for the exact TUI palette via OSC 4.

## Recommended: install script

From the repository:

```bash
./install.sh
```

The script is Linux-only for now. It uses **uv** when available and falls back
to `python -m venv` + `pip`. It installs into:

```text
~/.local/share/packrat/venv
~/.local/bin/packrat
```

and also keeps a compatibility `ytm-offline` link. Re-running the script replaces the
venv atomically; it never touches your music library, archive, config or logs.

```bash
./install.sh --git https://github.com/you/packrat.git
./install.sh --prefix ~/.local/share/packrat --bin-dir ~/.local/bin
./install.sh --dry-run
```

External tools are **detected, never installed**:

- ffmpeg/ffprobe: required; the script stops with distro-specific instructions.
- Deno ≥ 2.3: detected; a warning is printed if missing/old.
- uv: if absent, pip is used and the uv install URL is printed.

If `~/.local/bin` is not on your PATH, the script prints the exact export line.

## Remote install with curl

The easiest path:

```bash
curl -fsSL https://raw.githubusercontent.com/gnoooo/packrat/refs/heads/master/install.sh | bash
```

A piped install defaults to `https://github.com/gnoooo/packrat.git`. Override it for a fork or a pinned branch:

```bash
curl -fsSL https://raw.githubusercontent.com/gnoooo/packrat/refs/heads/master/install.sh | bash -s -- --git https://github.com/you/packrat.git --ref dev
```

Preview without installing:
```bash
curl -fsSL https://raw.githubusercontent.com/gnoooo/packrat/refs/heads/master/install.sh | bash -s -- --dry-run
```

Safer: download and inspect it first:

```bash
curl -fsSL https://raw.githubusercontent.com/gnoooo/packrat/refs/heads/master/install.sh -o install.sh
less install.sh
bash install.sh --git https://github.com/gnoooo/packrat.git
```

Piping a remote script executes unreviewed code. The installer does not
verify a checksum or signature. Prefer a pinned commit/tag with `--ref` when
using this path.

## Manual install

```bash
git clone https://github.com/gnoooo/packrat.git packrat
cd packrat
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

`pip install -e .` installs the `packrat` command and the `python -m packrat`
entry point.

## Verify

```bash
packrat --version
packrat --help
packrat doctor
```

## First run

```bash
packrat login        # save browser request headers
packrat auth         # verify the login
packrat sync --limit 1 --dry-run
packrat sync --limit 1
```

The first real download creates `~/Music/PackratMidden/`. Download state lives
under `~/.config/packrat/` (`archive`, `runs.jsonl`, `profile.json`, `logs/`). See [Quickstart](QUICKSTART.md) for a complete first run.

## Updating

For a standard installer install:

```bash
packrat upgrade --check
packrat upgrade
```

`upgrade` requires `gh` installed and authenticated (`gh auth login`, or
`GH_TOKEN`) for attestation verification. It replaces only the
application venv and command link; the music library and
`~/.config/packrat/` state are preserved.

With the install script:

```bash
git pull
./install.sh
```

The script rebuilds the venv and swaps it atomically.

Manual editable install:

```bash
git pull
pip install -e .
```

## Uninstall

With the default script layout:

```bash
rm -rf ~/.local/share/packrat
rm -f ~/.local/bin/packrat ~/.local/bin/ytm-offline
```

This removes the application only. Your music library, `~/.config/packrat/archive`,
config and logs are preserved.

For a manual venv install, deactivate it and remove the venv; use
`pip uninstall packrat` only if you installed it into a shared environment.
