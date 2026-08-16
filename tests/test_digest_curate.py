"""Digest heuristic pick, create_digest, and LLM curation contracts."""

from datetime import date

import json

from sqlalchemy.orm import Session

from app.auth import hash_password
from app.config import get_settings
from app.models import Preference, User
from app.services.digest import (
    _heuristic_pick,
    _llm_curate,
    _static_fallback,
    age_band,
    build_digest_preview,
    create_digest,
    occupation_tokens,
    personalize_candidates,
    profile_brief,
)
from app.services.sources import SourceItem


def _approved_user(db: Session, email: str = "d@example.com") -> User:
    user = User(
        email=email,
        display_name="",
        password_hash=hash_password("abcdefgh"),
        status="approved",
    )
    db.add(user)
    db.flush()
    return user


def _cands() -> list[SourceItem]:
    return [
        SourceItem(kind="아티클", title="금리 연준", url="https://a.example", summary="아티클", source="아티클"),
        SourceItem(kind="아티클", title="A2", url="https://a2.example", summary="s", source="s"),
        SourceItem(kind="유튜브", title="B", url="https://b.example", summary="s", source="s"),
        SourceItem(kind="커뮤니티", title="C", url="https://c.example", summary="s", source="s"),
    ]


def test_heuristic_pick_returns_three_or_empty():
    picked = _heuristic_pick(_cands(), "seed")
    assert len(picked) == 3
    assert _heuristic_pick([], "s") == []


def test_static_fallback_returns_three_items():
    items = _static_fallback(["경제"], "seed")
    assert len(items) == 3
    assert all("입니다." in row["blurb"] or row["blurb"].endswith("습니다.") for row in items)


def test_create_digest_without_llm_forces_seoul_timezone(db_session, monkeypatch):
    user = _approved_user(db_session)
    pref = Preference(
        user_id=user.id,
        topics="경제",
        timezone="UTC",
        notes="짧게",
        insight_questions=True,
        sources="hn",
    )
    db_session.add(pref)
    db_session.commit()
    monkeypatch.setattr("app.services.digest.gather_candidates", lambda *a, **k: _cands())
    monkeypatch.setenv("LLM_API_KEY", "")
    get_settings.cache_clear()
    try:
        digest = create_digest(db_session, user, pref, status="draft")
        assert digest.id
        assert pref.timezone == "Asia/Seoul"
        assert "왜:" in digest.body or "왜 " in digest.body
    finally:
        get_settings.cache_clear()


def test_llm_curate_no_candidates(db_session, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    get_settings.cache_clear()
    try:
        user = _approved_user(db_session, "c0@example.com")
        pref = Preference(user_id=user.id, topics="경제", notes="")
        db_session.add(pref)
        db_session.flush()
        none, reason, _ = _llm_curate(db_session, user, pref, [])
        assert none is None
        assert reason == "no_candidates"
    finally:
        get_settings.cache_clear()


def test_llm_curate_empty_response(db_session, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    get_settings.cache_clear()
    monkeypatch.setattr("app.services.digest.llm_service.chat_completion", lambda *_a, **_k: (None, None))
    try:
        user = _approved_user(db_session, "c1@example.com")
        pref = Preference(user_id=user.id, topics="경제", insight_questions=True)
        db_session.add(pref)
        db_session.flush()
        none, reason, _ = _llm_curate(db_session, user, pref, _cands())
        assert reason == "empty_response"
    finally:
        get_settings.cache_clear()


def test_llm_curate_fewer_than_three_items(db_session, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.services.digest.llm_service.chat_completion",
        lambda *_a, **_k: (
            '{"title":"","items":[{"kind":"아티클","title":"t","blurb":"b","url":"https://nope.example"}]}',
            None,
        ),
    )
    try:
        user = _approved_user(db_session, "c2@example.com")
        pref = Preference(user_id=user.id, topics="경제", insight_questions=True)
        db_session.add(pref)
        db_session.flush()
        none, reason, _ = _llm_curate(db_session, user, pref, _cands())
        assert reason == "fewer_than_3_items"
    finally:
        get_settings.cache_clear()


def test_llm_curate_accepts_fenced_json_and_url_prefix(db_session, monkeypatch):
    payload = {
        "title": "",
        "items": [
            {
                "kind": "아티클",
                "title": "t1",
                "blurb": "b",
                "url": "https://a.example/extra",
                "insight_q": "q",
                "insight_url": "https://b.example",
            },
            {"kind": "유튜브", "title": "t2", "blurb": "b", "url": "https://b.example"},
            {"kind": "커뮤니티", "title": "t3", "blurb": "b", "url": "https://c.example"},
        ],
    }
    monkeypatch.setenv("LLM_API_KEY", "k")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.services.digest.llm_service.chat_completion",
        lambda *_a, **_k: (f"```json\n{json.dumps(payload)}\n```", None),
    )
    try:
        user = _approved_user(db_session, "c3@example.com")
        pref = Preference(user_id=user.id, topics="경제", insight_questions=True)
        db_session.add(pref)
        db_session.flush()
        result, reason, _raw = _llm_curate(db_session, user, pref, _cands())
        assert result is not None
        assert reason == ""
    finally:
        get_settings.cache_clear()


def test_llm_curate_keeps_site_why_not_invented_stats(db_session, monkeypatch):
    cands = [
        SourceItem(
            kind="아티클",
            title="금리 연준",
            url="https://a.example",
            summary="아티클",
            source="HN",
            site_id="hn",
            pick_reason="HN 프론트페이지",
        ),
        SourceItem(
            kind="유튜브",
            title="B",
            url="https://b.example",
            summary="s",
            source="s",
            pick_reason="조회수 1만+ 영상",
        ),
        SourceItem(
            kind="커뮤니티",
            title="C",
            url="https://c.example",
            summary="s",
            source="s",
            pick_reason="커뮤니티 인기글",
        ),
    ]
    payload = {
        "title": "하루만장",
        "items": [
            {"kind": "아티클", "title": "t1", "blurb": "b입니다.", "url": "https://a.example", "why": "좋아요 2만"},
            {"kind": "유튜브", "title": "t2", "blurb": "b입니다.", "url": "https://b.example"},
            {"kind": "커뮤니티", "title": "t3", "blurb": "b입니다.", "url": "https://c.example"},
        ],
    }
    monkeypatch.setenv("LLM_API_KEY", "k")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.services.digest.llm_service.chat_completion",
        lambda *_a, **_k: (json.dumps(payload), None),
    )
    try:
        user = _approved_user(db_session, "c-why@example.com")
        pref = Preference(user_id=user.id, topics="경제", notes="")
        db_session.add(pref)
        db_session.flush()
        result, reason, _raw = _llm_curate(db_session, user, pref, cands)
        assert reason == ""
        assert result is not None
        _title, body, items = result
        assert items[0]["why"] == "HN 프론트페이지"
        assert "왜 HN 프론트페이지" in body
        assert "좋아요 2만" not in body
    finally:
        get_settings.cache_clear()


