"""Size helpers."""

from __future__ import annotations

from ytm_offline.sizes import estimate_size, format_size, parse_size


def test_estimate_size_from_filesize_approx():
    assert estimate_size({"filesize_approx": 1234}) == 1234


def test_estimate_size_from_requested_formats():
    assert estimate_size({"requested_formats": [
        {"filesize_approx": 100}, {"filesize_approx": 200},
    ]}) == 300


def test_estimate_size_missing():
    assert estimate_size(None) is None
    assert estimate_size({}) is None


def test_size_round_trip():
    assert parse_size("3GB") == 3_000_000_000
    assert format_size(1024) == "1.0 KB"
