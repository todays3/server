"""LLM client usage logging and provider URL resolution."""

from __future__ import annotations

from types import SimpleNamespace

import threading
import time

import pytest

from app.config import get_settings
from app.services import llm as llm_service


@pytest.fixture(autouse=True)
def _fast_llm_runtime(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_LOCAL_ENABLED", "false")
    llm_service.reset_llm_runtime_for_tests(sleeper=lambda _s: None, tpm=0, rpm=0)
    yield
    llm_service.reset_llm_runtime_for_tests()


def test_chat_completion_skips_when_not_configured(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "")
    get_settings.cache_clear()
    try:
        text, usage = llm_service.chat_completion(
            SimpleNamespace(), user_id=1, purpose="t", messages=[{"role": "user", "content": "hi"}]
        )
        assert text is None
        assert usage is None
    finally:
        get_settings.cache_clear()


def test_chat_completion_persists_usage_on_success(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    get_settings.cache_clear()
    try:

        class FakeUsage:
            prompt_tokens = 1
            completion_tokens = 2
            total_tokens = 0

        class FakeMsg:
            content = " hello "

        class FakeChoice:
            message = FakeMsg()

        class FakeResp:
            choices = [FakeChoice()]
            usage = FakeUsage()

        class FakeCompletions:
            def create(self, **_k):
                return FakeResp()

        class FakeChat:
            completions = FakeCompletions()

        class FakeClient:
            def __init__(self, **_k):
                self.chat = FakeChat()

        added: list = []

        class FakeDb:
            def add(self, row):
                added.append(row)

            def flush(self):
                return None

            def commit(self):
                return None

            def refresh(self, _row):
                return None

        monkeypatch.setattr("app.services.llm.OpenAI", FakeClient)
        out, row = llm_service.chat_completion(
            FakeDb(), user_id=1, purpose="digest_curate", messages=[{"role": "user", "content": "x"}]
        )
        assert out == "hello"
        assert row.total_tokens == 3
        assert row.ttft_ms >= 0
        assert row.total_ms >= 0
        assert row.streamed is False
    finally:
        get_settings.cache_clear()


def test_chat_completion_records_stream_ttft_and_tps(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    get_settings.cache_clear()
    ticks = [0.0]

    def clock() -> float:
        ticks[0] += 0.1
        return ticks[0]

    llm_service.reset_llm_runtime_for_tests(sleeper=lambda _s: None, tpm=0, rpm=0, clock=clock)

    class Event:
        def __init__(self, content: str, usage=None):
            self.choices = [
                SimpleNamespace(
                    delta=SimpleNamespace(content=content),
                    finish_reason="stop" if usage else None,
                    message=None,
                )
            ]
            self.usage = usage

    class FakeClient:
        def __init__(self, **_k):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **k: (
                        Event("hel"),
                        Event(
                            "lo",
                            SimpleNamespace(prompt_tokens=1, completion_tokens=4, total_tokens=5),
                        ),
                    )
                )
            )

    added: list = []
    try:
        monkeypatch.setattr("app.services.llm.OpenAI", FakeClient)
        out, row = llm_service.chat_completion(
            _fake_db(added), user_id=1, purpose="digest_curate", messages=[{"role": "user", "content": "x"}]
        )
        assert out == "hello"
        assert row.streamed is True
        assert row.ttft_ms > 0
        assert row.total_ms >= row.ttft_ms
        assert row.tps > 0
        assert row.completion_tokens == 4
    finally:
        get_settings.cache_clear()
        llm_service.reset_llm_runtime_for_tests()


def test_chat_completion_persists_error_row_on_failure(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    get_settings.cache_clear()
    try:

        class Boom:
            def __init__(self, **_k):
                self.chat = SimpleNamespace(
                    completions=SimpleNamespace(create=lambda **_k: (_ for _ in ()).throw(RuntimeError("down")))
                )

        class FakeDb:
            def add(self, row):
                return None

            def flush(self):
                return None

            def commit(self):
                return None

            def refresh(self, _row):
                return None

        monkeypatch.setattr("app.services.llm.OpenAI", Boom)
        none, err_row = llm_service.chat_completion(FakeDb(), user_id=1, purpose="digest_curate", messages=[])
        assert none is None
        assert err_row.success is False
    finally:
        get_settings.cache_clear()


def test_resolved_llm_urls_and_models(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("LLM_MODEL", "")
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert "openai.com" in s.resolved_llm_base_url
        assert s.resolved_llm_model == "gpt-4o-mini"
        monkeypatch.setenv("LLM_PROVIDER", "groq")
        monkeypatch.setenv("LLM_MODEL", "")
        get_settings.cache_clear()
        groq = get_settings()
        assert groq.resolved_llm_model == "openai/gpt-oss-120b"
        monkeypatch.setenv("LLM_PROVIDER", "hybrid")
        monkeypatch.setenv("LLM_MODEL", "qwen2.5:1.5b")
        get_settings.cache_clear()
        hybrid = get_settings()
        assert hybrid.resolved_llm_local_model == "qwen2.5:1.5b"
        assert hybrid.resolved_llm_model == "openai/gpt-oss-120b"
        monkeypatch.setenv("LLM_PROVIDER", "custom")
        get_settings.cache_clear()
        s2 = get_settings()
        assert s2.resolved_llm_base_url.endswith("/v1") or "groq" in s2.resolved_llm_base_url
        monkeypatch.setenv("LLM_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("LLM_MODEL", "mine")
        get_settings.cache_clear()
        s3 = get_settings()
        assert s3.resolved_llm_base_url == "https://example.com/v1"
        assert s3.resolved_llm_model == "mine"
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.delenv("LLM_BASE_URL", raising=False)
        monkeypatch.setenv("LLM_MODEL", "")
        get_settings.cache_clear()
        local = get_settings()
        assert local.llm_configured is True
        assert "11434" in local.resolved_llm_local_base_url
        assert local.resolved_llm_local_model == "qwen2.5:1.5b"
        assert local.llm_local_num_ctx == 4096
    finally:
        get_settings.cache_clear()


def _fake_db(added: list):
    class FakeDb:
        def add(self, row):
            added.append(row)

        def flush(self):
            return None

        def commit(self):
            return None

        def refresh(self, _row):
            return None

    return FakeDb()


def test_chat_completion_sends_low_reasoning_for_gpt_oss(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-oss-120b")
    get_settings.cache_clear()
    captured: dict = {}

    class FakeMsg:
        content = '{"ok":true}'

    class FakeChoice:
        message = FakeMsg()
        finish_reason = "stop"

    class FakeResp:
        choices = [FakeChoice()]
        usage = SimpleNamespace(prompt_tokens=4, completion_tokens=6, total_tokens=10)

    class FakeClient:
        def __init__(self, **_k):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kwargs: captured.update(kwargs) or FakeResp())
            )

    added: list = []
    try:
        monkeypatch.setattr("app.services.llm.OpenAI", FakeClient)
        text, row = llm_service.chat_completion(
            _fake_db(added), user_id=1, purpose="digest_curate", messages=[{"role": "user", "content": "x"}]
        )
        assert text == '{"ok":true}'
        assert captured["extra_body"] == {"reasoning_effort": "low"}
        assert row.success is True
    finally:
        get_settings.cache_clear()


def test_chat_completion_retries_rate_limit_then_succeeds(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MAX_RETRIES", "2")
    get_settings.cache_clear()
    calls = {"n": 0}
    slept: list[float] = []
    llm_service.reset_llm_runtime_for_tests(sleeper=slept.append, tpm=0)

    class FakeMsg:
        content = "ok"

    class FakeResp:
        choices = [SimpleNamespace(message=FakeMsg(), finish_reason="stop")]
        usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)

    def create(**_k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError(
                "Error code: 429 - {'error': {'message': 'Please try again in 44.4s.', "
                "'code': 'rate_limit_exceeded'}}"
            )
        return FakeResp()

    class FakeClient:
        def __init__(self, **_k):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    added: list = []
    try:
        monkeypatch.setattr("app.services.llm.OpenAI", FakeClient)
        text, row = llm_service.chat_completion(
            _fake_db(added), user_id=1, purpose="digest_curate", messages=[{"role": "user", "content": "x"}]
        )
        assert text == "ok"
        assert row.success is True
        assert slept == [44.4]
        assert calls["n"] == 2
    finally:
        get_settings.cache_clear()


def test_chat_completion_retries_empty_length_then_reads_content(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    get_settings.cache_clear()
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=None), finish_reason="length")],
                usage=SimpleNamespace(prompt_tokens=5014, completion_tokens=1400, total_tokens=6414),
            )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"items":[]}'), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=5014, completion_tokens=200, total_tokens=5214),
        )

    class FakeClient:
        def __init__(self, **_k):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    added: list = []
    try:
        monkeypatch.setattr("app.services.llm.OpenAI", FakeClient)
        text, row = llm_service.chat_completion(
            _fake_db(added),
            user_id=1,
            purpose="digest_curate",
            messages=[{"role": "user", "content": "x"}],
            max_tokens=1400,
        )
        assert text == '{"items":[]}'
        assert row.success is True
        assert calls["n"] == 2
        assert added[-1].completion_tokens == 200
    finally:
        get_settings.cache_clear()