def test_llm_curate_invalid_json(db_session, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.services.digest.llm_service.chat_completion",
        lambda *_a, **_k: ("not-json", None),
    )
    try:
        user = _approved_user(db_session, "c4@example.com")
        pref = Preference(user_id=user.id, topics="경제", insight_questions=True)
        db_session.add(pref)
        db_session.flush()
        none, reason, _ = _llm_curate(db_session, user, pref, _cands())
        assert reason == "invalid_json"
    finally:
        get_settings.cache_clear()


def test_llm_curate_rejects_unknown_urls(db_session, monkeypatch):
    bad_urls = {
        "title": "x",
        "items": [
            {"kind": "아티클", "title": "t", "blurb": "b", "url": "https://zzz.example"},
            {"kind": "아티클", "title": "t", "blurb": "b", "url": "https://yyy.example"},
            {"kind": "아티클", "title": "t", "blurb": "b", "url": "https://xxx.example"},
        ],
    }
    monkeypatch.setenv("LLM_API_KEY", "k")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.services.digest.llm_service.chat_completion",
        lambda *_a, **_k: (json.dumps(bad_urls), None),
    )
    try:
        user = _approved_user(db_session, "c5@example.com")
        pref = Preference(user_id=user.id, topics="경제", insight_questions=True)
        db_session.add(pref)
        db_session.flush()
        none, reason, _ = _llm_curate(db_session, user, pref, _cands())
        assert reason == "fewer_than_3_valid_urls"
    finally:
        get_settings.cache_clear()


def test_llm_curate_prompt_asks_for_seumnida_style(db_session, monkeypatch):
    captured: dict[str, object] = {}

    def capture(*_a, **kwargs):
        captured["messages"] = kwargs["messages"]
        return None, None

    monkeypatch.setenv("LLM_API_KEY", "k")
    get_settings.cache_clear()
    monkeypatch.setattr("app.services.digest.llm_service.chat_completion", capture)
    try:
        user = User(
            email="tone@example.com",
            display_name="테스터",
            occupation="간호사",
            password_hash=hash_password("abcdefgh"),
            status="approved",
        )
        db_session.add(user)
        db_session.flush()
        pref = Preference(user_id=user.id, topics="경제", notes="")
        db_session.add(pref)
        db_session.flush()
        _llm_curate(
            db_session,
            user,
            pref,
            [SourceItem(kind="아티클", title="A", url="https://a.example", summary="s", source="s")],
        )
    finally:
        get_settings.cache_clear()

    prompt = captured["messages"][1]["content"]
    assert "합니다/습니다" in prompt
    assert "큐레이터입니다" in prompt
    assert "큐레이터다" not in prompt
    assert "간호사" in prompt
    assert "직업:" in prompt
    assert "한국 사용자" in prompt
    assert "선정 신호" in prompt


