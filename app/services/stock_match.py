"""Pick one listed stock from today's market signals — not a buy call."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

MATCH_STOCK_KIND = "종목"
DISCLAIMER = "매수 추천이 아닙니다."
BLURB_PREFIX = "오늘 시장 흐름에 맞춰 짚은 종목입니다."

# Strong tape / flow / event cues from finance portals and headlines.
_SIGNAL_WEIGHTS: tuple[tuple[str, int], ...] = (
    ("외국인 순매수", 18),
    ("외국인 순매도", 16),
    ("기관 순매수", 14),
    ("기관 순매도", 12),
    ("외국인", 8),
    ("기관", 6),
    ("순매수", 10),
    ("순매도", 9),
    ("거래대금", 16),
    ("거래량 상위", 14),
    ("거래량", 8),
    ("시간외", 16),
    ("장후", 12),
    ("장전", 8),
    ("급등", 12),
    ("급락", 10),
    ("상한가", 18),
    ("하한가", 14),
    ("특징주", 12),
    ("공시", 12),
    ("수주", 11),
    ("실적", 9),
    ("목표가", 10),
    ("투자의견", 9),
    ("신고가", 11),
    ("신저가", 9),
    ("테마", 6),
    ("이슈", 5),
)

# Mega caps need a clear signal; ambient "코스피/반도체" mentions alone are not enough.
_AMBIENT_MEGA_TICKERS = frozenset({"005930"})
_TICKER_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")


@dataclass(frozen=True)
class KnownStock:
    name: str
    ticker: str
    market: str  # KR | US
    aliases: tuple[str, ...]


KNOWN_STOCKS: tuple[KnownStock, ...] = (
    KnownStock("삼성전자", "005930", "KR", ("삼성전자", "samsung electronics")),
    KnownStock("SK하이닉스", "000660", "KR", ("sk하이닉스", "하이닉스", "hynix")),
    KnownStock("삼성바이오로직스", "207940", "KR", ("삼성바이오", "바이오로직스")),
    KnownStock("LG에너지솔루션", "373220", "KR", ("lg엔솔", "lg에너지솔루션", "lg에너지", "에너지솔루션")),
    KnownStock("현대차", "005380", "KR", ("현대차", "현대자동차")),
    KnownStock("기아", "000270", "KR", ("기아차", "기아 ")),
    KnownStock("네이버", "035420", "KR", ("네이버", "naver")),
    KnownStock("카카오", "035720", "KR", ("카카오", "kakao")),
    KnownStock("KB금융", "105560", "KR", ("kb금융", "kb 금융")),
    KnownStock("신한지주", "055550", "KR", ("신한지주",)),
    KnownStock("한국전력", "015760", "KR", ("한국전력", "한전")),
    KnownStock("두산에너빌리티", "034020", "KR", ("두산에너빌리티", "두산에너")),
    KnownStock("한화에어로스페이스", "012450", "KR", ("한화에어로", "한화에어로스페이스")),
    KnownStock("LIG넥스원", "079550", "KR", ("lig넥스원",)),
    KnownStock("엔씨소프트", "036570", "KR", ("엔씨소프트",)),
    KnownStock("크래프톤", "259960", "KR", ("크래프톤",)),
    KnownStock("포스코홀딩스", "005490", "KR", ("포스코홀딩스", "포스코")),
    KnownStock("LG전자", "066570", "KR", ("lg전자",)),
    KnownStock("현대모비스", "012330", "KR", ("현대모비스",)),
    KnownStock("에코프로비엠", "247540", "KR", ("에코프로비엠",)),
    KnownStock("에코프로", "086520", "KR", ("에코프로",)),
    KnownStock("HLB", "028300", "KR", ("hlb",)),
    KnownStock("알테오젠", "196170", "KR", ("알테오젠",)),
    KnownStock("에스엠", "041510", "KR", ("에스엠", "sm엔터")),
    KnownStock("셀트리온", "068270", "KR", ("셀트리온",)),
    KnownStock("삼성SDI", "006400", "KR", ("삼성sdi", "삼성 sdi")),
    KnownStock("카카오뱅크", "323410", "KR", ("카카오뱅크",)),
    KnownStock("카카오페이", "377300", "KR", ("카카오페이",)),
    KnownStock("HMM", "011200", "KR", ("hmm",)),
    KnownStock("대한항공", "003490", "KR", ("대한항공",)),
    KnownStock("한진칼", "180640", "KR", ("한진칼",)),
    KnownStock("아모레퍼시픽", "090430", "KR", ("아모레퍼시픽",)),
    KnownStock("LG화학", "051910", "KR", ("lg화학",)),
    KnownStock("삼성물산", "028260", "KR", ("삼성물산",)),
    KnownStock("NVIDIA", "NVDA", "US", ("nvidia", "엔비디아", "nvda")),
    KnownStock("Apple", "AAPL", "US", ("apple", "애플", "aapl")),
    KnownStock("Microsoft", "MSFT", "US", ("microsoft", "마이크로소프트", "msft")),
    KnownStock("Tesla", "TSLA", "US", ("tesla", "테슬라", "tsla")),
    KnownStock("Amazon", "AMZN", "US", ("amazon", "아마존", "amzn")),
    KnownStock("Alphabet", "GOOGL", "US", ("alphabet", "구글", "google", "googl")),
    KnownStock("Meta", "META", "US", ("meta", "메타", "페이스북")),
    KnownStock("TSMC", "TSM", "US", ("tsmc", "대만반도체", "tsm")),
    KnownStock("Broadcom", "AVGO", "US", ("broadcom", "브로드컴", "avgo")),
)

_BY_TICKER = {row.ticker.upper(): row for row in KNOWN_STOCKS}


def financials_url(*, name: str, ticker: str, market: str) -> str:
    _ = name
    code = (ticker or "").strip().upper()
    if not code:
        return ""
    if market == "US" or not code.isdigit():
        return f"https://finance.yahoo.com/quote/{quote(code)}/financials"
    return f"https://finance.naver.com/item/coinfo.naver?code={code.zfill(6)}"


def normalize_match_stock(ticker: str, market: str | None = None) -> tuple[str, str] | None:
    raw = (ticker or "").strip().upper().lstrip("$")
    hint = (market or "").strip()
    if raw.isdigit() and 5 <= len(raw) <= 6:
        return raw.zfill(6), "KR"
    if raw.isalpha() and 1 <= len(raw) <= 5:
        return raw, "US"
    if hint in {"국내증시", "KR", "KRX"} and raw.isdigit():
        return raw.zfill(6), "KR"
    return None


def _with_blurb(name: str, ticker: str, market: str, why: str) -> dict[str, str]:
    reason = " ".join((why or "").split())[:120]
    blurb = f"{BLURB_PREFIX} {DISCLAIMER}"
    if reason and DISCLAIMER not in reason and "추천" not in reason:
        blurb = f"{BLURB_PREFIX} {reason} {DISCLAIMER}"
    elif reason:
        blurb = f"{BLURB_PREFIX} {reason}"
        if DISCLAIMER not in blurb:
            blurb = f"{blurb} {DISCLAIMER}"
    url = financials_url(name=name, ticker=ticker, market=market)
    return {
        "kind": MATCH_STOCK_KIND,
        "title": f"{name} ({ticker})",
        "name": name,
        "blurb": blurb[:220],
        "url": url,
        "hint": "match_stock",
        "ticker": ticker,
        "why": "오늘 시장 흐름 · 추천 아님",
    }


def parse_match_stock(raw: object) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()[:40]
    ticker_raw = str(raw.get("ticker") or "").strip()
    market_raw = str(raw.get("market") or "").strip()
    why = str(raw.get("why") or "").strip()
    normalized = normalize_match_stock(ticker_raw, market_raw)
    if not name or normalized is None:
        return None
    ticker, market = normalized
    known = _BY_TICKER.get(ticker.upper())
    if known and (not name or name == ticker):
        name = known.name
    return _with_blurb(name, ticker, market, why)


def _item_text(item: dict[str, str]) -> str:
    return " ".join(
        [
            str(item.get("title") or ""),
            str(item.get("blurb") or ""),
            str(item.get("summary") or ""),
            str(item.get("source") or ""),
            str(item.get("why") or ""),
            str(item.get("url") or ""),
        ]
    )


def _item_name_text(item: dict[str, str]) -> str:
    """Name/ticker haystack — exclude source labels like '네이버 증권' that fake-match tickers."""
    return " ".join(
        [
            str(item.get("title") or ""),
            str(item.get("blurb") or ""),
            str(item.get("summary") or ""),
            str(item.get("url") or ""),
        ]
    )


def _signal_score(blob: str) -> tuple[int, list[str]]:
    hits: list[str] = []
    total = 0
    lower = blob.lower()
    for keyword, weight in _SIGNAL_WEIGHTS:
        if keyword.lower() in lower:
            total += weight
            hits.append(keyword)
    return total, hits


def _finance_portal_bonus(item: dict[str, str]) -> int:
    source = f"{item.get('source') or ''} {item.get('site_id') or ''}".lower()
    bonus = 0
    for token in (
        "네이버",
        "naver",
        "한경",
        "hankyung",
        "매경",
        "mk",
        "토스",
        "toss",
        "yahoo",
        "investing",
        "거래량",
        "외국인",
        "시간외",
        "dart",
        "공시",
    ):
        if token in source:
            bonus += 4
    return min(bonus, 12)


def _resolve_stock(ticker: str, name_hint: str = "") -> KnownStock | None:
    code = (ticker or "").strip().upper()
    if code.isdigit():
        code = code.zfill(6)
    known = _BY_TICKER.get(code)
    if known:
        return known
    try:
        from app.services.finance_db import lookup_ticker

        row = lookup_ticker(code) or (lookup_ticker(name_hint) if name_hint else None)
    except Exception:  # noqa: BLE001
        row = None
    if row is None:
        if name_hint and code:
            market = "KR" if code.isdigit() else "US"
            return KnownStock(name_hint[:40], code, market, (name_hint.lower(),))
        return None
    market = "KR" if str(row.market).upper().startswith("KOS") or code.isdigit() else "US"
    return KnownStock(row.name, row.ticker, market, (row.name.lower(),))


def _stocks_mentioned(blob: str, *, prefer_us: bool) -> list[tuple[KnownStock, int]]:
    lower = blob.lower()
    found: dict[str, tuple[KnownStock, int]] = {}
    # Longer aliases first so "에코프로비엠" wins over "에코프로".
    ordered = sorted(
        KNOWN_STOCKS,
        key=lambda stock: -max((len(alias) for alias in stock.aliases), default=0),
    )
    claimed_spans: list[str] = []
    for stock in ordered:
        if prefer_us and stock.market != "US":
            continue
        if not prefer_us and stock.market != "KR":
            continue
        hits = 0
        for alias in sorted(stock.aliases, key=len, reverse=True):
            needle = alias.lower().strip()
            if len(needle) < 2 or needle not in lower:
                continue
            if any(needle != span and needle in span for span in claimed_spans):
                continue
            hits += 1
            claimed_spans.append(needle)
            break
        if hits:
            found[stock.ticker] = (stock, hits)
    for code in _TICKER_RE.findall(blob):
        stock = _resolve_stock(code)
        if stock is None:
            continue
        if prefer_us and stock.market != "US":
            continue
        if not prefer_us and stock.market != "KR":
            continue
        prev = found.get(stock.ticker)
        hits = (prev[1] if prev else 0) + 2
        found[stock.ticker] = (stock, hits)
    return list(found.values())


def _why_from_signals(signals: list[str], stock_name: str) -> str:
    if not signals:
        return f"오늘 기사·수급 흐름에서 {stock_name}이(가) 가장 분명하게 드러났습니다."
    top = "·".join(signals[:3])
    return f"오늘 후보에서 {top} 신호가 {stock_name}에 가장 뚜렷합니다."


def heuristic_match_stock(items: list[dict[str, str]], *, market: str = "국내증시") -> dict[str, str]:
    """Score listed names by tape signals near them — avoid ambient mega-cap defaults."""
    prefer_us = market in {"미국증시", "US"}
    scores: dict[str, tuple[int, KnownStock, list[str]]] = {}

    for item in items:
        blob = _item_text(item)
        names = _item_name_text(item)
        if not names.strip() and not blob.strip():
            continue
        signal_pts, signals = _signal_score(blob)
        portal = _finance_portal_bonus(item)
        for stock, alias_hits in _stocks_mentioned(names, prefer_us=prefer_us):
            score = alias_hits * 8 + signal_pts + portal
            if stock.ticker in _AMBIENT_MEGA_TICKERS and signal_pts < 8:
                score -= 20
            # Ranking-list style titles (code in URL path / short name) with volume cues.
            if signal_pts >= 8:
                score += 6
            prev = scores.get(stock.ticker)
            if prev is None or score > prev[0]:
                merged = list(dict.fromkeys((prev[2] if prev else []) + signals))
                scores[stock.ticker] = (score, stock, merged)

    ranked = sorted(scores.values(), key=lambda row: (-row[0], row[1].name))
    # Require either a real signal or a clear multi-hit mention — else rotate default.
    for score, stock, signals in ranked:
        if score >= 12 or signals:
            why = _why_from_signals(signals, stock.name)
            return _with_blurb(stock.name, stock.ticker, stock.market, why)
    if ranked and ranked[0][0] >= 8:
        score, stock, signals = ranked[0]
        return _with_blurb(stock.name, stock.ticker, stock.market, _why_from_signals(signals, stock.name))
    return default_match_stock(market=market)


def default_match_stock(*, market: str = "국내증시") -> dict[str, str]:
    """Day-rotated fallback — never hard-code Samsung as the only answer."""
    prefer_us = market in {"미국증시", "US"}
    pool = [row for row in KNOWN_STOCKS if (row.market == "US") == prefer_us]
    # Skip ambient mega as the silent default.
    pool = [row for row in pool if row.ticker not in _AMBIENT_MEGA_TICKERS] or pool
    if not pool:
        pool = list(KNOWN_STOCKS)
    idx = date.today().toordinal() % len(pool)
    stock = pool[idx]
    return _with_blurb(
        stock.name,
        stock.ticker,
        stock.market,
        "오늘 뚜렷한 개별 수급·이슈 신호가 약해 시장 대표 흐름 종목으로 짚었습니다.",
    )
