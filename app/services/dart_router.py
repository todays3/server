"""Keyword/regex query router. NUMBER and CALCULATION never call an LLM."""

from __future__ import annotations

import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

_YEAR = re.compile(r"(20\d{2})")

_ANALYSIS = ("왜", "이유", "배경", "분석", "영향", "전망", "원인", "어떻게", "설명")
_CALC_YOY = ("증가율", "증감", "yoy", "전년", "성장률", "변화율", "대비")
_CALC_MARGIN = ("이익률", "마진", "영업이익률")
_NUMBER = ("얼마", "몇", "수치", "금액", "매출", "영업이익", "순이익", "공시")


class QueryRoute(str, Enum):
    NUMBER = "NUMBER"
    CALCULATION = "CALCULATION"
    ANALYSIS = "ANALYSIS"


class RoutedQuery(BaseModel):
    route: QueryRoute
    account: str = "revenue"
    year: int | None = None
    calc: Literal["yoy", "margin"] = "yoy"
    question: str = Field(default="")


def route_finance_question(question: str) -> RoutedQuery:
    text = (question or "").strip()
    lowered = text.lower()
    year = _year(text)
    account = _account(text)
    if any(token in text for token in _ANALYSIS):
        return RoutedQuery(route=QueryRoute.ANALYSIS, account=account, year=year, question=text)
    if any(token in text or token in lowered for token in _CALC_MARGIN):
        return RoutedQuery(
            route=QueryRoute.CALCULATION,
            account="operating_profit",
            year=year,
            calc="margin",
            question=text,
        )
    if any(token in text or token in lowered for token in _CALC_YOY):
        return RoutedQuery(
            route=QueryRoute.CALCULATION,
            account=account,
            year=year,
            calc="yoy",
            question=text,
        )
    if any(token in text for token in _NUMBER):
        return RoutedQuery(route=QueryRoute.NUMBER, account=account, year=year, question=text)
    return RoutedQuery(route=QueryRoute.ANALYSIS, account=account, year=year, question=text)


def _year(text: str) -> int | None:
    match = _YEAR.search(text or "")
    if match is None:
        return None
    return int(match.group(1))


def _account(text: str) -> str:
    if "영업이익" in text:
        return "operating_profit"
    if "당기순이익" in text or ("순이익" in text and "영업" not in text):
        return "net_income"
    if "매출" in text:
        return "revenue"
    return "revenue"
