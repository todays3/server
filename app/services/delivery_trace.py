"""Append-only delivery attempt trace stored on Digest rows."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.models import Digest


def parse_delivery_trace(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def append_delivery_trace(
    digest: Digest,
    *,
    step: str,
    ok: bool,
    error: str = "",
    chunks_sent: int = 0,
    chunk_total: int = 0,
    note: str = "",
) -> None:
    trace = parse_delivery_trace(getattr(digest, "delivery_trace_json", "") or "")
    trace.append(
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "step": step,
            "ok": ok,
            "error": error,
            "chunks_sent": int(chunks_sent),
            "chunk_total": int(chunk_total),
            "attempt": int(digest.attempt_count or 0),
            "note": note,
        }
    )
    digest.delivery_trace_json = json.dumps(trace, ensure_ascii=False)
