import httpx
import pytest
from httpx import AsyncClient

from app.auth import hash_password
from app.config import get_settings
from app.models import KakaoAccount, Preference, User
from app.services.agent_enrichment import (
    OLLAMA_OPTIONS,
    build_prompt,
    format_enrichment_footer,
    infer_enrichment,
    profession_for,
    ItemEnrichment,
)
from app.services.finance_db import replace_digest_actions
from app.routers.kakao_webhook import extract_utterance, parse_save_index


def test_profession_maps_desk_roles():
    user = User(email="a@b.c", display_name="u", occupation="의사")
    pref = Preference(topics="의학", roles="doctor")
    assert profession_for(user, pref) == "Doctor"
    pref.roles = "developer"
    assert profession_for(user, pref) == "Developer"
    pref.roles = "semiconductor"
    assert profession_for(user, pref) == "Semiconductor Engineer"
    pref.roles = "investor"
    assert profession_for(user, pref) == "Retail Investor"


def test_prompt_injects_profession_and_hardware_caps_are_fixed():
    prompt = build_prompt(
        "Retail Investor",
        [{"title": "삼성 실적", "blurb": "영업이익 증가"}],
        role_label="일반 개미 투자자",
    )
    assert "Retail Investor" in prompt
    assert "삼성 실적" in prompt
    assert "Korean" in prompt
    assert OLLAMA_OPTIONS == {"num_ctx": 4096, "num_predict": 512, "num_thread": 2}


@pytest.mark.asyncio
async def test_ollama_generate_sends_hardware_caps(monkeypatch):
    captured: dict = {}

    class FakeResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"response": '{"items":[]}'}

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
    text = await infer_enrichment("ping")
    assert text == '{"items":[]}'
    assert captured["json"]["options"] == OLLAMA_OPTIONS
    assert captured["json"]["stream"] is False
    assert captured["url"].endswith("/api/generate")


def test_footer_and_save_regex():
    footer = format_enrichment_footer(
        [
            (
                {"title": "삼성 실적"},
                ItemEnrichment(core_insight="실적", action_item="공시 확인", relevant_ticker="005930"),
                "삼성전자 매출",
            )
        ]
    )
    assert "1." in footer
    assert "1번 저장" in footer
    assert parse_save_index("내일 1번 저장 해줘") == 1
    assert parse_save_index("저장") is None


def test_extract_kakao_skill_utterance():
    text, kid = extract_utterance(
        {"userRequest": {"utterance": "2번 저장", "user": {"id": "kakao-9"}}}
    )
    assert text == "2번 저장"
    assert kid == "kakao-9"


@pytest.mark.asyncio
async def test_enrichment_fallback_on_ollama_timeout(monkeypatch):
    from app.services import agent_enrichment as ae

    monkeypatch.setenv("AGENT_ENRICHMENT_ENABLED", "true")
    get_settings.cache_clear()

    async def boom(_prompt: str):
        raise TimeoutError("ollama down")

    monkeypatch.setattr(ae, "infer_with_metrics", boom)
    user = User(id=1, email="a@b.c", display_name="u")
    pref = Preference(topics="경제", roles="investor")
    items = [{"kind": "아티클", "title": "뉴스", "blurb": "요약"}]
    body = "원문 바디"
    try:
        out_items, out_body = ae.enrich_digest_payload(user, pref, items, body)
        assert out_body == body
        assert out_items == items
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_kakao_webhook_queues_notion(client: AsyncClient, db_session, monkeypatch, tmp_path):
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
    finance = tmp_path / "finance.db"
    monkeypatch.setenv("FINANCE_DB_PATH", str(finance))
    monkeypatch.setenv("KAKAO_WEBHOOK_SECRET", "")
    get_settings.cache_clear()
    replace_digest_actions(admin.id, [(1, "공시 확인", "005930", "실적")], path=finance)
    called: list[dict] = []

    def fake_save(**kwargs):
        called.append(kwargs)
        return True

    monkeypatch.setattr("app.routers.kakao_webhook.save_action_to_notion", fake_save)
    try:
        res = await client.post(
            "/webhook/kakao",
            json={"userRequest": {"utterance": "1번 저장", "user": {"id": "kakao-admin"}}},
        )
        assert res.status_code == 200
        assert "저장" in res.text
        assert called and called[0]["action_item"] == "공시 확인"
    finally:
        get_settings.cache_clear()
