from pathlib import Path

import httpx
import pytest

from app.config import get_settings
from app.schemas import FinanceQueryIn
from app.services.agent_enrichment import OLLAMA_OPTIONS
from app.services.dart_db import init_dart_db
from app.services.dart_query import answer_finance_query


@pytest.mark.asyncio
async def test_number_and_calc_use_xbrl_without_llm(tmp_path: Path, monkeypatch):
    db = tmp_path / "dart_financials.db"
    init_dart_db(db)
    monkeypatch.setenv("DART_DB_PATH", str(db))
    get_settings.cache_clear()

    async def boom(_prompt: str) -> str:
        raise AssertionError("LLM must not run for NUMBER/CALCULATION")

    monkeypatch.setattr("app.services.dart_query.infer_with_metrics", boom)
    try:
        number = await answer_finance_query(
            FinanceQueryIn(ticker="005930", question="2024 매출액은 얼마야?")
        )
        assert number.ok is True
        assert number.route == "NUMBER"
        assert "300,870,903,000,000" in number.answer
        assert number.facts and number.facts[0].value == 300_870_903_000_000

        calc = await answer_finance_query(
            FinanceQueryIn(ticker="삼성전자", question="영업이익 증가율")
        )
        assert calc.ok is True
        assert calc.route == "CALCULATION"
        assert "%" in calc.answer
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_analysis_uses_fts_xbrl_and_hardware_caps(tmp_path: Path, monkeypatch):
    db = tmp_path / "dart_financials.db"
    init_dart_db(db)
    monkeypatch.setenv("DART_DB_PATH", str(db))
    get_settings.cache_clear()
    captured: dict = {}

    class FakeResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"response": "HBM과 반도체 수요가 실적을 뒷받침했습니다."}

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc) -> None:
            return None

        async def post(self, url, json):
            captured["url"] = url
            captured["json"] = json
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    try:
        out = await answer_finance_query(
            FinanceQueryIn(ticker="005930", question="반도체 수요 때문에 왜 실적이 좋아졌나?")
        )
        assert out.ok is True
        assert out.route == "ANALYSIS"
        assert "반도체" in out.answer
        prompt = captured["json"]["prompt"]
        assert "Answer in Korean" in prompt
        assert "너는" not in prompt
        assert "매출액" in prompt or "revenue" in prompt.lower() or "300" in prompt
        assert "반도체 수요" in prompt
        assert "%PDF" not in prompt
        assert captured["json"]["options"] == OLLAMA_OPTIONS
        assert captured["json"]["options"]["num_ctx"] == 4096
        assert captured["json"]["options"]["num_predict"] == 512
        assert captured["json"]["options"]["num_thread"] == 2
        assert out.sources
    finally:
        get_settings.cache_clear()
