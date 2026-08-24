"""TTFT / TPS and cheap RAG scores. No extra LLM judge (16GB host)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import LlmUsage

log = logging.getLogger(__name__)

_TOKEN = re.compile(r"[A-Za-z]{2,}|\d{5,}|[\uac00-\ud7a3]{2,}")
_NUM = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d+")


@dataclass(frozen=True)
class TimingMetrics:
    ttft_ms: int
    total_ms: int
    generate_ms: int
    tps: float
    streamed: bool


@dataclass(frozen=True)
class RagScores:
    faithfulness: float
    hallucination_rate: float
    answer_relevance: float
    context_precision: float


def estimate_completion_tokens(text: str) -> int:
    raw = (text or "").strip()
    if not raw:
        return 0
    return max(1, int(round(len(raw) / 2)))


def compute_timing(
    *,
    started_at: float,
    first_token_at: float | None,
    ended_at: float,
    completion_tokens: int,
    streamed: bool,
) -> TimingMetrics:
    total_ms = max(0, int(round((ended_at - started_at) * 1000)))
    if streamed and first_token_at is not None:
        ttft_ms = max(0, int(round((first_token_at - started_at) * 1000)))
        generate_ms = max(0, int(round((ended_at - first_token_at) * 1000)))
    else:
        ttft_ms = total_ms
        generate_ms = total_ms
        streamed = False
    seconds = generate_ms / 1000.0
    tps = float(completion_tokens) / seconds if seconds > 0 and completion_tokens > 0 else 0.0
    return TimingMetrics(
        ttft_ms=ttft_ms,
        total_ms=total_ms,
        generate_ms=generate_ms,
        tps=round(tps, 3),
        streamed=streamed,
    )


def percentile_float(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * (p / 100.0)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return float(ordered[lo] * (1.0 - frac) + ordered[hi] * frac)


def tokenize(text: str) -> set[str]:
    return {match.group(0).lower() for match in _TOKEN.finditer(text or "")}


def extract_numbers(text: str) -> list[str]:
    found: list[str] = []
    for match in _NUM.finditer(text or ""):
        digits = match.group(0).replace(",", "")
        if "." in digits:
            digits = digits.rstrip("0").rstrip(".")
        compact = digits.replace(".", "")
        if len(compact) <= 4:
            continue
        found.append(digits)
    return found


def _number_grounded(num: str, grounded: list[str]) -> bool:
    for raw in grounded:
        target = (raw or "").replace(",", "")
        if not target:
            continue
        if num == target or num in target or target in num:
            return True
    return False


def score_rag(
    *,
    question: str,
    answer: str,
    contexts: list[str],
    grounded_values: list[str] | None = None,
) -> RagScores:
    q_tokens = tokenize(question)
    a_tokens = tokenize(answer)
    ctx_tokens: set[str] = set()
    for chunk in contexts:
        ctx_tokens |= tokenize(chunk)

    if q_tokens:
        relevance = len(q_tokens & a_tokens) / len(q_tokens)
    else:
        relevance = 0.0

    if contexts:
        relevant_chunks = 0
        for chunk in contexts:
            chunk_tokens = tokenize(chunk)
            if chunk_tokens & q_tokens or chunk_tokens & a_tokens:
                relevant_chunks += 1
        precision = relevant_chunks / len(contexts)
    else:
        precision = 0.0

    numbers = extract_numbers(answer)
    grounded = [str(v) for v in (grounded_values or [])]
    if numbers and grounded:
        bad = sum(1 for num in numbers if not _number_grounded(num, grounded + list(contexts)))
        hallucination = bad / len(numbers)
    elif a_tokens and ctx_tokens:
        unsupported = a_tokens - ctx_tokens - q_tokens
        hallucination = len(unsupported) / len(a_tokens)
    else:
        hallucination = 0.0 if a_tokens else 1.0

    hallucination = min(1.0, max(0.0, hallucination))
    return RagScores(
        faithfulness=round(1.0 - hallucination, 4),
        hallucination_rate=round(hallucination, 4),
        answer_relevance=round(min(1.0, max(0.0, relevance)), 4),
        context_precision=round(min(1.0, max(0.0, precision)), 4),
    )


def apply_quality(
    row: LlmUsage,
    timing: TimingMetrics,
    rag: RagScores | None = None,
) -> LlmUsage:
    row.ttft_ms = int(timing.ttft_ms)
    row.total_ms = int(timing.total_ms)
    row.tps = float(timing.tps)
    row.streamed = bool(timing.streamed)
    if rag is not None:
        row.faithfulness = float(rag.faithfulness)
        row.hallucination_rate = float(rag.hallucination_rate)
        row.answer_relevance = float(rag.answer_relevance)
        row.context_precision = float(rag.context_precision)
    return row


def persist_quality_row(
    db: Session,
    *,
    user_id: int,
    purpose: str,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    success: bool,
    error_message: str,
    timing: TimingMetrics,
    rag: RagScores | None = None,
) -> LlmUsage | None:
    try:
        row = LlmUsage(
            user_id=user_id,
            purpose=purpose,
            provider=provider,
            model=model,
            prompt_tokens=int(prompt_tokens),
            completion_tokens=int(completion_tokens),
            total_tokens=int(prompt_tokens) + int(completion_tokens),
            success=success,
            error_message=(error_message or "")[:700],
        )
        apply_quality(row, timing, rag)
        db.add(row)
        db.commit()
        return row
    except Exception:
        log.exception("llm quality persist failed")
        try:
            db.rollback()
        except Exception:
            pass
        return None
