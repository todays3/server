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


def env_file_paths() -> tuple[str, ...]:
    """Load only `.env.development` or `.env.production`. Plain `.env` is unused."""
    path = _SERVER_ROOT / f".env.{env_mode()}"
    return (str(path),) if path.is_file() else ()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file_encoding="utf-8", extra="ignore")

    app_name: str = "오늘의 3"
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
    llm_provider: str = "groq"  # groq | openai | custom
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""

    # Auth endpoint rate limit (per IP + path)
    rate_limit_auth_max: int = 30
    rate_limit_auth_window_seconds: float = 60.0
    rate_limit_hooks_max: int = 60
    rate_limit_hooks_window_seconds: float = 60.0

    # Android Tasker/MacroDroid → POST /api/v1/hooks/notifications
    # Empty secret disables the endpoint (503).
    flash_webhook_secret: str = ""
    flash_alert_user_email: str = ""

    @property
    def allowed_cors_origins(self) -> list[str]:
        extras = [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
        return list(dict.fromkeys([self.frontend_origin, *extras]))

    @property
    def kakao_configured(self) -> bool:
        return bool(self.kakao_rest_api_key)

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key)

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
        if self.llm_model:
            return self.llm_model
        if self.llm_provider == "openai":
            return "gpt-4o-mini"
        # Groq free-tier default
        return "llama-3.3-70b-versatile"

    @property
    def flash_webhook_configured(self) -> bool:
        return bool(self.flash_webhook_secret)

    @property
    def flash_alert_target_email(self) -> str:
        return (self.flash_alert_user_email or self.admin_email).strip().lower()

    @property
    def api_prefix(self) -> str:
        return f"/api/{self.api_version}"


@lru_cache
def get_settings() -> Settings:
    return Settings(_env_file=env_file_paths())
