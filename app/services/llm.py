"""OpenAI-compatible LLM client with usage logging."""

from __future__ import annotations

from openai import OpenAI
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import LlmUsage


def chat_completion(
    db: Session,
    *,
    user_id: int,
    purpose: str,
    messages: list[dict[str, str]],
    temperature: float = 0.4,
    max_tokens: int = 900,
) -> tuple[str | None, LlmUsage | None]:
    """Call LLM and persist token usage. Returns (content, usage_row)."""
    settings = get_settings()
    if not settings.llm_configured:
        return None, None

    client = OpenAI(api_key=settings.llm_api_key, base_url=settings.resolved_llm_base_url)
    model = settings.resolved_llm_model
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = (resp.choices[0].message.content or "").strip()
        usage = getattr(resp, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        total_tokens = int(getattr(usage, "total_tokens", 0) or (prompt_tokens + completion_tokens))

        row = LlmUsage(
            user_id=user_id,
            purpose=purpose,
            provider=settings.llm_provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            success=bool(text),
            error_message="" if text else "empty_response",
        )
        db.add(row)
        db.flush()
        return text or None, row
    except Exception as exc:  # noqa: BLE001
        row = LlmUsage(
            user_id=user_id,
            purpose=purpose,
            provider=settings.llm_provider,
            model=model,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            success=False,
            error_message=str(exc)[:500],
        )
        db.add(row)
        db.flush()
        return None, row
