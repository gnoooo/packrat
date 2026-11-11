"""Redaction characterization tests."""

import logging

from packrat.logging_config import RedactFilter, redact


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


def test_redact_filter_redacts_record_in_place():
    """Redaction must work on every supported Python (filters returning a
    replacement LogRecord are only honored from 3.12 on)."""
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Cookie: VISITOR_INFO1_LIVE=%s",
        args=("secret",),
        exc_info=None,
    )
    assert RedactFilter().filter(record) is True
    assert record.args == ()
    assert "secret" not in record.getMessage()
    assert record.getMessage() == "Cookie: <redacted>"


def test_redact_filter_redacts_emitted_output():
    import io

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactFilter())
    log = logging.getLogger("packrat-redact-test")
    log.handlers = [handler]
    log.propagate = False
    log.setLevel(logging.INFO)
    try:
        log.info("Cookie: SECRET123 Authorization: Bearer abc")
    finally:
        log.handlers = []
    out = stream.getvalue()
    assert "SECRET123" not in out
    assert "Bearer abc" not in out
    assert "<redacted>" in out