def _ok_resp(text: str = "ok"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


def test_local_chat_sends_num_ctx_4096_and_thread_cap(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_LOCAL_ENABLED", "true")
    monkeypatch.setenv("LLM_LOCAL_NUM_CTX", "4096")
    monkeypatch.setenv("LLM_LOCAL_NUM_THREAD", "2")
    monkeypatch.setenv("LLM_LOCAL_MLOCK", "true")
    monkeypatch.setenv("LLM_LOCAL_MAX_CONCURRENT", "1")
    get_settings.cache_clear()
    captured: dict = {}

    class FakeClient:
        def __init__(self, **_k):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kwargs: captured.update(kwargs) or _ok_resp("local"))
            )

    added: list = []
    try:
        monkeypatch.setattr("app.services.llm.OpenAI", FakeClient)
        text, row = llm_service.chat_completion(
            _fake_db(added), user_id=1, purpose="digest_curate", messages=[{"role": "user", "content": "x"}]
        )
        assert text == "local"
        assert row.provider == "ollama"
        assert row.model == "qwen2.5:1.5b"
        assert captured["extra_body"]["options"]["num_ctx"] == 4096
        assert captured["extra_body"]["options"]["use_mlock"] is True
        assert captured["extra_body"]["options"]["num_thread"] >= 1
    finally:
        get_settings.cache_clear()


