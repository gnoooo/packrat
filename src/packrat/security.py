"""Credential handling: private file modes and secret redaction helpers."""

from __future__ import annotations

import logging
import os
import pathlib
import stat

logger = logging.getLogger(__name__)


def secure_directory(path: pathlib.Path) -> None:
    """Create a config directory with owner-only permissions when possible."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    except OSError:
        logger.debug("could not secure directory %s", path, exc_info=True)


def secure_file(path: pathlib.Path) -> None:
    """Best-effort chmod 0600 for a credential file."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        logger.debug("could not secure file %s", path, exc_info=True)


def is_private(path: pathlib.Path) -> bool:
    """True when a file exists and has no group/other permission bits."""
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return False
    return mode & 0o077 == 0


def warn_if_insecure(path: pathlib.Path, label: str = "credential file") -> bool:
    """Warn (but do not fail) when a credential file is group/world readable."""
    if path.exists() and not is_private(path):
        logger.warning(
            "%s %s is readable by other users; chmod 600 it", label, path
        )
        return False
    return True


def remove_credentials(path: pathlib.Path) -> bool:
    """Delete a credential file. Returns True when a file was removed."""
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    except OSError:
        logger.warning("could not delete credential file %s", path, exc_info=True)
        return False
    return True
