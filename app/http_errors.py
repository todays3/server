"""Map serious server failures to the branded SPA /error page for browser navigations."""

from __future__ import annotations

import logging
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.responses import Response

from app.config import get_settings

log = logging.getLogger("uvicorn.error")


def prefer_html_error(request: Request) -> bool:
    """True for full-page browser navigations; false for XHR/fetch API clients."""
    path = request.url.path or ""
    if path.rstrip("/").endswith("/auth/kakao/callback"):
        return True
    if (request.headers.get("sec-fetch-mode") or "").lower() == "navigate":
        return True
    accept = (request.headers.get("accept") or "").lower()
    if "text/html" in accept:
        return True
    return False


def spa_error_redirect(status_code: int = 500) -> RedirectResponse:
    settings = get_settings()
    code = status_code if 500 <= int(status_code) <= 599 else 500
    qs = urlencode({"code": str(code)})
    return RedirectResponse(url=f"{settings.frontend_origin}/error?{qs}", status_code=302)


def json_server_error(status_code: int = 500, detail: str = "Internal Server Error") -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


async def unhandled_exception_handler(request: Request, exc: Exception) -> Response:
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    if prefer_html_error(request):
        return spa_error_redirect(500)
    return json_server_error(500)


async def http_exception_handler(request: Request, exc: HTTPException) -> Response:
    code = int(exc.status_code)
    if code >= 500 and prefer_html_error(request):
        return spa_error_redirect(code)
    detail = exc.detail
    if not isinstance(detail, (str, int, float, bool, dict, list, type(None))):
        detail = str(detail)
    return JSONResponse(status_code=code, content={"detail": detail}, headers=getattr(exc, "headers", None))
