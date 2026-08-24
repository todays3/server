import pytest
from httpx import AsyncClient

from app.auth import hash_password
from app.config import get_settings
from app.models import KakaoAccount, User
from app.routers.kakao_webhook import parse_finance_command
from app.schemas import FinanceQueryOut


def test_parse_finance_command():
    assert parse_finance_command("!재무분석 005930 2024 매출액은?") == ("005930", "2024 매출액은?")
    assert parse_finance_command("안녕") is None
    assert parse_finance_command("1번 저장") is None


@pytest.mark.asyncio
async def test_kakao_finance_command_answers_number(client: AsyncClient, db_session, monkeypatch):
    admin = User(
        email="admin@example.com",
        display_name="관리자",
        password_hash=hash_password("admin12345"),
        status="approved",
        is_admin=True,
    )
    db_session.add(admin)
    db_session.flush()
    db_session.add(KakaoAccount(user_id=admin.id, kakao_id="kakao-admin", access_token="t"))
    db_session.commit()
    monkeypatch.setenv("KAKAO_WEBHOOK_SECRET", "")
    get_settings.cache_clear()

    async def fake_answer(payload):
        return FinanceQueryOut(ok=True, route="NUMBER", answer="매출액 테스트", facts=[], sources=[])

    monkeypatch.setattr("app.routers.kakao_webhook.answer_finance_query", fake_answer)
    try:
        res = await client.post(
            "/webhook/kakao",
            json={"userRequest": {"utterance": "!재무분석 005930 매출액은?", "user": {"id": "kakao-admin"}}},
        )
        assert res.status_code == 200
        assert "매출액 테스트" in res.text
    finally:
        get_settings.cache_clear()
