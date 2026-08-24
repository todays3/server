"""Browser 5xx failures redirect to the SPA /error page."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.http_errors import prefer_html_error, spa_error_redirect
from app.main import app
from starlette.requests import Request


def test_spa_error_redirect_points_at_error_route():
    res = spa_error_redirect(503)
    assert res.status_code == 302
    assert "/error?" in res.headers["location"]
    assert "code=503" in res.headers["location"]


def test_prefer_html_for_kakao_callback_and_navigate():
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/api/v1/auth/kakao/callback",
        "raw_path": b"/api/v1/auth/kakao/callback",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 123),
        "server": ("test", 443),
    }
    assert prefer_html_error(Request(scope)) is True

    scope2 = {
        **scope,
        "path": "/api/v1/health",
        "raw_path": b"/api/v1/health",
        "headers": [(b"accept", b"application/json")],
    }
    assert prefer_html_error(Request(scope2)) is False

    scope3 = {
        **scope2,
        "headers": [(b"accept", b"text/html,application/xhtml+xml")],
    }
    assert prefer_html_error(Request(scope3)) is True


@pytest.mark.asyncio
async def test_unhandled_api_error_stays_json_for_fetch():
    @app.get("/api/v1/__test_boom_json")
    def _boom():
        raise RuntimeError("boom")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            "/api/v1/__test_boom_json",
            headers={"Accept": "application/json"},
        )
    assert res.status_code == 500
    assert res.json()["detail"] == "Internal Server Error"


@pytest.mark.asyncio
async def test_unhandled_error_redirects_browser_to_spa():
    @app.get("/api/v1/__test_boom_html")
    def _boom():
        raise RuntimeError("boom")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
        res = await client.get(
            "/api/v1/__test_boom_html",
            headers={"Accept": "text/html", "Sec-Fetch-Mode": "navigate"},
        )
    assert res.status_code == 302
    assert "/error?" in res.headers["location"]
    assert "code=500" in res.headers["location"]
