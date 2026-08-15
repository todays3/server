"""Turn a phone notification into a short Kakao 'me' flash, with in-memory dedup."""

from __future__ import annotations

import hashlib
import time
from threading import Lock

_SEEN: dict[str, float] = {}
_LOCK = Lock()
_TTL_SECONDS = 6 * 3600


def clear_seen() -> None:
    with _LOCK:
        _SEEN.clear()


def notification_fingerprint(*, title: str, text: str) -> str:
    blob = f"{title.strip().casefold()}\n{text.strip().casefold()}".encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def already_seen(fingerprint: str) -> bool:
    now = time.monotonic()
    with _LOCK:
        expired = [key for key, seen_at in _SEEN.items() if now - seen_at > _TTL_SECONDS]
        for key in expired:
            del _SEEN[key]
        if fingerprint in _SEEN:
            return True
        _SEEN[fingerprint] = now
        return False


def format_flash_message(*, app: str, title: str, text: str) -> tuple[str, str]:
    source = app.strip() or "속보"
    headline = title.strip()
    kakao_title = f"속보 · {source}"
    lines = [f"⚡ {source}", headline]
    extra = text.strip()
    if extra and extra.casefold() != headline.casefold():
        lines.extend(["", extra[:800]])
    return kakao_title, "\n".join(lines)
