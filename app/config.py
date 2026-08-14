from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "오늘의 3"
    api_version: str = "v1"
    secret_key: str = "dev-secret-change-me"
    database_url: str = "sqlite:///./tome.db"
    frontend_origin: str = "http://localhost:5173"
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

    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"

    # Auth endpoint rate limit (per IP + path)
    rate_limit_auth_max: int = 30
    rate_limit_auth_window_seconds: float = 60.0

    @property
    def kakao_configured(self) -> bool:
        return bool(self.kakao_rest_api_key)

    @property
    def api_prefix(self) -> str:
        return f"/api/{self.api_version}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
