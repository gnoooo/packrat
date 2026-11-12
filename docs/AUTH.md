# Auth

<p align="center">
  <a href="../README.md">README</a> · <a href="INSTALL.md">Install</a> · <a href="QUICKSTART.md">Quickstart</a> · <a href="COMMANDS.md">Commands</a> · <strong>Auth</strong> · <a href="CONFIGURATION.md">Configuration</a>
</p>
<p align="center">
  <a href="TUI.md">TUI</a> · <a href="DISCOVERY.md">Discovery</a> · <a href="ARTISTS.md">Artists</a> · <a href="TROUBLESHOOTING.md">Troubleshooting</a> · <a href="DEVELOPMENT.md">Development</a> · <a href="HISTORY.md">History</a>
</p>

Packrat needs a YouTube Music **headers file** for `ytmusicapi`. 
Downloads also need a real cookie jar for age-gated or private tracks.

## Login file

```bash
packrat login
```

The command explains how to copy request headers from a logged-in `music.youtube.com` browser tab and saves them to:

```text
~/.config/packrat/browser.json
```

Use a different path:

```bash
packrat login --file /path/to/browser.json
packrat --headers /path/to/browser.json auth
```

Or set `PACKRAT_HEADERS`.

## Verify

```bash
packrat auth
```

If this fails, the headers may have expired. Run `packrat login` again.

## Delete the login file

```bash
packrat logout
packrat logout --file /path/to/browser.json
```

This removes the saved headers from disk. It cannot securely erase SSD blocks or backups; rotate the Google session if the file may have leaked.

## Doctor

```bash
packrat doctor
packrat doctor --online
```

`--online` verifies the login and runs a small yt-dlp extraction canary.

## Cookies for yt-dlp

Public tracks usually need no cookies. But private, age-restricted or bot-gated tracks do.

Recommended:

```bash
packrat sync --cookies-from-browser firefox
export PACKRAT_COOKIES_FROM_BROWSER=firefox:default-release
```

Advanced: export a Netscape `cookies.txt` and use:

```bash
packrat sync --cookies /path/to/cookies.txt
```

If neither is passed, Packrat infers the browser from the headers file's User-Agent and 
extracts that browser's cookies **once per run**. 
This is why the same login can authenticate both `ytmusicapi` and `yt-dlp`.

## Security

- Never commit `browser.json`, `cookies*.txt`, or headers files.
- They are gitignored.
- Use `packrat login` on your own machine; do not share the resulting file.

## Deno

yt-dlp uses Deno for YouTube's JavaScript challenges:

```bash
deno --version   # need >= 2.3
```

Install from <https://deno.com>. Without Deno some tracks fail with a player
or signature error.

## Legacy names (not important)
Before being pushed to this repository, Packrat was named "ytm-composer" or "ytm-offline", since the name was lacking of taste, it as been replaced by Packrat.
So old `YTM_*` environment variables are still read as fallbacks. 
The old config path `~/.config/ytmusic-composer/browser.json` is used if the new path does not exist (but it's better if not).
