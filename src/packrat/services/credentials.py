"""Credential lifecycle service."""

from __future__ import annotations

import pathlib

from packrat import cli_args, security


def resolve_headers_path(explicit: str | None = None) -> pathlib.Path:
    return pathlib.Path(
        explicit or cli_args._headers_default() or cli_args.HEADERS_CONFIG_PATH
    ).expanduser()


def delete_login(explicit: str | None = None) -> tuple[pathlib.Path, bool]:
    path = resolve_headers_path(explicit)
    if not path.exists():
        return path, False
    if not path.is_file():
        raise IsADirectoryError(str(path))
    return path, security.remove_credentials(path)
