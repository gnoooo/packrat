# Troubleshooting

<p align="center">
  <a href="../README.md">README</a> · <a href="INSTALL.md">Install</a> · <a href="QUICKSTART.md">Quickstart</a> · <a href="COMMANDS.md">Commands</a> · <a href="AUTH.md">Auth</a> · <a href="CONFIGURATION.md">Configuration</a>
</p>
<p align="center">
  <a href="TUI.md">TUI</a> · <a href="DISCOVERY.md">Discovery</a> · <a href="ARTISTS.md">Artists</a> · <strong>Troubleshooting</strong> · <a href="DEVELOPMENT.md">Development</a> · <a href="HISTORY.md">History</a>
</p>

Start with the setup check:

```bash
packrat doctor
packrat doctor --online
```

Then enable verbose logging:

```bash
packrat -v sync --limit 1
packrat -vv sync --limit 1
```

Logs are written under `logs/packrat-*.log`.

## Common problems

| Symptom | Fix |
|---|---|
| `Login failed` / expired headers | `packrat login`, then `packrat auth`. |
| Age-restricted / sign-in required | Use `--cookies-from-browser firefox` or `PACKRAT_COOKIES_FROM_BROWSER`. |
| 403 / 429 / bot check | Wait, use cookies, or increase `--delay`; repeated retryable errors stop the batch after 3. |
| Some songs fail without Deno | Install Deno ≥ 2.3. |
| Nothing downloads | Check `.packrat_archive`, `--limit`, `--max-size`, and the manifest. |
| Track skipped as too large | Raise or remove `--max-size`. |
| TUI colors look banded | Use tmux 3.7+ / `tmux-256color`; check the `TUI palette` log line. |
| TUI text invisible in tmux | Update Packrat; old versions exceeded tmux's 256-pair limit. |
| `terminal too small` | Use at least 40 columns × 12 rows. |
| Archive warning | Back up `.packrat_archive`; use `--force-new-archive` only if you accept re-downloading. |
| Output folder missing | It is created on first download; check `--output` / `PACKRAT_OUTPUT`. |

## Inspect a plan

```bash
packrat sync --dry-run
cat ~/Music/PackratMidden/playlists/liked.manifest.json | head
```

## Resume

```bash
packrat download-manifest
packrat download-manifest --failed-only
```

## Cookies

`browser.json` only authenticates `ytmusicapi`; it is not a cookie jar.
Packrat can infer the browser from its User-Agent and extract cookies once per run. 
Explicit options always win.

## Still stuck

Collect:

```bash
packrat --version
packrat -vv <failing command>
```

and the matching `logs/packrat-*.log` (redact secrets before sharing).
