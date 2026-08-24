from app.services.stock_match import (
    default_match_stock,
    financials_url,
    heuristic_match_stock,
    normalize_match_stock,
    parse_match_stock,
)


def test_financials_url_uses_naver_for_kr_ticker():
    url = financials_url(name="삼성전자", ticker="005930", market="KR")
    assert "005930" in url
    assert "naver.com" in url or "dart.fss.or.kr" in url


def test_financials_url_uses_yahoo_for_us_ticker():
    url = financials_url(name="NVIDIA", ticker="NVDA", market="US")
    assert "NVDA" in url
    assert "yahoo.com" in url or "sec.gov" in url


def test_heuristic_picks_stock_mentioned_in_articles():
    items = [
        {"title": "HBM 수요, SK하이닉스 수주 확대", "blurb": "메모리 사이클", "source": "한경"},
        {"title": "AI 서버 투자 지속", "blurb": "데이터센터", "source": "매경"},
        {"title": "코스피 반도체 강세", "blurb": "대형주", "source": "네이버"},
    ]
    picked = heuristic_match_stock(items, market="국내증시")
    assert picked is not None
    assert picked["ticker"] == "000660"
    assert picked["name"] == "SK하이닉스"
    assert "추천" in picked["blurb"]
    assert picked["url"]


def test_heuristic_prefers_foreign_flow_over_ambient_samsung():
    items = [
        {
            "title": "코스피 보합, 삼성전자도 혼조",
            "blurb": "대형주 전반",
            "source": "시황",
        },
        {
            "title": "에코프로비엠, 외국인 순매수 1위",
            "blurb": "2차전지 수급",
            "source": "네이버 외국인 순매수",
            "site_id": "naver-finance",
        },
        {
            "title": "거래대금 상위 에코프로비엠",
            "blurb": "247540",
            "source": "네이버 거래대금 상위",
        },
    ]
    picked = heuristic_match_stock(items, market="국내증시")
    assert picked["ticker"] == "247540"
    assert picked["name"] == "에코프로비엠"
    assert "외국인" in picked["blurb"] or "거래대금" in picked["blurb"] or "수급" in picked["blurb"]


def test_heuristic_reads_ticker_code_from_ranking_list():
    items = [
        {
            "title": "두산에너빌리티 (034020)",
            "summary": "네이버 거래량 상위 · 종목코드 034020",
            "source": "네이버 거래량 상위",
            "site_id": "naver-finance",
            "url": "https://finance.naver.com/item/main.naver?code=034020",
        }
    ]
    picked = heuristic_match_stock(items, market="국내증시")
    assert picked["ticker"] == "034020"
    assert "두산" in picked["name"]


def test_parse_match_stock_fills_financials_url():
    picked = parse_match_stock({"name": "삼성전자", "ticker": "005930", "market": "KR", "why": "실적"})
    assert picked is not None
    assert picked["ticker"] == "005930"
    assert "005930" in picked["url"]
    assert "추천이 아닙니다" in picked["blurb"] or "추천 아님" in picked["blurb"]


def test_heuristic_falls_back_to_default_when_no_mentions():
    picked = heuristic_match_stock([{"title": "날씨", "blurb": "맑음"}], market="국내증시")
    assert picked["ticker"]
    assert picked["ticker"] != "005930"
    assert "추천이 아닙니다" in picked["blurb"]


def test_default_match_stock_skips_samsung_bias():
    picked = default_match_stock(market="국내증시")
    assert picked["ticker"] != "005930"


def test_normalize_rejects_empty_ticker():
    assert parse_match_stock({"name": "모름", "ticker": "", "market": "KR"}) is None
    assert normalize_match_stock("??", "KR") is None
