"""OpenAI-compatible LLM client with local Ollama + Groq fallback, RPM/TPM pacing, and 429 retry."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from openai import OpenAI
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import LlmUsage
from app.services.llm_policy import (
    ConcurrencyLimiter,
    TokenBudget,
    clamp_local_compute,
    effective_limit,
    estimate_request_tokens,
    extra_body_for_local,
    extra_body_for_model,
    extract_assistant_text,
    finish_reason,
    is_local_unavailable,
    is_rate_limit_error,
    retry_after_seconds,
    should_retry_empty,
    suggested_remote_max_concurrent,
)
from app.services.llm_quality import (
    RagScores,
    apply_quality,
    compute_timing,
    estimate_completion_tokens,
    score_rag,
)

_sleep: Callable[[float], None] = time.sleep
_clock: Callable[[], float] = time.monotonic
_budget: TokenBudget | None = None
_budget_lock = threading.Lock()
_rpm_budget: TokenBudget | None = None
_local_limiter: ConcurrencyLimiter | None = None
_local_limiter_n = -1
_remote_limiter: ConcurrencyLimiter | None = None
_remote_limiter_n = -1
_local_skip_until = 0.0


def reset_llm_runtime_for_tests(
    *,
    sleeper: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
    tpm: int | None = None,
    rpm: int | None = None,
) -> None:
    global _sleep, _clock, _budget, _rpm_budget, _local_limiter, _local_limiter_n, _remote_limiter, _remote_limiter_n, _local_skip_until
    _sleep = sleeper or time.sleep
    _clock = clock or time.monotonic
    _budget = None if tpm is None else TokenBudget(tpm)
    _rpm_budget = None if rpm is None else TokenBudget(rpm)
    _local_limiter = None
    _local_limiter_n = -1
    _remote_limiter = None
    _remote_limiter_n = -1
    _local_skip_until = 0.0


def _get_budget(tpm: int) -> TokenBudget:
    global _budget
    with _budget_lock:
        if _budget is None:
            _budget = TokenBudget(tpm)
        return _budget


def _get_rpm_budget(rpm: int) -> TokenBudget:
    global _rpm_budget
    with _budget_lock:
        if _rpm_budget is None:
            _rpm_budget = TokenBudget(rpm)
        return _rpm_budget


def _get_local_limiter(limit: int) -> ConcurrencyLimiter:
    global _local_limiter, _local_limiter_n
    with _budget_lock:
        if _local_limiter is None or _local_limiter_n != limit:
            _local_limiter = ConcurrencyLimiter(limit)
            _local_limiter_n = limit
        return _local_limiter


def _get_remote_limiter(limit: int) -> ConcurrencyLimiter:
    global _remote_limiter, _remote_limiter_n
    with _budget_lock:
        if _remote_limiter is None or _remote_limiter_n != limit:
            _remote_limiter = ConcurrencyLimiter(limit)
            _remote_limiter_n = limit
        return _remote_limiter


def _usage_tokens(resp: Any) -> tuple[int, int, int]:
    usage = getattr(resp, "usage", None)
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    total_tokens = int(getattr(usage, "total_tokens", 0) or (prompt_tokens + completion_tokens))
    return prompt_tokens, completion_tokens, total_tokens


def _delta_text(event: Any) -> str:
    choices = getattr(event, "choices", None) or []
    if not choices:
        return ""
    delta = getattr(choices[0], "delta", None)
    content = getattr(delta, "content", None) if delta is not None else None
    return str(content or "")


def _is_nonstream_response(resp: Any) -> bool:
    choices = getattr(resp, "choices", None)
    if not choices:
        return False
    message = getattr(choices[0], "message", None)
    return message is not None


def _read_completion(resp: Any, started: float) -> tuple[str, tuple[int, int, int], Any]:
    """Return (text, usage tokens, finish_reason_raw) from stream or full response."""
    if _is_nonstream_response(resp):
        ended = _clock()
        text = extract_assistant_text(resp) or ""
        tokens = _usage_tokens(resp)
        finish = finish_reason(resp)
        timing = compute_timing(
            started_at=started,
            first_token_at=None,
            ended_at=ended,
            completion_tokens=tokens[1] or estimate_completion_tokens(text),
            streamed=False,
        )
        return text, tokens, (finish, timing)

    parts: list[str] = []
    first_at: float | None = None
    usage_holder: Any = None
    finish = ""
    try:
        for event in resp:
            piece = _delta_text(event)
            if piece:
                if first_at is None:
                    first_at = _clock()
                parts.append(piece)
            event_finish = finish_reason(event)
            if event_finish:
                finish = event_finish
            if getattr(event, "usage", None) is not None:
                usage_holder = event
    except TypeError:
        ended = _clock()
        timing = compute_timing(
            started_at=started,
            first_token_at=None,
            ended_at=ended,
            completion_tokens=0,
            streamed=False,
        )
        return "", (0, 0, 0), ("", timing)
    ended = _clock()
    text = "".join(parts).strip()
    tokens = _usage_tokens(usage_holder) if usage_holder is not None else (0, 0, 0)
    completion = tokens[1] or estimate_completion_tokens(text)
    timing = compute_timing(
        started_at=started,
        first_token_at=first_at,
        ended_at=ended,
        completion_tokens=completion,
        streamed=first_at is not None,
    )
    if tokens[1] == 0 and completion:
        tokens = (tokens[0], completion, tokens[0] + completion)
    return text, tokens, (finish, timing)


def _persist(
    db: Session,
    *,
    user_id: int,
    purpose: str,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    success: bool,
    error_message: str,
    timing=None,
    rag: RagScores | None = None,
) -> LlmUsage:
    row = LlmUsage(
        user_id=user_id,
        purpose=purpose,
        provider=provider,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        success=success,
        error_message=error_message[:700],
    )
    if timing is not None:
        apply_quality(row, timing, rag)
    db.add(row)
    # Commit immediately so SQLite write lock is not held across later LLM/network work
    # in the same Session (e.g. hybrid local→Groq, multi-assistant curate).
    db.commit()
    db.refresh(row)
    return row


def _openai_client(*, api_key: str, base_url: str, timeout: float | None, connect_timeout: float | None = None) -> OpenAI:
    kwargs: dict[str, Any] = {"api_key": api_key or "ollama", "base_url": base_url, "max_retries": 0}
    if timeout is not None:
        try:
            import httpx

            kwargs["timeout"] = httpx.Timeout(timeout, connect=connect_timeout or min(2.0, timeout))
        except Exception:  # noqa: BLE001
            kwargs["timeout"] = timeout
    return OpenAI(**kwargs)


def _complete(
    db: Session,
    *,
    user_id: int,
    purpose: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    local: bool,
    rag: RagScores | None = None,
) -> tuple[str | None, LlmUsage | None]:
    settings = get_settings()
    if local:
        concurrent, threads = clamp_local_compute(
            concurrent=int(settings.llm_local_max_concurrent),
            threads=int(settings.llm_local_num_thread),
        )
        provider = "ollama"
        model = settings.resolved_llm_local_model
        client = _openai_client(
            api_key=settings.llm_local_api_key,
            base_url=settings.resolved_llm_local_base_url,
            timeout=float(settings.llm_local_timeout_seconds),
            connect_timeout=float(settings.llm_local_connect_timeout_seconds),
        )
        extra = extra_body_for_local(
            num_ctx=int(settings.llm_local_num_ctx or 4096),
            num_thread=threads,
            mlock=bool(settings.llm_local_mlock),
        )
        budget = TokenBudget(0)
        rpm_budget = TokenBudget(0)
        gate = _get_local_limiter(concurrent)
    else:
        provider = settings.resolved_remote_provider
        model = settings.resolved_llm_model
        client = _openai_client(
            api_key=settings.llm_api_key,
            base_url=settings.resolved_llm_base_url,
            timeout=None,
        )
        extra = extra_body_for_model(model, (settings.llm_reasoning_effort or "").strip())
        headroom = float(settings.llm_rate_headroom)
        budget = _get_budget(effective_limit(int(settings.llm_tpm_limit), headroom))
        rpm_budget = _get_rpm_budget(effective_limit(int(settings.llm_rpm_limit), headroom))
        concurrent = suggested_remote_max_concurrent(
            tpm=int(settings.llm_tpm_limit),
            rpm=int(settings.llm_rpm_limit),
            configured=int(settings.llm_max_concurrent),
        )
        gate = _get_remote_limiter(concurrent)

    retries = max(0, int(settings.llm_max_retries))
    retry_cap = max(1.0, float(settings.llm_retry_cap_seconds))
    reasoning_overhead = 256 if (not local and "gpt-oss" in model.lower()) else 0
    needed = estimate_request_tokens(messages, max_tokens, reasoning_overhead=reasoning_overhead)
    empty_retried = False
    last_exc: BaseException | None = None
    effort = (settings.llm_reasoning_effort or "").strip()

    def _invoke() -> tuple[str | None, LlmUsage]:
        nonlocal max_tokens, needed, empty_retried, last_exc, extra, effort
        for attempt in range(retries + 1):
            if local:
                extra = extra_body_for_local(
                    num_ctx=int(settings.llm_local_num_ctx or 4096),
                    num_thread=clamp_local_compute(
                        concurrent=int(settings.llm_local_max_concurrent),
                        threads=int(settings.llm_local_num_thread),
                    )[1],
                    mlock=bool(settings.llm_local_mlock),
                )
            else:
                extra = extra_body_for_model(model, effort)
            rpm_budget.wait_for(1, _sleep, _clock)
            budget.wait_for(needed, _sleep, _clock)
            rpm_budget.record(1, _clock)
            try:
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                }
                if extra:
                    kwargs["extra_body"] = extra
                started = _clock()
                resp = client.chat.completions.create(**kwargs)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if is_rate_limit_error(exc) and attempt < retries:
                    _sleep(min(retry_after_seconds(exc), retry_cap))
                    continue
                return None, _persist(
                    db,
                    user_id=user_id,
                    purpose=purpose,
                    provider=provider,
                    model=model,
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                    success=False,
                    error_message=str(exc),
                )

            text, token_pack, extra_out = _read_completion(resp, started)
            finish, timing = extra_out
            prompt_tokens, completion_tokens, total_tokens = token_pack
            budget.record(total_tokens or prompt_tokens + completion_tokens, _clock)
            if should_retry_empty(
                text=text,
                finish=finish,
                completion_tokens=completion_tokens,
                max_tokens=max_tokens,
                already=empty_retried,
            ):
                empty_retried = True
                effort = effort or "low"
                max_tokens = min(max(max_tokens, 1) * 2, 2500)
                needed = estimate_request_tokens(
                    messages, max_tokens, reasoning_overhead=reasoning_overhead
                )
                continue

            error_message = ""
            if not text:
                error_message = "empty_response"
                if finish:
                    error_message = f"empty_response:finish_reason={finish}"
            return text or None, _persist(
                db,
                user_id=user_id,
                purpose=purpose,
                provider=provider,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                success=bool(text),
                error_message=error_message,
                timing=timing,
                rag=rag,
            )

        return None, _persist(
            db,
            user_id=user_id,
            purpose=purpose,
            provider=provider,
            model=model,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            success=False,
            error_message=str(last_exc or "llm_retries_exhausted")[:700],
        )

    with gate:
        return _invoke()


def chat_completion(
    db: Session,
    *,
    user_id: int,
    purpose: str,
    messages: list[dict[str, str]],
    temperature: float = 0.4,
    max_tokens: int = 900,
    quality_question: str | None = None,
    quality_contexts: list[str] | None = None,
    quality_grounded_values: list[str] | None = None,
    force_remote: bool = False,
) -> tuple[str | None, LlmUsage | None]:
    """Call local Qwen and/or Groq. Hybrid tries local first, then Groq."""
    settings = get_settings()
    if not settings.llm_configured:
        return None, None

    def _attach_rag(answer: str | None, row: LlmUsage | None) -> None:
        if not quality_contexts or not answer or row is None:
            return
        try:
            scored = score_rag(
                question=quality_question or "",
                answer=answer,
                contexts=quality_contexts,
                grounded_values=quality_grounded_values,
            )
        except Exception:
            return
        row.faithfulness = scored.faithfulness
        row.hallucination_rate = scored.hallucination_rate
        row.answer_relevance = scored.answer_relevance
        row.context_precision = scored.context_precision

    last: LlmUsage | None = None
    global _local_skip_until
    prefer_remote = force_remote or (
        bool(settings.llm_digest_prefer_remote) and purpose.startswith("digest")
    )
    use_local = (
        settings.llm_local_ready
        and not prefer_remote
        and _clock() >= _local_skip_until
    )
    if use_local:
        text, row = _complete(
            db,
            user_id=user_id,
            purpose=purpose,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            local=True,
            rag=None,
        )
        last = row
        if text:
            _attach_rag(text, row)
            if row is not None:
                db.commit()
            _local_skip_until = 0.0
            return text, row
        if is_local_unavailable(getattr(row, "error_message", "") or ""):
            skip_for = max(1.0, float(settings.llm_local_unhealthy_skip_seconds))
            _local_skip_until = _clock() + skip_for
        if not settings.remote_llm_ready:
            return None, row

    if settings.remote_llm_ready:
        text, row = _complete(
            db,
            user_id=user_id,
            purpose=purpose,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            local=False,
            rag=None,
        )
        _attach_rag(text, row)
        if row is not None:
            db.commit()
        return text, row
    return None, last
