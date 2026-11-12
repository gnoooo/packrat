"""packrat: download music from YouTube Music for offline use."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _source_version() -> str | None:
    """Read pyproject.toml when running directly from a checkout."""
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        import tomllib

        with pyproject.open("rb") as handle:
            return str(tomllib.load(handle)["project"]["version"])
    except Exception:
        return None


try:
    __version__ = _source_version() or version("packrat")
except PackageNotFoundError:  # installed metadata unavailable
    __version__ = "0.0.0"
