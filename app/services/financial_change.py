"""Compact, XBRL-grounded financial statement change analysis for stock digests."""

from __future__ import annotations

import re
from dataclasses import dataclass
from time import perf_counter
from typing import Iterable

from sqlalchemy.orm import Session

from app.services.dart_db import XbrlFact, list_facts
from app.services.llm import chat_completion

METRICS: tuple[tuple[str, str], ...] = (
    ("revenue", "매출액"),
    ("operating_profit", "영업이익"),
    ("net_income", "당기순이익"),
)

_FORBIDDEN_AI_WORDS = ("매수", "매도", "추천", "사세요", "팔아요", "투자하세요")
_DIGIT_RE = re.compile(r"[0-9０-９%％]")


@dataclass(frozen=True)
class ChangeMetric:
    account: str
    label: str
    previous: int
    current: int
    delta: int
    rate: float | None


@dataclass(frozen=True)
class FinancialChange:
    ticker: str
    previous_year: int
    current_year: int
    metrics: tuple[ChangeMetric, ...]
    missing_labels: tuple[str, ...] = ()
    margin_delta: float | None = None


def _annual_facts(facts: Iterable[XbrlFact]) -> dict[tuple[int, str], XbrlFact]:
    return {
        (fact.year, fact.account): fact
        for fact in facts
        if fact.quarter == 0 and fact.account in {account for account, _ in METRICS}
    }


def load_financial_change(ticker: str, *, path=None) -> FinancialChange | None:
    """Load the newest annual period and its immediately preceding annual period."""
    facts = _annual_facts(list_facts(ticker, limit=200, path=path))
    years = sorted({year for year, _account in facts}, reverse=True)
    if not years:
        return None

    current_year = years[0]
    previous_year = current_year - 1
    metrics: list[ChangeMetric] = []
    missing: list[str] = []
    for account, label in METRICS:
        current = facts.get((current_year, account))
        previous = facts.get((previous_year, account))
        if current is None or previous is None:
            missing.append(label)
            continue
        rate = None if previous.value == 0 else (current.value - previous.value) / previous.value * 100
        metrics.append(
            ChangeMetric(
                account=account,
                label=label,
                previous=previous.value,
                current=current.value,
                delta=current.value - previous.value,
                rate=rate,
            )
        )

    margin_delta: float | None = None
    revenue = {metric.account: metric for metric in metrics}
    operating = revenue.get("operating_profit")
    sales = revenue.get("revenue")
    if operating and sales and operating.previous is not None and sales.previous:
        margin_delta = (
            operating.current / sales.current * 100
            - operating.previous / sales.previous * 100
        )
    return FinancialChange(
        ticker=ticker,
        previous_year=previous_year,
        current_year=current_year,
        metrics=tuple(metrics),
        missing_labels=tuple(missing),
        margin_delta=margin_delta,
    )


def _signed_amount(value: int) -> str:
    sign = "+" if value > 0 else "-" if value < 0 else ""
    amount = abs(value)
    if amount >= 1_000_000_000_000:
        return f"{sign}{amount / 1_000_000_000_000:.1f}조"
    if amount >= 100_000_000:
        return f"{sign}{amount / 100_000_000:.1f}억"
    if amount >= 10_000:
        return f"{sign}{amount / 10_000:.1f}만"
    return f"{sign}{amount:,}원"


def _signed_rate(rate: float | None) -> str:
    if rate is None:
        return "증감률 산출 불가"
    if abs(rate) < 0.05:
        return "0.0%"
    return f"{rate:+.1f}%"


def format_change_summary(change: FinancialChange) -> str:
    if not change.metrics:
        return "재무 변화: 비교 가능한 전년 데이터가 없습니다."
    parts = [
        f"{metric.label} {_signed_rate(metric.rate)}({_signed_amount(metric.delta)})"
        for metric in change.metrics
    ]
    if change.margin_delta is not None:
        parts.append(f"영업이익률 {change.margin_delta:+.1f}%p")
    if change.missing_labels:
        parts.append(f"비교 불가: {', '.join(change.missing_labels)}")
    return f"재무 변화({change.previous_year}→{change.current_year}): " + " · ".join(parts)


def deterministic_comment(change: FinancialChange) -> str:
    if not change.metrics:
        return "비교 가능한 전년 재무 데이터가 없어 변화 분석을 보류했습니다."
    positive = sum(metric.delta > 0 for metric in change.metrics)
    negative = sum(metric.delta < 0 for metric in change.metrics)
    if change.margin_delta is not None and change.margin_delta > 0.05:
        return "매출과 이익 흐름을 함께 보면 수익성이 개선된 모습입니다."
    if change.margin_delta is not None and change.margin_delta < -0.05:
        return "매출 대비 이익 흐름이 약해져 수익성은 점검이 필요합니다."
    if positive and not negative:
        return "주요 실적 지표가 전반적으로 개선된 흐름입니다."
    if negative and not positive:
        return "주요 실적 지표가 전반적으로 약해진 흐름입니다."
    return "지표별 방향이 엇갈려 매출과 이익을 나눠서 확인할 필요가 있습니다."


def _safe_ai_comment(text: str) -> str:
    comment = " ".join((text or "").split()).strip("`\"'")
    if not comment or _DIGIT_RE.search(comment):
        return ""
    if any(word in comment for word in _FORBIDDEN_AI_WORDS):
        return ""
    if len(comment) > 100:
        comment = comment[:99].rstrip() + "…"
    return comment


def ai_change_comment(
    db: Session,
    *,
    user_id: int,
    change: FinancialChange,
) -> tuple[str, int]:
    """Ask for one qualitative sentence; all numeric claims stay server-owned."""
    fallback = deterministic_comment(change)
    if not change.metrics:
        return fallback, 0
    data = " / ".join(
        f"{metric.label}: 전년 {metric.previous:,}원 → 최근 {metric.current:,}원, "
        f"증감률 {_signed_rate(metric.rate)}"
        for metric in change.metrics
    )
    prompt = (
        "개미 투자자에게 재무 흐름을 설명하는 문장 하나만 작성하세요. "
        "한국어 존댓말, 80자 이내, 숫자·퍼센트·종목 추천·매수/매도 표현은 쓰지 마세요. "
        "매출과 이익의 방향, 수익성 변화를 해석하되 원인이나 미래 주가는 추측하지 마세요.\n"
        f"기간: {change.previous_year}년 대비 {change.current_year}년\n"
        f"XBRL 비교값: {data}\n"
    )
    started = perf_counter()
    try:
        text, _usage = chat_completion(
            db,
            user_id=user_id,
            purpose="digest_financial_change",
            messages=[
                {
                    "role": "system",
                    "content": "숫자를 만들지 말고 재무 변화의 의미만 한 문장으로 답하세요.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=120,
        )
        comment = _safe_ai_comment(text or "")
        return comment or fallback, int((perf_counter() - started) * 1000)
    except Exception:
        return fallback, int((perf_counter() - started) * 1000)
