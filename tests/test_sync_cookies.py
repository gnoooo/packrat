"""Tests for secure temporary browser-cookie handling."""

import pathlib
import stat
from unittest.mock import MagicMock, patch

from ytm_offline import sync


def test_browser_cookie_file_sets_mode_0600():
    with patch.object(sync, "extract_cookies_from_browser") as mock_extract:
        mock_extract.return_value = MagicMock()
        with sync.browser_cookie_file("firefox") as path:
            cookie_path = pathlib.Path(path)
            mode = cookie_path.stat().st_mode
            assert stat.S_IMODE(mode) == 0o600, f"expected 0o600, got {oct(stat.S_IMODE(mode))}"


def test_browser_cookie_file_cleans_up_on_normal_exit():
    with patch.object(sync, "extract_cookies_from_browser") as mock_extract:
        mock_extract.return_value = MagicMock()
        with sync.browser_cookie_file("firefox") as path:
            cookie_path = pathlib.Path(path)
            assert cookie_path.exists()
        assert not cookie_path.exists()


def test_browser_cookie_file_cleans_up_on_exception():
    with patch.object(sync, "extract_cookies_from_browser") as mock_extract:
        mock_extract.return_value = MagicMock()
        cookie_path = None
        try:
            with sync.browser_cookie_file("firefox") as path:
                cookie_path = pathlib.Path(path)
                assert cookie_path.exists()
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert cookie_path is not None
        assert not cookie_path.exists()
