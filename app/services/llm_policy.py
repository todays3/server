"""Parse LLM responses, rate-limit errors, and Groq TPM pacing."""

from __future__ import annotations

import json
import os
import re
import threading
from typing import Any, Callable

_RETRY_IN_SECONDS = re.compile(r"try again in ([\d.]+)\s*s", re.I)

# Free-tier openai/gpt-oss-120b (https://console.groq.com/docs/rate-limits).
# Groq does not publish a separate in-flight cap; RPM and TPM bind first.
GROQ_FREE_GPT_OSS_RPM = 30
GROQ_FREE_GPT_OSS_TPM = 8000


def extract_assistant_text(resp: Any) -> str:
    choices = getattr(resp, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    if message is None:
        return ""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            if isinstance(part, dict):
                parts.append(str(part.get("text") or ""))
                continue
            parts.append(str(getattr(part, "text", "") or ""))
        return "".join(parts).strip()
    return ""


def parse_json_object(text: str) -> dict[str, Any] | None:
    """Pull a JSON object out of small-model chatter, fences, or trailing commas."""
    raw = (text or "").strip()
    if not raw:
        return None
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)
    candidates = [raw]
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for blob in candidates:
        cleaned = re.sub(r",\s*([}\]])", r"\1", blob)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def finish_reason(resp: Any) -> str:
    choices = getattr(resp, "choices", None) or []
    if not choices:
        return ""
    return str(getattr(choices[0], "finish_reason", "") or "")


def is_rate_limit_error(exc: BaseException) -> bool:
    if int(getattr(exc, "status_code", 0) or 0) == 429:
        return True
    text = str(exc).lower()
    return "429" in text or "rate_limit" in text or "rate limit" in text


def is_transport_error(exc: BaseException | str) -> bool:
    text = f"{type(exc).__name__} {exc}".lower() if not isinstance(exc, str) else exc.lower()
    return any(
        token in text
        for token in (
            "timeout",
            "timed out",
            "connect",
            "connection refused",
            "connection error",
            "unavailable",
        )
    )


def is_local_unavailable(exc: BaseException | str) -> bool:
    """Local Ollama is down or the Qwen tag is missing. Groq must not receive that tag."""
    if is_transport_error(exc):
        return True
    text = f"{type(exc).__name__} {exc}".lower() if not isinstance(exc, str) else exc.lower()
    return "not_found" in text or "not found" in text or "error code: 404" in text


def retry_after_seconds(exc: BaseException, default: float = 20.0) -> float:
    match = _RETRY_IN_SECONDS.search(str(exc))
    if match:
        return float(match.group(1))
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return default


def should_retry_empty(*, text: str, finish: str, completion_tokens: int, max_tokens: int, already: bool) -> bool:
    if already or text:
        return False
    if finish == "length":
        return True
    return completion_tokens >= max(1, int(max_tokens * 0.9))


def classify_llm_skip_reason(usage: Any | None) -> str:
    if usage is None:
        return "empty_response"
    err = str(getattr(usage, "error_message", "") or "")
    lower = err.lower()
    if "rate_limit" in lower or "429" in lower:
        return "rate_limited"
    if err.startswith("empty_response") or not err:
        return "empty_response"
    return "llm_error"


def estimate_request_tokens(
    messages: list[dict[str, str]],
    max_tokens: int,
    *,
    reasoning_overhead: int = 0,
) -> int:
    chars = 0
    for message in messages:
        chars += len(message.get("content") or "")
    # Digest prompts are mostly Hangul. chars//2 under-counted and caused TPM 429s.
    prompt = max(1, chars)
    return prompt + max(1, max_tokens) + max(0, int(reasoning_overhead))


def effective_limit(limit: int, headroom: float = 0.85) -> int:
    if limit <= 0:
        return 0
    ratio = min(1.0, max(0.1, float(headroom)))
    return max(1, int(limit * ratio))


def suggested_remote_max_concurrent(*, tpm: int, rpm: int, configured: int = 1) -> int:
    """Cap in-flight Groq calls. Free gpt-oss 8K TPM / 30 RPM → 1."""
    configured = max(1, int(configured) if configured else 1)
    cap = configured
    if tpm > 0:
        cap = min(cap, max(1, int(tpm) // GROQ_FREE_GPT_OSS_TPM))
    if rpm > 0:
        cap = min(cap, max(1, int(rpm) // GROQ_FREE_GPT_OSS_RPM))
    return max(1, cap)


def extra_body_for_model(model: str, effort: str) -> dict[str, str] | None:
    if not effort.strip():
        return None
    lowered = model.lower()
    if "gpt-oss" in lowered or lowered.startswith("o1") or lowered.startswith("o3"):
        return {"reasoning_effort": effort.strip()}
    return None


class TokenBudget:
    """Sliding-window TPM budget so Groq on_demand (often 8k/min) is not overrun."""

    def __init__(self, tpm: int, window_seconds: float = 60.0) -> None:
        self.tpm = tpm
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._events: list[tuple[float, int]] = []

    def _used(self, now: float) -> int:
        cutoff = now - self.window_seconds
        self._events = [(stamp, tokens) for stamp, tokens in self._events if stamp > cutoff]
        return sum(tokens for _, tokens in self._events)

    def wait_for(self, needed: int, sleeper: Callable[[float], None], clock: Callable[[], float]) -> None:
        if self.tpm <= 0:
            return
        target = min(max(1, needed), self.tpm)
        while True:
            now = clock()
            with self._lock:
                used = self._used(now)
                if self.tpm - used >= target:
                    return
                oldest = min(stamp for stamp, _ in self._events)
                delay = min(self.window_seconds, max(0.05, oldest + self.window_seconds - now))
            sleeper(delay)

    def record(self, tokens: int, clock: Callable[[], float]) -> None:
        if self.tpm <= 0 or tokens <= 0:
            return
        with self._lock:
            self._events.append((clock(), int(tokens)))


def clamp_local_compute(
    *,
    concurrent: int,
    threads: int,
    cpu_count: int | None = None,
) -> tuple[int, int]:
    """Keep local inference off at least one core when the host has more than one."""
    cpus = cpu_count if cpu_count is not None else (os.cpu_count() or 2)
    cpus = max(1, int(cpus))
    budget = max(1, cpus - 1) if cpus > 1 else 1
    threads = max(1, min(int(threads) if threads else 1, budget))
    concurrent = max(1, min(int(concurrent) if concurrent else 1, budget))
    while concurrent > 1 and concurrent * threads > budget:
        concurrent -= 1
    if concurrent * threads > budget:
        threads = max(1, budget // concurrent)
    return concurrent, threads


def extra_body_for_local(*, num_ctx: int, num_thread: int, mlock: bool) -> dict[str, dict[str, int | bool]]:
    options: dict[str, int | bool] = {
        "num_ctx": max(1, int(num_ctx)),
        "num_thread": max(1, int(num_thread)),
    }
    if mlock:
        options["use_mlock"] = True
    return {"options": options}


class ConcurrencyLimiter:
    """Hard cap on in-flight local model calls (CPU occupancy)."""

    def __init__(self, limit: int) -> None:
        self.limit = max(1, int(limit))
        self._sem = threading.BoundedSemaphore(self.limit)

    def __enter__(self) -> ConcurrencyLimiter:
        self._sem.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._sem.release()
