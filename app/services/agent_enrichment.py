"""Agentic enrichment middleware: scrape output → Ollama JSON → finance lookup → Kakao footer."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings
from app.models import Preference, User
from app.services.finance_db import replace_digest_actions
from app.services.faiss_store import search_ticker
from app.services.roles import ROLE_LABELS, parse_roles

from app.services.llm_quality import TimingMetrics, compute_timing, estimate_completion_tokens

log = logging.getLogger(__name__)

OLLAMA_OPTIONS = {"num_ctx": 4096, "num_predict": 512, "num_thread": 2}

PROFESSION_BY_ROLE: dict[str, str] = {
    "investor": "Retail Investor",
    "stock_analyst": "Retail Investor",
    "developer": "Developer",
    "doctor": "Doctor",
    "semiconductor": "Semiconductor Engineer",
}


class ItemEnrichment(BaseModel):
    core_insight: str = ""
    action_item: str = ""
    relevant_ticker: str = ""


class EnrichmentBatch(BaseModel):
    items: list[ItemEnrichment] = Field(default_factory=list)


def profession_for(user: User, pref: Preference) -> str:
    roles = parse_roles(getattr(pref, "roles", "") or "")
    for role in roles:
        mapped = PROFESSION_BY_ROLE.get(role)
        if mapped:
            return mapped
    occupation = (getattr(user, "occupation", "") or "").strip()
    if "의사" in occupation or "doctor" in occupation.lower():
        return "Doctor"
    if "반도체" in occupation or "semiconductor" in occupation.lower():
        return "Semiconductor Engineer"
    if "개발" in occupation or "develop" in occupation.lower():
        return "Developer"
    if occupation:
        return occupation
    return "Retail Investor"


def _article_rows(items: list[dict[str, str]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in items:
        if (item.get("kind") or "") == "종목":
            continue
        if not (item.get("title") or item.get("blurb")):
            continue
        rows.append(item)
        if len(rows) >= 3:
            break
    return rows


def build_prompt(profession: str, articles: list[dict[str, str]], *, role_label: str = "") -> str:
    lines = []
    for i, item in enumerate(articles, start=1):
        lines.append(f"{i}. {item.get('title') or ''} — {item.get('blurb') or ''}")
    block = "\n".join(lines) or "(none)"
    who = f"{profession} ({role_label})" if role_label else profession
    return (
        f"You assist a {who}.\n"
        "JSON only:\n"
        '{"items":[{"core_insight":"...","action_item":"...","relevant_ticker":"005930"}]}\n'
        "relevant_ticker is a KOSPI/KOSDAQ 6-digit code or empty.\n"
        "core_insight and action_item must be Korean 합니다/습니다 for Kakao. "
        "action_item is one concrete next step for that profession.\n"
        f"Articles:\n{block}\n"
    )


def _parse_batch(text: str, expected: int) -> list[ItemEnrichment]:
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.I | re.M)
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start : end + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid enrichment json") from exc
    batch = EnrichmentBatch.model_validate(data)
    rows = list(batch.items)
    while len(rows) < expected:
        rows.append(ItemEnrichment())
    return rows[:expected]


def _ollama_base_url() -> str:
    settings = get_settings()
    url = (settings.llm_local_base_url or "http://127.0.0.1:11434/v1").rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    return url or "http://127.0.0.1:11434"


@dataclass
class GenerateMetrics:
    text: str
    timing: TimingMetrics
    completion_tokens: int


async def infer_with_metrics(prompt: str) -> GenerateMetrics:
    """Call Ollama /api/generate with hardware caps. Prefers streaming for TTFT."""
    settings = get_settings()
    payload: dict[str, Any] = {
        "model": settings.resolved_llm_local_model,
        "prompt": prompt,
        "stream": False,
        "options": dict(OLLAMA_OPTIONS),
    }
    timeout = httpx.Timeout(45.0, connect=2.0)
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=timeout) as client:
        if hasattr(client, "stream"):
            try:
                stream_payload = dict(payload)
                stream_payload["stream"] = True
                parts: list[str] = []
                first_at: float | None = None
                eval_count = 0
                async with client.stream("POST", f"{_ollama_base_url()}/api/generate", json=stream_payload) as res:
                    res.raise_for_status()
                    async for line in res.aiter_lines():
                        if not line.strip():
                            continue
                        body = json.loads(line)
                        chunk = str(body.get("response") or "")
                        if chunk:
                            if first_at is None:
                                first_at = time.perf_counter()
                            parts.append(chunk)
                        if body.get("done"):
                            eval_count = int(body.get("eval_count") or 0)
                text = "".join(parts)
                tokens = eval_count or estimate_completion_tokens(text)
                timing = compute_timing(
                    started_at=started,
                    first_token_at=first_at,
                    ended_at=time.perf_counter(),
                    completion_tokens=tokens,
                    streamed=first_at is not None,
                )
                return GenerateMetrics(text=text, timing=timing, completion_tokens=tokens)
            except (httpx.HTTPError, ValueError, TypeError, OSError):
                log.warning("ollama stream unavailable, using non-stream generate")
        res = await client.post(f"{_ollama_base_url()}/api/generate", json=payload)
        res.raise_for_status()
        body = res.json()
    text = str(body.get("response") or "")
    tokens = int(body.get("eval_count") or 0) or estimate_completion_tokens(text)
    timing = compute_timing(
        started_at=started,
        first_token_at=None,
        ended_at=time.perf_counter(),
        completion_tokens=tokens,
        streamed=False,
    )
    return GenerateMetrics(text=text, timing=timing, completion_tokens=tokens)


async def infer_enrichment(prompt: str) -> str:
    """Call Ollama /api/generate with hardware caps. Raises on transport errors."""
    return (await infer_with_metrics(prompt)).text


def _run_coro(coro):
    import asyncio
    import concurrent.futures

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result(timeout=60)


def format_enrichment_footer(enriched: list[tuple[dict[str, str], ItemEnrichment, str]]) -> str:
    if not enriched:
        return ""
    lines = ["", "실행 포인트"]
    for idx, (item, row, finance) in enumerate(enriched, start=1):
        title = (item.get("title") or "")[:40]
        action = (row.action_item or row.core_insight or "").strip()[:80]
        extra = f" · {finance}" if finance else ""
        ticker = f" · {row.relevant_ticker}" if row.relevant_ticker else ""
        lines.append(f"{idx}. {title}{ticker} — {action}{extra}")
    lines.append('저장하려면 「1번 저장」처럼 답장하세요.')
    return "\n".join(lines)


def enrich_digest_payload(
    user: User,
    pref: Preference,
    items: list[dict[str, str]],
    body: str,
) -> tuple[list[dict[str, str]], str]:
    """Append profession-tailored actions. On any failure return the original payload."""
    return _run_coro(_enrich_digest_payload_async(user, pref, items, body))


async def _enrich_digest_payload_async(
    user: User,
    pref: Preference,
    items: list[dict[str, str]],
    body: str,
) -> tuple[list[dict[str, str]], str]:
    from app.services.pipeline_flags import enrichment_enabled

    if not enrichment_enabled():
        return items, body
    settings = get_settings()
    articles = _article_rows(items)
    if not articles:
        return items, body
    try:
        roles = parse_roles(getattr(pref, "roles", "") or "")
        role_label = ROLE_LABELS.get(roles[0], "") if roles else ""
        prompt = build_prompt(profession_for(user, pref), articles, role_label=role_label)
        metrics = await infer_with_metrics(prompt)
        raw = metrics.text
        parsed = _parse_batch(raw, len(articles))
        footer_rows: list[tuple[dict[str, str], ItemEnrichment, str]] = []
        actions: list[tuple[int, str, str, str]] = []
        for idx, (item, row) in enumerate(zip(articles, parsed, strict=False), start=1):
            highlight = None
            try:
                highlight = search_ticker(row.relevant_ticker or f"{item.get('title')} {row.core_insight}")
            except Exception:
                log.exception("finance/faiss lookup failed")
            if highlight is not None and not row.relevant_ticker:
                row = row.model_copy(update={"relevant_ticker": highlight.ticker})
            finance = highlight.blurb() if highlight else ""
            item["core_insight"] = row.core_insight
            item["action_item"] = row.action_item
            item["relevant_ticker"] = row.relevant_ticker
            if finance:
                item["finance_blurb"] = finance
            footer_rows.append((item, row, finance))
            actions.append((idx, row.action_item or row.core_insight, row.relevant_ticker, row.core_insight))
        replace_digest_actions(int(user.id), actions)
        try:
            from app.db import SessionLocal
            from app.services.llm_quality import persist_quality_row, score_rag

            contexts = [f"{item.get('title') or ''} {item.get('blurb') or ''}" for item in articles]
            rag = score_rag(question="실행 포인트", answer=raw, contexts=contexts)
            qdb = SessionLocal()
            try:
                persist_quality_row(
                    qdb,
                    user_id=int(user.id),
                    purpose="agent_enrichment",
                    provider="ollama",
                    model=settings.resolved_llm_local_model,
                    prompt_tokens=0,
                    completion_tokens=metrics.completion_tokens,
                    success=True,
                    error_message="",
                    timing=metrics.timing,
                    rag=rag,
                )
            finally:
                qdb.close()
        except Exception:
            log.warning("enrichment quality persist skipped", exc_info=True)
        footer = format_enrichment_footer(footer_rows)
        if not footer:
            return items, body
        return items, f"{body.rstrip()}\n{footer}"
    except (httpx.HTTPError, ValidationError, ValueError, TimeoutError, OSError) as exc:
        log.warning("agent enrichment skipped: %s", exc)
        return items, body
    except Exception:
        log.exception("agent enrichment failed; sending original digest")
        return items, body
