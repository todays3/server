"""In-process rate limiter and client IP extraction."""

from types import SimpleNamespace

import pytest

from app.deps.rate_limit import SlidingWindowLimiter, client_ip


def test_limiter_blocks_after_max_calls():
    limiter = SlidingWindowLimiter()
    limiter.check("k", max_calls=1, window_seconds=60)
    with pytest.raises(Exception):
        limiter.check("k", max_calls=1, window_seconds=60)


def test_client_ip_prefers_forwarded_for():
    req = SimpleNamespace(headers={"x-forwarded-for": "1.1.1.1, 2.2.2.2"}, client=None)
    assert client_ip(req) == "1.1.1.1"


def test_client_ip_falls_back_to_peer():
    req = SimpleNamespace(headers={}, client=SimpleNamespace(host="9.9.9.9"))
    assert client_ip(req) == "9.9.9.9"


def test_client_ip_unknown_without_peer():
    req = SimpleNamespace(headers={}, client=None)
    assert client_ip(req) == "unknown"
