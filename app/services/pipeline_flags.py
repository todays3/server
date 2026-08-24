"""Runtime on/off for agentic digest steps. Overrides persist under /data."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from app.config import get_settings

log = logging.getLogger(__name__)

_lock = threading.Lock()
_overrides: dict[str, bool] = {}
_loaded = False

FLAG_KEYS = ("agent_enrichment", "digest_critique")


def _flags_path() -> Path:
    settings = get_settings()
    raw = (settings.database_url or "").replace("sqlite:///", "")
    if raw.startswith("/") or (len(raw) > 2 and raw[1] == ":"):
        # absolute sqlite path → sibling flags file
        base = Path(raw).resolve().parent
    else:
        base = Path("/data")
    if not base.exists():
        base = Path(".")
    return base / "pipeline_flags.json"


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        path = _flags_path()
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for key in FLAG_KEYS:
                        if key in data and isinstance(data[key], bool):
                            _overrides[key] = data[key]
            except (OSError, json.JSONDecodeError, TypeError):
                log.warning("pipeline flags load failed", exc_info=True)
        _loaded = True


def _persist() -> None:
    path = _flags_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_overrides, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        log.warning("pipeline flags persist failed", exc_info=True)


def enrichment_enabled() -> bool:
    _ensure_loaded()
    with _lock:
        if "agent_enrichment" in _overrides:
            return bool(_overrides["agent_enrichment"])
    return bool(get_settings().agent_enrichment_enabled)


def critique_enabled() -> bool:
    """Feedback loop: critique → re-curate once. Default off for local/hybrid."""
    _ensure_loaded()
    with _lock:
        if "digest_critique" in _overrides:
            return bool(_overrides["digest_critique"])
    settings = get_settings()
    if settings.digest_critique_enabled is not None:
        return bool(settings.digest_critique_enabled)
    provider = (settings.llm_provider or "").strip().lower()
    return provider not in {"ollama", "hybrid"}


def get_pipeline_flags() -> dict:
    _ensure_loaded()
    return {
        "agent_enrichment": enrichment_enabled(),
        "digest_critique": critique_enabled(),
        "agent_enrichment_source": (
            "override" if "agent_enrichment" in _overrides else "env"
        ),
        "digest_critique_source": (
            "override" if "digest_critique" in _overrides else "auto"
        ),
        "notes": {
            "agent_enrichment": "본문 포맷 직후 Ollama로 실행 포인트·티커를 붙입니다. 끄면 포맷이 즉시 끝납니다.",
            "digest_critique": "큐레이션 후 비평 → 한 번 더 고릅니다. 로컬/하이브리드에서는 기본 꺼짐.",
        },
    }


def set_pipeline_flags(
    *,
    agent_enrichment: bool | None = None,
    digest_critique: bool | None = None,
) -> dict:
    _ensure_loaded()
    with _lock:
        if agent_enrichment is not None:
            _overrides["agent_enrichment"] = bool(agent_enrichment)
        if digest_critique is not None:
            _overrides["digest_critique"] = bool(digest_critique)
        _persist()
    return get_pipeline_flags()


def clear_pipeline_flag_overrides() -> dict:
    """Tests / reset: drop runtime overrides (env defaults apply)."""
    global _loaded
    with _lock:
        _overrides.clear()
        _loaded = True
        path = _flags_path()
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass
    return get_pipeline_flags()
