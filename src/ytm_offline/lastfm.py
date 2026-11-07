"""Minimal Last.fm client for optional external-graph discovery."""

from __future__ import annotations

import os
import time
from typing import Any

import requests


class LastFMClient:
    """Thin wrapper around Last.fm API v2.0.

    Rate limit: their ToS says no more than 5 req/sec averaged over 5 min.
    We sleep 0.25 s between calls to stay well under that.
    """

    BASE = "https://ws.audioscrobbler.com/2.0/"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("LASTFM_API_KEY")
        self._last_call = 0.0

    def available(self) -> bool:
        return bool(self.api_key)

    def _call(self, method: str, **params: Any) -> dict[str, Any] | None:
        if not self.api_key:
            return None
        # Throttle to ~4 req/sec max.
        elapsed = time.monotonic() - self._last_call
        if elapsed < 0.25:
            time.sleep(0.25 - elapsed)
        params.update({
            "method": method,
            "api_key": self.api_key,
            "format": "json",
        })
        try:
            resp = requests.get(self.BASE, params=params, timeout=15)
            self._last_call = time.monotonic()
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                return None
            return data
        except Exception:
            return None

    def get_similar_artists(self, artist: str, limit: int = 10) -> list[str]:
        data = self._call("artist.getSimilar", artist=artist, limit=limit, autocorrect=1)
        if not data:
            return []
        similar = data.get("similarartists", {}).get("artist", [])
        return [a.get("name") for a in similar if a.get("name")]

    def get_top_tags(self, artist: str, limit: int = 5) -> list[str]:
        data = self._call("artist.getTopTags", artist=artist, limit=limit, autocorrect=1)
        if not data:
            return []
        tags = data.get("toptags", {}).get("tag", [])
        if isinstance(tags, dict):
            tags = [tags]
        return [t.get("name") for t in tags if t.get("name")]
