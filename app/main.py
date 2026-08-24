from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.middleware.cors import CORSMiddleware

from app.bootstrap import ensure_schema, seed_accounts
from app.config import get_settings
from app.db import Base, engine
from app.http_errors import http_exception_handler, unhandled_exception_handler
from app.routers import admin, auth, digests, finance, hooks, kakao, kakao_webhook, notes, prefs, push, sources
from app.schemas import HealthOut
from app.services.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    ensure_schema()
    seed_accounts()
    from app.services.finance_db import init_finance_db
    from app.services.dart_db import init_dart_db
    from app.services.faiss_store import warmup_faiss

    await run_in_threadpool(init_finance_db)
    await run_in_threadpool(init_dart_db)
    if get_settings().agent_faiss_enabled:
        await run_in_threadpool(warmup_faiss)
    start_scheduler()
    yield
    stop_scheduler()


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version=settings.api_version,
    lifespan=lifespan,
    openapi_url=f"{settings.api_prefix}/openapi.json",
    docs_url=f"{settings.api_prefix}/docs",
    redoc_url=f"{settings.api_prefix}/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(RequestValidationError, request_validation_exception_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

# API v1 — versioned surface (fastapi-pro)
api_v1 = settings.api_prefix
app.include_router(auth.router, prefix=api_v1)
app.include_router(admin.router, prefix=api_v1)
app.include_router(prefs.router, prefix=api_v1)
app.include_router(digests.router, prefix=api_v1)
app.include_router(kakao.router, prefix=api_v1)
app.include_router(hooks.router, prefix=api_v1)
app.include_router(push.router, prefix=api_v1)
app.include_router(sources.router, prefix=api_v1)
app.include_router(notes.router, prefix=api_v1)
app.include_router(finance.router, prefix=api_v1)
app.include_router(kakao_webhook.router)
app.include_router(kakao_webhook.router, prefix=api_v1)


@app.get(f"{api_v1}/health", response_model=HealthOut, tags=["health"])
def health() -> HealthOut:
    return HealthOut(
        status="ok",
        kakao_configured=settings.kakao_configured,
        llm_configured=settings.llm_configured,
        llm_provider=settings.llm_provider,
        sources="rss+youtube",
        scheduler="running",
        firebase_configured=settings.firebase_send_configured,
    )


@app.middleware("http")
async def add_api_version_header(request, call_next):
    response = await call_next(request)
    response.headers["X-API-Version"] = settings.api_version
    return response