def test_digest_prefer_remote_skips_local_for_curate(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "hybrid")
    monkeypatch.setenv("LLM_LOCAL_ENABLED", "true")
    monkeypatch.setenv("LLM_DIGEST_PREFER_REMOTE", "true")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-oss-120b")
    get_settings.cache_clear()
    llm_service.reset_llm_runtime_for_tests(sleeper=lambda _s: None, tpm=0)
    hosts: list[str] = []

    class FakeClient:
        def __init__(self, api_key="", base_url="", **_k):
            host = str(base_url)

            def create(**_kwargs):
                hosts.append(host)
                if "11434" in host:
                    raise AssertionError("local must be skipped for digest when prefer_remote")
                return _ok_resp("from-groq")

            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    added: list = []
    try:
        monkeypatch.setattr("app.services.llm.OpenAI", FakeClient)
        text, row = llm_service.chat_completion(
            _fake_db(added),
            user_id=1,
            purpose="digest_curate",
            messages=[{"role": "user", "content": "x"}],
        )
        assert text == "from-groq"
        assert row.provider == "groq"
        assert hosts and all("11434" not in h for h in hosts)
    finally:
        get_settings.cache_clear()


def test_hybrid_falls_back_to_groq_when_local_fails(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "hybrid")
    monkeypatch.setenv("LLM_LOCAL_ENABLED", "true")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-oss-120b")
    get_settings.cache_clear()
    calls: list[str] = []

    class FakeClient:
        def __init__(self, api_key="", base_url="", **_k):
            host = str(base_url)

            def create(**_kwargs):
                calls.append(host)
                if "11434" in host:
                    raise RuntimeError("local down")
                return _ok_resp("from-groq")

            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    added: list = []
    try:
        monkeypatch.setattr("app.services.llm.OpenAI", FakeClient)
        text, row = llm_service.chat_completion(
            _fake_db(added), user_id=1, purpose="digest_curate", messages=[{"role": "user", "content": "x"}]
        )
        assert text == "from-groq"
        assert any("11434" in host for host in calls)
        assert row.success is True
        assert row.provider == "groq"
    finally:
        get_settings.cache_clear()


def test_hybrid_never_sends_qwen_to_groq_when_local_model_missing(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "hybrid")
    monkeypatch.setenv("LLM_LOCAL_ENABLED", "true")
    monkeypatch.setenv("LLM_MODEL", "qwen2.5:1.5b")
    get_settings.cache_clear()
    llm_service.reset_llm_runtime_for_tests(sleeper=lambda _s: None, tpm=0)
    groq_models: list[str] = []

    class FakeClient:
        def __init__(self, api_key="", base_url="", **_k):
            host = str(base_url)

            def create(**kwargs):
                if "11434" in host or "ollama" in host:
                    raise RuntimeError(
                        "Error code: 404 - {'error': {'message': \"model 'qwen2.5:1.5b' not found\"}}"
                    )
                groq_models.append(str(kwargs.get("model")))
                assert "groq.com" in host
                return _ok_resp("from-groq")

            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    added: list = []
    try:
        monkeypatch.setattr("app.services.llm.OpenAI", FakeClient)
        text, row = llm_service.chat_completion(
            _fake_db(added), user_id=1, purpose="digest_curate", messages=[{"role": "user", "content": "x"}]
        )
        assert text == "from-groq"
        assert groq_models == ["openai/gpt-oss-120b"]
        assert row.provider == "groq"
        assert row.model == "openai/gpt-oss-120b"
    finally:
        get_settings.cache_clear()


