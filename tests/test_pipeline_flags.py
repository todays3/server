"""Pipeline agentic flags."""

from __future__ import annotations

from app.services.pipeline_flags import (
    clear_pipeline_flag_overrides,
    critique_enabled,
    enrichment_enabled,
    get_pipeline_flags,
    set_pipeline_flags,
)


def setup_function() -> None:
    clear_pipeline_flag_overrides()


def test_defaults_with_hybrid_critique_off(monkeypatch):
    monkeypatch.setenv("AGENT_ENRICHMENT_ENABLED", "true")
    monkeypatch.setenv("LLM_PROVIDER", "hybrid")
    from app.config import get_settings

    get_settings.cache_clear()
    clear_pipeline_flag_overrides()
    assert enrichment_enabled() is True
    assert critique_enabled() is False


def test_override_enrichment_off(monkeypatch):
    monkeypatch.setenv("AGENT_ENRICHMENT_ENABLED", "true")
    from app.config import get_settings

    get_settings.cache_clear()
    clear_pipeline_flag_overrides()
    set_pipeline_flags(agent_enrichment=False)
    assert enrichment_enabled() is False
    flags = get_pipeline_flags()
    assert flags["agent_enrichment"] is False
    assert flags["agent_enrichment_source"] == "override"


def test_override_critique_on(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "hybrid")
    from app.config import get_settings

    get_settings.cache_clear()
    clear_pipeline_flag_overrides()
    assert critique_enabled() is False
    set_pipeline_flags(digest_critique=True)
    assert critique_enabled() is True