def test_age_band_and_profile_brief_omit_raw_birthday():
    assert age_band(date(1990, 1, 1), today=date(2026, 8, 16)) == "30대"
    user = User(email="p@example.com", display_name="민수", occupation="내과 의사", birth_date=date(1988, 3, 12))
    brief = profile_brief(user, today=date(2026, 8, 16))
    assert "내과 의사" in brief
    assert "30대" in brief
    assert "1988" not in brief


def test_personalize_candidates_prefers_occupation_tokens():
    user = User(email="p2@example.com", display_name="민수", occupation="반도체 연구원")
    items = [
        SourceItem(kind="아티클", title="연애 팁", url="https://a.example", summary="소개팅", source="라이프"),
        SourceItem(kind="아티클", title="HBM 반도체 공정", url="https://b.example", summary="연구원 시각", source="전자"),
    ]
    ranked = personalize_candidates(items, user)
    assert ranked[0].url == "https://b.example"


def test_personalize_candidates_prefers_korean_articles_and_videos():
    user = User(email="k@example.com", display_name="민수", occupation="")
    items = [
        SourceItem(
            kind="아티클",
            title="Fed holds rates again",
            url="https://www.bloomberg.com/news/rates",
            summary="Treasury yields",
            source="Bloomberg",
        ),
        SourceItem(
            kind="아티클",
            title="코스피, 외국인 매수에 상승",
            url="https://n.news.naver.com/article/001",
            summary="국내 증시 마감",
            source="네이버",
        ),
        SourceItem(
            kind="유튜브",
            title="오늘 시황 브리핑",
            url="https://www.youtube.com/watch?v=krvid",
            summary="국내 시장",
            source="삼프로TV",
        ),
    ]
    ranked = personalize_candidates(items, user)
    assert ranked[0].url.startswith("https://n.news.naver.com") or ranked[0].kind == "유튜브"
    assert ranked[-1].url.startswith("https://www.bloomberg.com")


def test_occupation_tokens_expand_job_aliases():
    tokens = occupation_tokens("내과 의사")
    assert "의사" in tokens
    assert "의료" in tokens


def test_build_digest_preview_llm_prompt_is_shortlist_reviewed_count_is_pool(db_session, monkeypatch):
    captured: dict[str, object] = {}

    def capture(*_a, **kwargs):
        captured["messages"] = kwargs["messages"]
        return None, None

    spam = [
        SourceItem(
            kind="아티클",
            title=f"Buy gadget deal {i:02d} extra",
            url=f"https://spam.example/{i}",
            summary="shop",
            source="Blog",
        )
        for i in range(40)
    ]
    keepers = [
        SourceItem(
            kind="아티클",
            title="코스피 반도체 수출 호조 기록",
            url="https://n.news.naver.com/semi",
            summary="국내 증시",
            source="네이버",
        ),
        SourceItem(
            kind="유튜브",
            title="오늘 시황 브리핑입니다",
            url="https://www.youtube.com/watch?v=kr1",
            summary="국내 시장",
            source="삼프로TV",
        ),
        SourceItem(
            kind="커뮤니티",
            title="국내주식 수급 토론 모음입니다",
            url="https://finance.naver.com/talk",
            summary="수급",
            source="네이버",
        ),
    ]
    pool = keepers + spam
    user = User(
        email="short@example.com",
        display_name="민수",
        occupation="반도체 연구원",
        password_hash=hash_password("abcdefgh"),
        status="approved",
    )
    db_session.add(user)
    db_session.flush()
    pref = Preference(user_id=user.id, topics="경제/주식/국내증시", notes="")
    db_session.add(pref)
    db_session.commit()
    monkeypatch.setattr("app.services.digest.gather_candidates", lambda *a, **k: pool)
    monkeypatch.setenv("LLM_API_KEY", "k")
    get_settings.cache_clear()
    monkeypatch.setattr("app.services.digest.llm_service.chat_completion", capture)
    try:
        preview = build_digest_preview(db_session, user, pref)
    finally:
        get_settings.cache_clear()

    prompt = captured["messages"][1]["content"]
    assert "코스피 반도체" in prompt
    assert "Buy gadget deal 39 extra" not in prompt
    assert len(preview.candidates) == 43
    assert "43개 중 골랐습니다." in preview.body


def test_heuristic_keeps_personalized_order_and_kind_mix():
    user = User(email="p3@example.com", display_name="민수", occupation="반도체 연구원")
    items = [
        SourceItem(kind="아티클", title="연애 팁", url="https://life.example", summary="소개팅", source="라이프"),
        SourceItem(kind="아티클", title="HBM 반도체", url="https://semi.example", summary="공정", source="전자"),
        SourceItem(kind="유튜브", title=" unrelated clip", url="https://yt.example", summary="s", source="yt"),
        SourceItem(kind="커뮤니티", title="잡담", url="https://c.example", summary="s", source="c"),
    ]
    ranked = personalize_candidates(items, user)
    picked = _heuristic_pick(ranked, "seed")
    assert picked[0]["url"] == "https://semi.example"
    assert {row["kind"] for row in picked} >= {"아티클", "유튜브", "커뮤니티"}

