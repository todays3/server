"""One-time login tickets so Kakao OAuth does not put JWTs in redirect URLs."""

from __future__ import annotations

import secrets
import time
from threading import Lock

_TTL_SECONDS = 120.0
_lock = Lock()
_tickets: dict[str, tuple[float, str]] = {}


def issue_login_ticket(access_token: str) -> str:
    ticket = secrets.token_urlsafe(32)
    expires = time.monotonic() + _TTL_SECONDS
    with _lock:
        _purge_locked()
        _tickets[ticket] = (expires, access_token)
    return ticket


def consume_login_ticket(ticket: str) -> str | None:
    now = time.monotonic()
    with _lock:
        _purge_locked(now)
        item = _tickets.pop(ticket, None)
    if item is None:
        return None
    expires, token = item
    if expires < now:
        return None
    return token


def _purge_locked(now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    for key in [k for k, (exp, _) in _tickets.items() if exp < now]:
        del _tickets[key]
