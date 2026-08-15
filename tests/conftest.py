"""Shared pytest hooks. File-local fixtures still win over these."""

from __future__ import annotations

import pytest

from app.config import get_settings

INTEGRATION_FILES = frozenset(
    {
        "test_auth_oauth.py",
        "test_hooks_notification.py",
        "test_admin_digest_preview.py",
        "test_admin_source_probes.py",
        "test_admin_overview.py",
        "test_sources_catalog_api.py",
        "test_prefs_api.py",
        "test_user_flows_api.py",
        "test_admin_crawl_runs.py",
    }
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.path.name in INTEGRATION_FILES:
            item.add_marker(pytest.mark.integration)
        else:
            item.add_marker(pytest.mark.unit)


@pytest.fixture(autouse=True)
def _roomy_rate_limits(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_AUTH_MAX", "10000")
    monkeypatch.setenv("RATE_LIMIT_HOOKS_MAX", "10000")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
