from pathlib import Path

from app.services.dart_db import XbrlFact, init_dart_db, upsert_facts
from app.services.digest import _format_match_stock_lines
from app.services.financial_change import (
    ai_change_comment,
    deterministic_comment,
    format_change_summary,
    load_financial_change,
)


def test_load_financial_change_uses_same_annual_periods(tmp_path: Path):
    db = tmp_path / "dart.db"
    init_dart_db(db)
    upsert_facts(
        [
            XbrlFact("000660", 2023, 0, "revenue", "매출액", 10),
            XbrlFact("000660", 2024, 0, "revenue", "매출액", 15),
            XbrlFact("000660", 2023, 1, "operating_profit", "영업이익", 100),
            XbrlFact("000660", 2024, 0, "operating_profit", "영업이익", 30),
            XbrlFact("000660", 2023, 0, "net_income", "당기순이익", 2),
            XbrlFact("000660", 2024, 0, "net_income", "당기순이익", 4),
        ],
        path=db,
    )

    change = load_financial_change("000660", path=db)

    assert change is not None
    assert (change.previous_year, change.current_year) == (2023, 2024)
    assert [metric.account for metric in change.metrics] == ["revenue", "net_income"]
    assert change.missing_labels == ("영업이익",)


def test_format_change_summary_contains_grounded_deltas(tmp_path: Path):
    db = tmp_path / "dart.db"
    init_dart_db(db)
    upsert_facts(
        [
            XbrlFact("005930", 2023, 0, "revenue", "매출액", 100_000_000_000_000),
            XbrlFact("005930", 2024, 0, "revenue", "매출액", 120_000_000_000_000),
            XbrlFact("005930", 2023, 0, "operating_profit", "영업이익", 10_000_000_000_000),
            XbrlFact("005930", 2024, 0, "operating_profit", "영업이익", 18_000_000_000_000),
            XbrlFact("005930", 2023, 0, "net_income", "당기순이익", 5_000_000_000_000),
            XbrlFact("005930", 2024, 0, "net_income", "당기순이익", 4_000_000_000_000),
        ],
        path=db,
    )

    summary = format_change_summary(load_financial_change("005930", path=db))

    assert "재무 변화(2023→2024)" in summary
    assert "매출액 +20.0%(+20.0조)" in summary
    assert "영업이익 +80.0%(+8.0조)" in summary
    assert "당기순이익 -20.0%(-1.0조)" in summary
    assert "영업이익률" in summary


def test_zero_previous_value_does_not_emit_infinite_rate(tmp_path: Path):
    db = tmp_path / "dart.db"
    init_dart_db(db)
    upsert_facts(
        [
            XbrlFact("000660", 2023, 0, "revenue", "매출액", 0),
            XbrlFact("000660", 2024, 0, "revenue", "매출액", 10),
        ],
        path=db,
    )

    change = load_financial_change("000660", path=db)
    summary = format_change_summary(change)

    assert change is not None
    assert "증감률 산출 불가" in summary
    assert "inf" not in summary


def test_ai_comment_rejects_numbers_and_investment_advice(monkeypatch):
    class FakeDb:
        pass

    def fake_completion(*_args, **_kwargs):
        return "매출은 20% 증가했으니 매수 추천입니다.", None

    monkeypatch.setattr("app.services.financial_change.chat_completion", fake_completion)
    change = load_financial_change("005930")

    comment, _elapsed = ai_change_comment(FakeDb(), user_id=1, change=change)

    assert comment == deterministic_comment(change)
    assert not any(char.isdigit() for char in comment)
    assert "추천" not in comment


def test_stock_kakao_section_includes_compact_financial_analysis():
    lines = _format_match_stock_lines(
        {
            "kind": "종목",
            "title": "삼성전자 (005930)",
            "blurb": "오늘 수급 흐름에서 확인했습니다.",
            "financial_change": "재무 변화(2023→2024): 매출액 +16.2%(+41.9조)",
            "financial_analysis": "매출과 이익 흐름을 함께 보면 수익성이 개선된 모습입니다.",
            "url": "https://finance.naver.com/item/coinfo.naver?code=005930",
        }
    )

    body = "\n".join(lines)
    assert "재무 변화(2023→2024)" in body
    assert "도윤의 해석:" in body
    assert "재무제표:" in body
    assert len(body) < 1000
