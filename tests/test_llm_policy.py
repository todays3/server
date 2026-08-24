from types import SimpleNamespace

import threading
import time

from app.services.llm_policy import (
    GROQ_FREE_GPT_OSS_RPM,
    GROQ_FREE_GPT_OSS_TPM,
    ConcurrencyLimiter,
    TokenBudget,
    classify_llm_skip_reason,
    clamp_local_compute,
    effective_limit,
    estimate_request_tokens,
    extra_body_for_local,
    extra_body_for_model,
    extract_assistant_text,
    finish_reason,
    parse_json_object,
    is_rate_limit_error,
    retry_after_seconds,
    should_retry_empty,
    suggested_remote_max_concurrent,
    is_local_unavailable,
    is_transport_error,
)


def test_extract_assistant_text_from_string_and_parts():
    assert extract_assistant_text(SimpleNamespace(choices=[])) == ""
    assert (
        extract_assistant_text(
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="  hi  "))])
        )
        == "hi"
    )
    assert (
        extract_assistant_text(
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=[{"type": "text", "text": '{"a":1}'}, {"text": ""}])
                    )
                ]
            )
        )
        == '{"a":1}'
    )
    assert (
        extract_assistant_text(
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, reasoning="think"))])
        )
        == ""
    )


def test_parse_json_object_salvages_fences_prose_and_trailing_commas():
    assert parse_json_object("") is None
    assert parse_json_object("not json") is None
    assert parse_json_object('{"ok": true}') == {"ok": True}
    wrapped = 'Sure.\n```json\n{"title":"하루만장","items":[{"url":"https://a.example"},],}\n```\n'
    got = parse_json_object(wrapped)
    assert got is not None
    assert got["title"] == "하루만장"
    assert got["items"][0]["url"] == "https://a.example"


def test_finish_reason_and_empty_retry():
    resp = SimpleNamespace(choices=[SimpleNamespace(finish_reason="length")])
    assert finish_reason(resp) == "length"
    assert should_retry_empty(text="", finish="length", completion_tokens=10, max_tokens=1400, already=False)
    assert should_retry_empty(text="", finish="", completion_tokens=1400, max_tokens=1400, already=False)
    assert not should_retry_empty(text="", finish="", completion_tokens=12, max_tokens=1400, already=False)
    assert not should_retry_empty(text="{}", finish="length", completion_tokens=1400, max_tokens=1400, already=False)
    assert not should_retry_empty(text="", finish="length", completion_tokens=1400, max_tokens=1400, already=True)


def test_rate_limit_parse_and_classify():
    msg = (
        "Error code: 429 - {'error': {'message': 'Rate limit reached for model "
        "`openai/gpt-oss-120b` on tokens per minute (TPM): Limit 8000, Used 7906, "
        "Requested 6014. Please try again in 44.4s. Need more tokens?', "
        "'code': 'rate_limit_exceeded'}}"
    )
    exc = RuntimeError(msg)
    assert is_rate_limit_error(exc)
    assert retry_after_seconds(exc) == 44.4
    assert classify_llm_skip_reason(SimpleNamespace(error_message=msg)) == "rate_limited"
    assert classify_llm_skip_reason(SimpleNamespace(error_message="empty_response:finish_reason=length")) == (
        "empty_response"
    )
    assert classify_llm_skip_reason(None) == "empty_response"
    assert classify_llm_skip_reason(SimpleNamespace(error_message="provider down")) == "llm_error"
    assert is_transport_error(TimeoutError("local timed out"))
    assert is_transport_error("ConnectTimeout: timed out")
    assert not is_transport_error("empty_response")
    missing = "Error code: 404 - {'error': {'message': \"model 'qwen2.5:1.5b' not found\", 'type': 'not_found_error'}}"
    assert is_local_unavailable(missing)
    assert not is_transport_error(missing)


def test_extra_body_only_for_reasoning_models():
    assert extra_body_for_model("openai/gpt-oss-120b", "low") == {"reasoning_effort": "low"}
    assert extra_body_for_model("gpt-4o-mini", "low") is None
    assert extra_body_for_model("openai/gpt-oss-120b", "") is None


def test_token_budget_waits_until_window_has_room():
    budget = TokenBudget(tpm=100, window_seconds=10)
    now = [0.0]
    slept: list[float] = []
    budget.record(90, clock=lambda: now[0])
    budget.wait_for(20, sleeper=lambda s: slept.append(s) or now.__setitem__(0, now[0] + s), clock=lambda: now[0])
    assert slept
    assert slept[0] == 10
    assert now[0] == 10


def test_estimate_request_tokens_counts_cjk_conservatively():
    tokens = estimate_request_tokens([{"role": "user", "content": "가" * 100}], max_tokens=50)
    assert tokens >= 150
    with_reasoning = estimate_request_tokens(
        [{"role": "user", "content": "가" * 100}],
        max_tokens=50,
        reasoning_overhead=256,
    )
    assert with_reasoning >= tokens + 256


def test_clamp_local_compute_never_uses_every_cpu_core():
    concurrent, threads = clamp_local_compute(concurrent=32, threads=16, cpu_count=8)
    assert concurrent >= 1
    assert threads >= 1
    assert concurrent * threads <= 7
    assert threads < 8


def test_clamp_local_compute_keeps_small_budget():
    concurrent, threads = clamp_local_compute(concurrent=1, threads=2, cpu_count=8)
    assert concurrent == 1
    assert threads == 2


def test_clamp_local_compute_single_core_stays_at_one():
    concurrent, threads = clamp_local_compute(concurrent=4, threads=4, cpu_count=1)
    assert concurrent == 1
    assert threads == 1


def test_local_extra_body_sets_num_ctx_and_mlock():
    body = extra_body_for_local(num_ctx=4096, num_thread=2, mlock=True)
    assert body == {"options": {"num_ctx": 4096, "num_thread": 2, "use_mlock": True}}
    assert extra_body_for_local(num_ctx=4096, num_thread=2, mlock=False)["options"].get("use_mlock") is None


def test_concurrency_limiter_serializes_when_limit_is_one():
    limiter = ConcurrencyLimiter(1)
    active = 0
    peak = 0
    lock = threading.Lock()

    def worker():
        nonlocal active, peak
        with limiter:
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.04)
            with lock:
                active -= 1

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert peak == 1


def test_groq_free_gpt_oss_limits_match_published_docs():
    assert GROQ_FREE_GPT_OSS_RPM == 30
    assert GROQ_FREE_GPT_OSS_TPM == 8000


def test_suggested_remote_max_concurrent_is_one_on_free_tpm():
    assert suggested_remote_max_concurrent(tpm=8000, rpm=30, configured=8) == 1
    assert suggested_remote_max_concurrent(tpm=250_000, rpm=1000, configured=8) == 8
    assert suggested_remote_max_concurrent(tpm=8000, rpm=30, configured=1) == 1


def test_effective_limit_keeps_headroom_under_groq_ceiling():
    assert effective_limit(30, 0.85) == 25
    assert effective_limit(8000, 0.85) == 6800
    assert effective_limit(0, 0.85) == 0


def test_rpm_budget_waits_when_minute_is_full():
    budget = TokenBudget(tpm=2, window_seconds=10)
    now = [0.0]
    slept: list[float] = []
    budget.record(1, clock=lambda: now[0])
    budget.record(1, clock=lambda: now[0])
    budget.wait_for(1, sleeper=lambda s: slept.append(s) or now.__setitem__(0, now[0] + s), clock=lambda: now[0])
    assert slept
    assert now[0] == 10
