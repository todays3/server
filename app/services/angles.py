"""Three-slot mix for every assistant brief: trend, specific issue, famous person."""

from __future__ import annotations

import re
from typing import Any

ANGLE_FLOW = "흐름"
ANGLE_ISSUE = "이슈"
ANGLE_PERSON = "인물"
ANGLE_ORDER = (ANGLE_FLOW, ANGLE_ISSUE, ANGLE_PERSON)

_PERSON = re.compile(
    r"인터뷰|연설|회견|ceo|cfo|대표이사|\b회장\b|총리|대통령|\b배우\b|\b작가\b|\b감독\b|"
    r"유명인|셀럽|interview|founder|이재용|정의선|손정의|일론|머스크|베이조스|버핏",
    re.I,
)
_ISSUE = re.compile(
    r"출시|출간|발간|신간|신작|신제품|업데이트|패치|버전|공시|실적|취약점|리콜|"
    r"cve|launch|release|\bv\d|구체|신곡|발매",
    re.I,
)
_FLOW = re.compile(
    r"전망|동향|시황|트렌드|이번\s*주|한\s*주|흐름|브리핑|주간|시장\s*전반|"
    r"outlook|weekly|trend",
    re.I,
)


def classify_angle(*parts: str) -> str:
    blob = " ".join(part for part in parts if part)
    if not blob:
        return ANGLE_FLOW
    if _PERSON.search(blob):
        return ANGLE_PERSON
    if _ISSUE.search(blob):
        return ANGLE_ISSUE
    if _FLOW.search(blob):
        return ANGLE_FLOW
    return ANGLE_FLOW


def normalize_angle(raw: str | None) -> str | None:
    text = (raw or "").strip()
    if text in ANGLE_ORDER:
        return text
    aliases = {
        "flow": ANGLE_FLOW,
        "trend": ANGLE_FLOW,
        "overall": ANGLE_FLOW,
        "issue": ANGLE_ISSUE,
        "hot": ANGLE_ISSUE,
        "person": ANGLE_PERSON,
        "people": ANGLE_PERSON,
        "인물": ANGLE_PERSON,
        "이슈": ANGLE_ISSUE,
        "흐름": ANGLE_FLOW,
    }
    return aliases.get(text.lower())


def order_items_by_angle(items: list[dict[str, str]]) -> list[dict[str, str]]:
    remaining = [dict(row) for row in items]
    ordered: list[dict[str, str]] = []
    for ang in ANGLE_ORDER:
        idx = next((i for i, row in enumerate(remaining) if normalize_angle(row.get("angle")) == ang), None)
        if idx is None:
            idx = 0 if remaining else None
        if idx is None:
            break
        row = remaining.pop(idx)
        row["angle"] = ang
        ordered.append(row)
    return ordered


def pick_three_by_angle(candidates: list[Any]) -> list[tuple[str, Any]]:
    """One item per angle. Empty buckets fill from leftovers, preferring unused kinds."""
    if not candidates:
        return []
    buckets: dict[str, list[Any]] = {ang: [] for ang in ANGLE_ORDER}
    for item in candidates:
        ang = classify_angle(
            getattr(item, "title", "") or "",
            getattr(item, "summary", "") or "",
            getattr(item, "source", "") or "",
        )
        buckets[ang].append(item)
    picked: list[tuple[str, Any]] = []
    used: set[int] = set()
    used_kinds: set[str] = set()

    def take(item: Any, ang: str) -> None:
        picked.append((ang, item))
        used.add(id(item))
        kind = getattr(item, "kind", "") or ""
        if kind:
            used_kinds.add(kind)

    def first_unused(pool: list[Any], *, prefer_new_kind: bool) -> Any | None:
        if prefer_new_kind:
            for item in pool:
                if id(item) in used:
                    continue
                kind = getattr(item, "kind", "") or ""
                if kind and kind not in used_kinds:
                    return item
        for item in pool:
            if id(item) not in used:
                return item
        return None

    for ang in ANGLE_ORDER:
        chosen = first_unused(buckets[ang], prefer_new_kind=True)
        if chosen is None:
            chosen = first_unused(list(candidates), prefer_new_kind=True)
        if chosen is None:
            chosen = first_unused(list(candidates), prefer_new_kind=False)
        if chosen is not None:
            take(chosen, ang)
        if len(picked) >= 3:
            break
    return picked
