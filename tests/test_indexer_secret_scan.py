"""Tests for indexer/secret_scan.py — detect-secrets gate."""
from __future__ import annotations

from indexer.secret_scan import contains_secret


def test_clean_text_passes():
    assert contains_secret("just a normal markdown paragraph about devices") is False


def test_aws_access_key_pattern():
    assert contains_secret("config: AKIAIOSFODNN7EXAMPLE  # placeholder") is True


def test_jwt_pattern():
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ."
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    assert contains_secret(f"Authorization: Bearer {jwt}") is True


def test_password_assignment():
    assert contains_secret('password = "this_is_a_real_secret_value_xyz123"') is True


def test_short_b64_does_not_trigger():
    # Common base64-like substrings shouldn't false-positive
    assert contains_secret("abc123==") is False
