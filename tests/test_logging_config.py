"""Redaction characterization tests."""

from ytm_offline.logging_config import redact


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
