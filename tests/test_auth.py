"""Password hashing, admin guard, and access tokens."""

from types import SimpleNamespace

import pytest

from app.auth import create_access_token, get_admin_user, hash_password, verify_password


def test_verify_password_rejects_missing_hash():
    assert verify_password("x", None) is False


def test_verify_password_accepts_matching_hash():
    hashed = hash_password("secret123")
    assert verify_password("secret123", hashed) is True


def test_get_admin_user_rejects_non_admin():
    with pytest.raises(Exception):
        get_admin_user(SimpleNamespace(is_admin=False))  # type: ignore[arg-type]


def test_create_access_token_roundtrip():
    token = create_access_token(1)
    assert isinstance(token, str) and len(token) > 10
