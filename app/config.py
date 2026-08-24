from functools import lru_cache
from os import getenv
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_SERVER_ROOT = Path(__file__).resolve().parent.parent


def env_mode() -> str:
    raw = getenv("APP_ENV", "development").strip().lower()
    if raw in ("prod", "production"):
        return "production"
    return "development"


def _is_ollama_model_tag(name: str) -> bool:
    """Ollama uses name:tag. Groq/OpenAI use org/model without a colon tag."""
    trimmed = (name or "").strip()
    if not trimmed:
        return False
    lowered = trimmed.lower()
    if lowered.startswith("qwen"):
        return True
    return ":" in trimmed and "/" not in trimmed.split(":", 1)[0]


def env_file_paths() -> tuple[str, ...]:
    """Load only `.env.development` or `.env.production`. Plain `.env` is unused."""
    path = _SERVER_ROOT / f".env.{env_mode()}"
    return (str(path),) if path.is_file() else ()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file_encoding="utf-8", extra="ignore")

    app_name: str = "하루만장"
    api_version: str = "v1"
    secret_key: str = "dev-secret-change-me"
    database_url: str = "sqlite:///./tome.db"
    frontend_origin: str = "http://localhost:5173"
    # Extra CORS allowlist (comma-separated). frontend_origin is always included.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    default_timezone: str = "Asia/Seoul"

    # Seeded on startup if missing
    admin_email: str = "admin@example.com"
    admin_password: str = "admin12345"
    admin_name: str = "관리자"

    test_user_email: str = "user@example.com"
    test_user_password: str = "user12345"
    test_user_name: str = "테스트유저"

    kakao_rest_api_key: str = ""
    kakao_client_secret: str = ""
    # Must match Kakao Developers Redirect URI (OAuth signup/login callback)
    kakao_redirect_uri: str = "http://localhost:8000/api/v1/auth/kakao/callback"

    # Free-tier friendly default: Groq OpenAI-compatible API
    # Get a key at https://console.groq.com (free) then set LLM_API_KEY
    llm_provider: str = "groq"  # groq | openai | custom | ollama | hybrid
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    # Groq Free openai/gpt-oss-120b: 30 RPM, 8K TPM. 0 disables that dimension.
    llm_tpm_limit: int = 8000
    llm_rpm_limit: int = 30
    llm_max_concurrent: int = 1
    llm_rate_headroom: float = 0.85
    llm_max_retries: int = 3
    llm_retry_cap_seconds: float = 90.0
    # gpt-oss spends completion tokens on hidden reasoning; low keeps JSON in content.
    llm_reasoning_effort: str = "low"
    # Local Qwen2.5-1.5B (Ollama). hybrid = local then Groq fallback.
    llm_local_enabled: bool = False
    llm_local_base_url: str = "http://127.0.0.1:11434/v1"
    llm_local_model: str = "qwen2.5:1.5b"
    llm_local_api_key: str = "ollama"
    llm_local_num_ctx: int = 4096
    llm_local_max_concurrent: int = 1
    llm_local_num_thread: int = 2
    llm_local_mlock: bool = True
    llm_local_timeout_seconds: float = 25.0
    llm_local_connect_timeout_seconds: float = 2.0
    llm_local_unhealthy_skip_seconds: float = 60.0
    # Digest curate/critique: prefer Groq. Local Qwen often spends 1–5 min then returns
    # broken JSON, so hybrid-local-first makes E2E/schedule look hung.
    llm_digest_prefer_remote: bool = True

    # Auth endpoint rate limit (per IP + path)
    rate_limit_auth_max: int = 30
    rate_limit_auth_window_seconds: float = 60.0
    rate_limit_hooks_max: int = 60
    rate_limit_hooks_window_seconds: float = 60.0
    rate_limit_notes_max: int = 3
    rate_limit_notes_window_seconds: float = 60.0
    rate_limit_note_hearts_max: int = 30
    rate_limit_note_hearts_window_seconds: float = 60.0
    notes_daily_max: int = 5
    notes_min_interval_seconds: float = 120.0
    notes_max_open: int = 20
    notes_duplicate_window_hours: float = 24.0

    youtube_api_key: str = ""

    # Firebase Cloud Messaging (HTTP v1). Empty → skip push, Kakao still sends.
    firebase_project_id: str = ""
    firebase_client_email: str = ""
    firebase_private_key: str = ""
    firebase_web_api_key: str = ""
    firebase_web_app_id: str = ""
    firebase_web_messaging_sender_id: str = ""
    firebase_web_vapid_key: str = ""
    firebase_web_auth_domain: str = ""

    # Android Tasker/MacroDroid → POST /api/v1/hooks/notifications
    # Empty secret disables the endpoint (503).
    flash_webhook_secret: str = ""
    flash_alert_user_email: str = ""

    # Agentic enrichment (Scrape → enrich → Kakao). Off in tests via conftest.
    agent_enrichment_enabled: bool = True
    # Critique → re-curate once. None = auto (off for ollama/hybrid).
    digest_critique_enabled: bool | None = None
    agent_faiss_enabled: bool = False
    agent_embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    finance_db_path: str = ""
    notion_api_key: str = ""
    notion_database_id: str = ""
    kakao_webhook_secret: str = ""

    # DART XBRL + PDF FTS5 (separate dart_financials.db). Ingest/OCR off by default.
    dart_api_key: str = ""
    dart_db_path: str = ""
    dart_ingest_enabled: bool = False
    dart_ocr_enabled: bool = False
    dart_tickers: str = "005930,000660"

    # Scheduled digest: prepare early, send at/after slot (never before slot).
    schedule_prep_lead_minutes: int = 30
    schedule_send_grace_minutes: int = 60
    # Keep trying unsent drafts this long after the slot (must be >= grace).
    schedule_send_catchup_minutes: int = 180

    @property
    def allowed_cors_origins(self) -> list[str]:
        extras = [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
        return list(dict.fromkeys([self.frontend_origin, *extras]))

    @property
    def kakao_configured(self) -> bool:
        return bool(self.kakao_rest_api_key)

    @property
    def llm_local_ready(self) -> bool:
        provider = (self.llm_provider or "").strip().lower()
        if provider == "ollama":
            return True
        if provider == "hybrid":
            return True
        return bool(self.llm_local_enabled)

    @property
    def remote_llm_ready(self) -> bool:
        provider = (self.llm_provider or "").strip().lower()
        if provider == "ollama":
            return False
        return bool(self.llm_api_key)

    @property
    def llm_configured(self) -> bool:
        return self.llm_local_ready or self.remote_llm_ready

    @property
    def resolved_llm_local_base_url(self) -> str:
        return (self.llm_local_base_url or "http://127.0.0.1:11434/v1").rstrip("/")

    @property
    def resolved_llm_local_model(self) -> str:
        return (self.llm_local_model or "qwen2.5:1.5b").strip()

    @property
    def resolved_remote_provider(self) -> str:
        provider = (self.llm_provider or "groq").strip().lower()
        if provider in {"openai", "custom"}:
            return provider
        return "groq"

    @property
    def resolved_llm_base_url(self) -> str:
        if self.llm_base_url:
            return self.llm_base_url
        if self.llm_provider == "groq":
            return "https://api.groq.com/openai/v1"
        if self.llm_provider == "openai":
            return "https://api.openai.com/v1"
        return "https://api.groq.com/openai/v1"

    @property
    def resolved_llm_model(self) -> str:
        name = (self.llm_model or "").strip()
        provider = (self.llm_provider or "").strip().lower()
        # Qwen/Ollama tags are local-only. Groq (and hybrid fallback) never receive them.
        if name:
            if _is_ollama_model_tag(name) and provider in {"", "groq", "hybrid"}:
                return "openai/gpt-oss-120b"
            return name
        if provider == "openai":
            return "gpt-4o-mini"
        # Groq retired llama-3.3-70b-versatile on 2026-08-16.
        return "openai/gpt-oss-120b"

    @property
    def youtube_configured(self) -> bool:
        return bool(self.youtube_api_key)

    @property
    def firebase_send_configured(self) -> bool:
        return bool(self.firebase_project_id and self.firebase_client_email and self.firebase_private_key)

    @property
    def firebase_web_configured(self) -> bool:
        return bool(
            self.firebase_project_id
            and self.firebase_web_api_key
            and self.firebase_web_app_id
            and self.firebase_web_messaging_sender_id
            and self.firebase_web_vapid_key
        )

    @property
    def firebase_private_key_pem(self) -> str:
        return (self.firebase_private_key or "").replace("\\n", "\n")

    @property
    def firebase_auth_domain(self) -> str:
        if self.firebase_web_auth_domain:
            return self.firebase_web_auth_domain
        if self.firebase_project_id:
            return f"{self.firebase_project_id}.firebaseapp.com"
        return ""

    @property
    def flash_webhook_configured(self) -> bool:
        return bool(self.flash_webhook_secret)

    @property
    def kakao_webhook_configured(self) -> bool:
        return bool(self.kakao_webhook_secret)

    @property
    def notion_configured(self) -> bool:
        return bool(self.notion_api_key and self.notion_database_id)

    @property
    def flash_alert_target_email(self) -> str:
        return (self.flash_alert_user_email or self.admin_email).strip().lower()

    @property
    def api_prefix(self) -> str:
        return f"/api/{self.api_version}"


@lru_cache
def get_settings() -> Settings:
    return Settings(_env_file=env_file_paths())
