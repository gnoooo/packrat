"""packrat: download music from YouTube Music for offline use."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("packrat")
except PackageNotFoundError:  # running directly from a source checkout
    __version__ = "0.0.0"
