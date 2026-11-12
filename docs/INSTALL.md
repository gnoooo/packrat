# Install

<p align="center">
  <a href="../README.md">README</a> · <strong>Install</strong> · <a href="QUICKSTART.md">Quickstart</a> · <a href="COMMANDS.md">Commands</a> · <a href="AUTH.md">Auth</a> · <a href="CONFIGURATION.md">Configuration</a>
</p>
<p align="center">
  <a href="TUI.md">TUI</a> · <a href="DISCOVERY.md">Discovery</a> · <a href="ARTISTS.md">Artists</a> · <a href="TROUBLESHOOTING.md">Troubleshooting</a> · <a href="DEVELOPMENT.md">Development</a> · <a href="HISTORY.md">History</a>
</p>

## Requirements

- **Python 3.10+**
- **pip**
- **ffmpeg**: used by yt-dlp for m4a/metadata/thumbnail post-processing.
- **Deno ≥ 2.3**: recommended; yt-dlp uses it for YouTube's JavaScript challenges. Without it some tracks fail.
- Optional: **tmux 3.7+** for the exact TUI palette via OSC 4.

## From source

```bash
git clone <your-packrat-remote> packrat
cd packrat
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

`pip install -e .` installs the `packrat` command and the `python -m packrat` entry point.

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

The first real download creates `.music/` and `.packrat_archive` in the
current directory. See [Quickstart](QUICKSTART.md) for a complete first run.

## Updating

```bash
cd packrat
git pull
pip install -e .
```

## Uninstall

```bash
pip uninstall packrat
```

Your music and download history are ordinary files and are not removed by
`pip uninstall`.
