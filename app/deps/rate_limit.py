"""Simple in-memory sliding-window rate limiter for auth endpoints."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.auth import get_current_user
from app.config import get_settings
from app.models import User


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str, *, max_calls: int, window_seconds: float) -> None:
        now = time.monotonic()
        with self._lock:
            bucket = self._hits[key]
            while bucket and now - bucket[0] > window_seconds:
                bucket.popleft()
            if len(bucket) >= max_calls:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="요청이 너무 많습니다. 잠시 후 다시 시도하세요.",
                )
            bucket.append(now)


_limiter = SlidingWindowLimiter()


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def rate_limit_auth(
    request: Request,
    ip: Annotated[str, Depends(client_ip)],
) -> None:
    settings = get_settings()
    _limiter.check(
        f"auth:{ip}:{request.url.path}",
        max_calls=settings.rate_limit_auth_max,
        window_seconds=settings.rate_limit_auth_window_seconds,
    )


def rate_limit_hooks(
    request: Request,
    ip: Annotated[str, Depends(client_ip)],
) -> None:
    settings = get_settings()
    _limiter.check(
        f"hooks:{ip}:{request.url.path}",
        max_calls=settings.rate_limit_hooks_max,
        window_seconds=settings.rate_limit_hooks_window_seconds,
    )


def rate_limit_notes(
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    settings = get_settings()
    _limiter.check(
        f"notes:{user.id}",
        max_calls=settings.rate_limit_notes_max,
        window_seconds=settings.rate_limit_notes_window_seconds,
    )
