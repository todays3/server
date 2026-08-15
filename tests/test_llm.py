"""LLM client usage logging and provider URL resolution."""

from __future__ import annotations

from types import SimpleNamespace

from app.config import get_settings
from app.services import llm as llm_service


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

        monkeypatch.setattr("app.services.llm.OpenAI", FakeClient)
        out, row = llm_service.chat_completion(
            FakeDb(), user_id=1, purpose="digest_curate", messages=[{"role": "user", "content": "x"}]
        )
        assert out == "hello"
        assert row.total_tokens == 3
    finally:
        get_settings.cache_clear()


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
    monkeypatch.delenv("LLM_MODEL", raising=False)
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert "openai.com" in s.resolved_llm_base_url
        assert s.resolved_llm_model == "gpt-4o-mini"
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
    finally:
        get_settings.cache_clear()
