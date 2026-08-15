from pathlib import Path

from app.config import env_file_paths, env_mode, get_settings

_SERVER_DIR = Path(__file__).resolve().parent.parent


def _use_example_env(tmp_path: Path, monkeypatch, mode: str) -> None:
    """CI has no gitignored `.env.*`; load the committed example as that mode file."""
    src = _SERVER_DIR / f".env.{mode}.example"
    dest = tmp_path / f".env.{mode}"
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr("app.config._SERVER_ROOT", tmp_path)
    monkeypatch.setenv("APP_ENV", mode)
    monkeypatch.delenv("FRONTEND_ORIGIN", raising=False)
    monkeypatch.delenv("KAKAO_REDIRECT_URI", raising=False)


def test_allowed_cors_origins_includes_frontend_and_extras(monkeypatch):
    monkeypatch.setenv("FRONTEND_ORIGIN", "https://todays3.muhak.store")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173, http://127.0.0.1:5173")
    get_settings.cache_clear()
    try:
        origins = get_settings().allowed_cors_origins
        assert origins[0] == "https://todays3.muhak.store"
        assert "http://localhost:5173" in origins
        assert "http://127.0.0.1:5173" in origins
    finally:
        get_settings.cache_clear()


def test_env_mode_defaults_to_development(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    assert env_mode() == "development"
    monkeypatch.setenv("APP_ENV", "prod")
    assert env_mode() == "production"


def test_env_file_paths_are_mode_only(monkeypatch, tmp_path):
    monkeypatch.setattr("app.config._SERVER_ROOT", tmp_path)
    (tmp_path / ".env").write_text("FRONTEND_ORIGIN=https://from-plain-env.example\n", encoding="utf-8")
    (tmp_path / ".env.development").write_text(
        "FRONTEND_ORIGIN=http://localhost:5173\n", encoding="utf-8"
    )
    monkeypatch.setenv("APP_ENV", "development")
    names = [Path(p).name for p in env_file_paths()]
    assert names == [".env.development"]


def test_plain_env_is_not_loaded(monkeypatch, tmp_path):
    monkeypatch.setattr("app.config._SERVER_ROOT", tmp_path)
    (tmp_path / ".env").write_text(
        "FRONTEND_ORIGIN=https://from-plain-env.example\n"
        "KAKAO_REDIRECT_URI=https://from-plain-env.example/callback\n",
        encoding="utf-8",
    )
    (tmp_path / ".env.development").write_text(
        "FRONTEND_ORIGIN=http://localhost:5173\n"
        "KAKAO_REDIRECT_URI=http://localhost:5173/api/v1/auth/kakao/callback\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("FRONTEND_ORIGIN", raising=False)
    monkeypatch.delenv("KAKAO_REDIRECT_URI", raising=False)
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.frontend_origin == "http://localhost:5173"
        assert "localhost:5173" in settings.kakao_redirect_uri
    finally:
        get_settings.cache_clear()


def test_development_env_file_uses_localhost(monkeypatch, tmp_path):
    _use_example_env(tmp_path, monkeypatch, "development")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.frontend_origin == "http://localhost:5173"
        assert "localhost:5173" in settings.kakao_redirect_uri
        assert all(path.endswith(".env.development") for path in env_file_paths())
    finally:
        get_settings.cache_clear()


def test_production_env_file_uses_public_origin(monkeypatch, tmp_path):
    _use_example_env(tmp_path, monkeypatch, "production")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.frontend_origin == "https://todays3.muhak.store"
        assert settings.kakao_redirect_uri.startswith("https://todays3.muhak.store")
        assert all(path.endswith(".env.production") for path in env_file_paths())
    finally:
        get_settings.cache_clear()