def test_local_max_concurrent_prevents_overlap(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_LOCAL_ENABLED", "true")
    monkeypatch.setenv("LLM_LOCAL_MAX_CONCURRENT", "1")
    get_settings.cache_clear()
    llm_service.reset_llm_runtime_for_tests(sleeper=lambda _s: None, tpm=0)
    active = 0
    peak = 0
    lock = threading.Lock()

    def create(**_k):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return _ok_resp("local")

    class FakeClient:
        def __init__(self, **_k):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    added: list = []
    monkeypatch.setattr("app.services.llm.OpenAI", FakeClient)
    try:
        def worker():
            llm_service.chat_completion(
                _fake_db(added), user_id=1, purpose="digest_curate", messages=[{"role": "user", "content": "x"}]
            )

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert peak == 1
    finally:
        get_settings.cache_clear()


def test_groq_max_concurrent_prevents_overlap(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_MAX_CONCURRENT", "1")
    monkeypatch.setenv("LLM_RPM_LIMIT", "30")
    monkeypatch.setenv("LLM_TPM_LIMIT", "8000")
    get_settings.cache_clear()
    llm_service.reset_llm_runtime_for_tests(sleeper=lambda _s: None, tpm=0, rpm=0)
    active = 0
    peak = 0
    lock = threading.Lock()

    def create(**_k):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return _ok_resp("ok")

    class FakeClient:
        def __init__(self, **_k):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    added: list = []
    monkeypatch.setattr("app.services.llm.OpenAI", FakeClient)
    try:

        def worker():
            llm_service.chat_completion(
                _fake_db(added), user_id=1, purpose="digest_curate", messages=[{"role": "user", "content": "x"}]
            )

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert peak == 1
    finally:
        get_settings.cache_clear()


def test_chat_completion_paces_groq_rpm_before_sending(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_RPM_LIMIT", "2")
    monkeypatch.setenv("LLM_TPM_LIMIT", "0")
    get_settings.cache_clear()
    now = [0.0]
    slept: list[float] = []

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    llm_service.reset_llm_runtime_for_tests(sleeper=fake_sleep, clock=lambda: now[0], tpm=0, rpm=2)

    class FakeClient:
        def __init__(self, **_k):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_kwargs: _ok_resp("ok"))
            )

    added: list = []
    monkeypatch.setattr("app.services.llm.OpenAI", FakeClient)
    try:
        for _ in range(3):
            text, row = llm_service.chat_completion(
                _fake_db(added), user_id=1, purpose="digest_curate", messages=[{"role": "user", "content": "x"}]
            )
            assert text == "ok"
            assert row.success is True
        assert slept
        assert now[0] >= 60
    finally:
        get_settings.cache_clear()


def test_openai_client_disables_sdk_retries(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    get_settings.cache_clear()
    captured: dict = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_k: _ok_resp("ok"))
            )

    added: list = []
    try:
        monkeypatch.setattr("app.services.llm.OpenAI", FakeClient)
        llm_service.chat_completion(
            _fake_db(added), user_id=1, purpose="digest_curate", messages=[{"role": "user", "content": "x"}]
        )
        assert captured.get("max_retries") == 0
    finally:
        get_settings.cache_clear()


def test_hybrid_skips_unhealthy_local_and_uses_groq(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "hybrid")
    monkeypatch.setenv("LLM_LOCAL_ENABLED", "true")
    monkeypatch.setenv("LLM_LOCAL_UNHEALTHY_SKIP_SECONDS", "60")
    get_settings.cache_clear()
    now = [10.0]
    llm_service.reset_llm_runtime_for_tests(sleeper=lambda _s: None, clock=lambda: now[0], tpm=0, rpm=0)
    hosts: list[str] = []

    class FakeClient:
        def __init__(self, api_key="", base_url="", **_k):
            host = str(base_url)

            def create(**_kwargs):
                hosts.append(host)
                if "11434" in host:
                    raise TimeoutError("local timed out")
                return _ok_resp("from-groq")

            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    added: list = []
    monkeypatch.setattr("app.services.llm.OpenAI", FakeClient)
    try:
        first, _row = llm_service.chat_completion(
            _fake_db(added), user_id=1, purpose="digest_curate", messages=[{"role": "user", "content": "x"}]
        )
        assert first == "from-groq"
        hosts.clear()
        second, _row2 = llm_service.chat_completion(
            _fake_db(added), user_id=1, purpose="digest_curate", messages=[{"role": "user", "content": "x"}]
        )
        assert second == "from-groq"
        assert all("11434" not in host for host in hosts)
    finally:
        get_settings.cache_clear()
