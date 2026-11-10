"""Redaction characterization tests."""

import logging

from ytm_offline.logging_config import RedactFilter, redact


def test_redact_masks_cookie():
    msg = "Cookie: VISITOR_INFO1_LIVE=secret123; SAPISID=token"
    out = redact(msg)
    assert "secret123" not in out
    assert "token" not in out
    assert "Cookie: <redacted>" in out


def test_redact_masks_authorization():
    msg = "Authorization: Bearer super-secret-token"
    out = redact(msg)
    assert "super-secret-token" not in out
    assert "Authorization: <redacted>" in out


def test_redact_masks_sapisid_pair():
    msg = "SAPISID=abc123; other=value"
    out = redact(msg)
    assert "abc123" not in out
    assert "SAPISID=<redacted>" in out
    assert "other=value" in out


def test_redact_filter_does_not_mutate_original_record():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Cookie: VISITOR_INFO1_LIVE=%s",
        args=("secret",),
        exc_info=None,
    )
    filtered = RedactFilter().filter(record)
    assert record.args == ("secret",)
    assert "secret" in record.getMessage()
    assert "secret" not in str(filtered.msg)
