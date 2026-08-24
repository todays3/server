"""Digest heuristic pick, create_digest, and LLM curation contracts."""

from datetime import date
from types import SimpleNamespace

import json

from sqlalchemy.orm import Session

from app.auth import hash_password
from app.config import get_settings
from app.models import Preference, User
from app.services.digest import (
    _curate_stock_pick,
    _heuristic_pick,
    _llm_curate,
    _static_fallback,
    _why_for_item,
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
        assert "선정이유:" in digest.body
        assert any(ch.isdigit() for ch in digest.body.split("선정이유:")[1][:48])
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


def test_llm_curate_rate_limited(db_session, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    get_settings.cache_clear()
    usage = SimpleNamespace(
        error_message="Error code: 429 - rate_limit_exceeded. Please try again in 44.4s.",
        success=False,
    )
    monkeypatch.setattr("app.services.digest.llm_service.chat_completion", lambda *_a, **_k: (None, usage))
    try:
        user = _approved_user(db_session, "c1b@example.com")
        pref = Preference(user_id=user.id, topics="경제", insight_questions=True)
        db_session.add(pref)
        db_session.flush()
        none, reason, _ = _llm_curate(db_session, user, pref, _cands())
        assert none is None
        assert reason == "rate_limited"
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
            points=214,
            comments=87,
            list_rank=1,
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
        assert items[0]["why"] != "좋아요 2만"
        assert "좋아요 2만" not in body
        assert "214" in items[0]["why"]
        assert "프론트페이지" not in items[0]["why"]
    finally:
        get_settings.cache_clear()


def test_why_for_item_drops_qualitative_text_without_digits():
    why = _why_for_item(
        {"kind": "아티클", "url": "https://missing.example", "why": "한경 헤드라인"},
        [],
    )
    assert any(ch.isdigit() for ch in why)
    assert "헤드라인" not in why


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


def test_llm_curate_retries_remote_when_local_json_invalid(db_session, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "hybrid")
    get_settings.cache_clear()
    payload = {
        "title": "하루만장 · 8/22 (토)",
        "items": [
            {"kind": "아티클", "title": "t1", "blurb": "b입니다.", "url": "https://a.example", "angle": "흐름"},
            {"kind": "유튜브", "title": "t2", "blurb": "b입니다.", "url": "https://b.example", "angle": "이슈"},
            {"kind": "커뮤니티", "title": "t3", "blurb": "b입니다.", "url": "https://c.example", "angle": "인물"},
        ],
    }
    calls: list[dict] = []

    def fake_chat(_db, **kwargs):
        from types import SimpleNamespace
        import json

        calls.append(kwargs)
        if kwargs.get("force_remote"):
            return json.dumps(payload, ensure_ascii=False), SimpleNamespace(provider="groq")
        return "{not json", SimpleNamespace(provider="ollama")

    monkeypatch.setattr("app.services.digest.llm_service.chat_completion", fake_chat)
    try:
        user = _approved_user(db_session, "c4b@example.com")
        pref = Preference(user_id=user.id, topics="경제", insight_questions=False)
        db_session.add(pref)
        db_session.flush()
        result, reason, _ = _llm_curate(db_session, user, pref, _cands())
        assert reason == ""
        assert result is not None
        assert len(calls) == 2
        assert calls[1].get("force_remote") is True
    finally:
        get_settings.cache_clear()


def test_llm_curate_salvages_wrapped_json(db_session, monkeypatch):
    payload = {
        "title": "하루만장",
        "items": [
            {"kind": "아티클", "title": "t1", "blurb": "b입니다.", "url": "https://a.example"},
            {"kind": "유튜브", "title": "t2", "blurb": "b입니다.", "url": "https://b.example"},
            {"kind": "커뮤니티", "title": "t3", "blurb": "b입니다.", "url": "https://c.example"},
        ],
    }
    messy = "Here you go.\n```json\n" + json.dumps(payload) + "\n```\n"
    monkeypatch.setenv("LLM_API_KEY", "k")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.services.digest.llm_service.chat_completion",
        lambda *_a, **_k: (messy, None),
    )
    try:
        user = _approved_user(db_session, "c-json@example.com")
        pref = Preference(user_id=user.id, topics="경제", notes="")
        db_session.add(pref)
        db_session.flush()
        result, reason, _ = _llm_curate(db_session, user, pref, _cands())
        assert reason == ""
        assert result is not None
        _title, _body, items = result
        assert len(items) == 3
        assert items[0]["url"] == "https://a.example"
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
    assert "Kakao" in prompt
    assert "큐레이터다" not in prompt
    assert "간호사" in prompt
    assert "Job:" in prompt
    assert "Korean users" in prompt
    assert "흐름" in prompt
    assert "인물" in prompt
    assert "JSON object only" in prompt


def test_llm_curate_developer_prompt_prioritizes_latest_verified_tech(db_session, monkeypatch):
    captured: dict[str, object] = {}

    def capture(*_a, **kwargs):
        captured["messages"] = kwargs["messages"]
        return None, None

    monkeypatch.setenv("LLM_API_KEY", "k")
    get_settings.cache_clear()
    monkeypatch.setattr("app.services.digest.llm_service.chat_completion", capture)
    try:
        user = User(
            email="dev@example.com",
            display_name="개발자",
            occupation="백엔드",
            password_hash=hash_password("abcdefgh"),
            status="approved",
        )
        db_session.add(user)
        db_session.flush()
        pref = Preference(user_id=user.id, topics="IT/개발", notes="", roles="developer")
        db_session.add(pref)
        db_session.flush()
        _llm_curate(
            db_session,
            user,
            pref,
            [SourceItem(kind="아티클", title="Rust async 트렌드", url="https://a.example", summary="도입", source="HN")],
            role="developer",
        )
    finally:
        get_settings.cache_clear()

    prompt = captured["messages"][1]["content"]
    assert "Developer desk rules" in prompt
    assert "Priority #1" in prompt
    assert "verified" in prompt.lower()
    assert "hiring/move" in prompt.lower() or "hiring" in prompt.lower()
    assert "evergreen" in prompt.lower()
    assert "latest tech" in prompt.lower()


def test_age_band_and_profile_brief_omit_raw_birthday():
    assert age_band(date(1990, 1, 1), today=date(2026, 8, 16)) == "30대"
    user = User(email="p@example.com", display_name="민수", occupation="내과 의사", birth_date=date(1988, 3, 12))
    brief = profile_brief(user, today=date(2026, 8, 16))
    assert "내과 의사" in brief
    assert "30대" in brief
    assert "1988" not in brief
    assert "Job:" in brief
    assert "Age band:" in brief


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
    assert "43개 중 골랐습니다." not in preview.body
    assert "오늘 43개 중에 고른" in preview.body


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


def test_build_digest_preview_picks_three_items_per_hired_assistant(db_session, monkeypatch):
    user = _approved_user(db_session, "roles2@example.com")
    pref = Preference(
        user_id=user.id,
        topics="IT/개발/all,음악/장르/K-POP",
        roles="developer,music",
        timezone="Asia/Seoul",
        sources="hn,github-trending,okky,melon,genie,hanteo",
    )
    db_session.add(pref)
    db_session.commit()
    pool = [
        SourceItem(kind="아티클", title="React 19", url="https://it1.example", summary="프론트", source="HN", site_id="hn"),
        SourceItem(kind="유튜브", title="타입스크립트", url="https://it2.example", summary="언어", source="YT", site_id="github-trending"),
        SourceItem(kind="커뮤니티", title="OKKY", url="https://it3.example", summary="개발", source="OKKY", site_id="okky"),
        SourceItem(kind="아티클", title="멜론 차트", url="https://mu1.example", summary="차트", source="멜론", site_id="melon"),
        SourceItem(kind="유튜브", title="신곡 MV", url="https://mu2.example", summary="뮤비", source="지니", site_id="genie"),
        SourceItem(kind="커뮤니티", title="한터", url="https://mu3.example", summary="음반", source="한터", site_id="hanteo"),
    ]
    monkeypatch.setattr("app.services.digest.gather_candidates", lambda *a, **k: pool)
    monkeypatch.setenv("LLM_API_KEY", "")
    get_settings.cache_clear()
    try:
        preview = build_digest_preview(db_session, user, pref)
    finally:
        get_settings.cache_clear()
    assert len(preview.items) == 6
    assistants = [row.get("assistant") for row in preview.items]
    assert assistants.count("민준") == 3
    assert assistants.count("하람") == 3
    assert preview.body.count("고른 3개입니다.") == 2
    dev_urls = {row["url"] for row in preview.items if row.get("role") == "developer"}
    music_urls = {row["url"] for row in preview.items if row.get("role") == "music"}
    assert not any("mu" in url for url in dev_urls)
    assert not any("it" in url for url in music_urls)


def test_build_digest_preview_stock_analyst_sends_one_stock_no_articles(db_session, monkeypatch):
    user = _approved_user(db_session, "analyst@example.com")
    pref = Preference(
        user_id=user.id,
        topics="경제/주식/국내증시/반도체",
        roles="stock_analyst",
        timezone="Asia/Seoul",
        role_settings='{"assistant_names":{"stock_analyst":"도윤"}}',
        sources="",
    )
    db_session.add(pref)
    db_session.commit()
    pool = [
        SourceItem(
            kind="아티클",
            title="SK하이닉스 HBM 수주",
            url="https://eco1.example",
            summary="경제 반도체 메모리입니다.",
            source="한경",
        ),
        SourceItem(
            kind="아티클",
            title="코스피 반도체 강세",
            url="https://eco2.example",
            summary="경제 시황입니다.",
            source="네이버",
        ),
        SourceItem(
            kind="아티클",
            title="AI 서버 투자",
            url="https://eco3.example",
            summary="경제 데이터센터입니다.",
            source="한경",
        ),
    ]
    monkeypatch.setattr("app.services.digest.gather_candidates", lambda *a, **k: pool)
    monkeypatch.setenv("LLM_API_KEY", "")
    get_settings.cache_clear()
    try:
        preview = build_digest_preview(db_session, user, pref)
    finally:
        get_settings.cache_clear()
    assert len(preview.items) == 1
    assert preview.items[0]["kind"] == "종목"
    assert preview.items[0]["assistant"] == "도윤"
    assert "첫째" not in preview.body
    assert "오늘 시장에서 종목 1개를 골랐습니다" in preview.body
    assert "매수 추천이 아닙니다" in preview.body


def test_filter_candidates_for_role_respects_mega_and_user_sources():
    from app.services.digest import filter_candidates_for_role

    pool = [
        SourceItem(kind="아티클", title="React", url="https://it1.example", summary="", source="HN", site_id="hn"),
        SourceItem(kind="아티클", title="Melon", url="https://mu1.example", summary="", source="멜론", site_id="melon"),
        SourceItem(
            kind="아티클",
            title="주식 뉴스",
            url="https://fin.example",
            summary="코스피",
            source="네이버",
            site_id="naver-finance",
        ),
    ]
    dev_pool = filter_candidates_for_role(
        pool,
        "developer",
        user_sources=["hn", "melon"],
        role_topics=["IT/개발/all"],
    )
    assert [item.site_id for item in dev_pool] == ["hn"]
    music_pool = filter_candidates_for_role(
        pool,
        "music",
        user_sources=["hn", "melon"],
        role_topics=["음악/장르/K-POP"],
    )
    assert [item.site_id for item in music_pool] == ["melon"]


def test_filter_candidates_keeps_investor_on_market_drops_process_news():
    from app.services.digest import filter_candidates_for_role

    pool = [
        SourceItem(
            kind="아티클",
            title="코스피 반도체주 강세",
            url="https://mkt.example",
            summary="수급 유입",
            source="한경",
            site_id="hankyung",
        ),
        SourceItem(
            kind="아티클",
            title="EUV 공정 수율 개선",
            url="https://semi.example",
            summary="ISSCC 발표",
            source="IEEE",
            site_id="topic-반도체",
        ),
        SourceItem(
            kind="아티클",
            title="연애 상담 칼럼",
            url="https://life.example",
            summary="소개팅",
            source="블로그",
            site_id="topic-경제",
        ),
    ]
    got = filter_candidates_for_role(
        pool,
        "investor",
        user_sources=["hankyung"],
        role_topics=["경제/주식/국내증시/시황"],
    )
    urls = {item.url for item in got}
    assert "https://mkt.example" in urls
    assert "https://semi.example" not in urls
    assert "https://life.example" not in urls


def test_filter_candidates_keeps_semiconductor_on_process_drops_stock_tape():
    from app.services.digest import filter_candidates_for_role

    pool = [
        SourceItem(
            kind="아티클",
            title="GAA 트랜지스터 수율",
            url="https://proc.example",
            summary="EUV 공정",
            source="IEDM",
            site_id="iedm",
        ),
        SourceItem(
            kind="아티클",
            title="코스피 급등 목표가 상향",
            url="https://tape.example",
            summary="매수의견",
            source="증권",
            site_id="topic-반도체",
        ),
        SourceItem(
            kind="아티클",
            title="국내 IT 스타트업 뉴스",
            url="https://it.example",
            summary="개발",
            source="긱뉴스",
            site_id="geeknews",
        ),
    ]
    got = filter_candidates_for_role(
        pool,
        "semiconductor",
        user_sources=["iedm", "geeknews"],
        role_topics=["반도체/기술동향/all"],
    )
    urls = {item.url for item in got}
    assert "https://proc.example" in urls
    assert "https://tape.example" not in urls
    assert "https://it.example" not in urls


def test_format_body_appends_match_stock_not_as_fourth_article():
    pref = Preference(roles="investor", insight_questions=False, notes="")
    items = [
        {"kind": "아티클", "title": "HBM 수요", "blurb": "하이닉스 수주입니다.", "url": "https://a.example", "why": "한경"},
        {"kind": "아티클", "title": "AI 서버", "blurb": "투자 지속입니다.", "url": "https://b.example", "why": "한경"},
        {"kind": "아티클", "title": "반도체 강세", "blurb": "대형주입니다.", "url": "https://c.example", "why": "한경"},
        {
            "kind": "종목",
            "title": "SK하이닉스 (000660)",
            "blurb": "오늘 고른 이슈 3개와 가장 맞닿아 있는 종목입니다. 매수 추천이 아닙니다.",
            "url": "https://finance.naver.com/item/coinfo.naver?code=000660",
            "hint": "match_stock",
        },
    ]
    from app.services.digest import _format_body

    body = _format_body("테스트", items, pref, ["경제"], reviewed_count=12)
    assert "고른 3개입니다" in body
    assert "넷째" not in body
    assert "오늘의 종목 · 매수 추천이 아닙니다" in body
    assert "SK하이닉스 (000660)" in body
    assert "재무제표: https://finance.naver.com/item/coinfo.naver?code=000660" in body


def test_format_body_stock_analyst_only_omits_article_count():
    pref = Preference(roles="stock_analyst", insight_questions=False, notes="")
    items = [
        {
            "kind": "종목",
            "title": "SK하이닉스 (000660)",
            "blurb": "오늘 시장 흐름에 맞춰 짚은 종목입니다. 매수 추천이 아닙니다.",
            "url": "https://finance.naver.com/item/coinfo.naver?code=000660",
            "hint": "match_stock",
        },
    ]
    from app.services.digest import _format_body

    body = _format_body("테스트", items, pref, ["경제"], reviewed_count=12)
    assert "오늘 시장에서 종목 1개를 골랐습니다" in body
    assert "고른 0개입니다" not in body
    assert "첫째" not in body
    assert "오늘의 종목 · 매수 추천이 아닙니다" in body
    assert "SK하이닉스 (000660)" in body


def test_label_picked_items_adds_heuristic_stock_for_analyst():
    from app.services.digest import _label_picked_items

    pref = Preference(roles="stock_analyst", role_settings="{}", notes="")
    picked = [
        {"kind": "아티클", "title": "SK하이닉스 HBM 수주", "blurb": "메모리입니다.", "url": "https://a.example", "why": "한경"},
        {"kind": "아티클", "title": "AI 서버", "blurb": "투자입니다.", "url": "https://b.example", "why": "한경"},
        {"kind": "아티클", "title": "코스피", "blurb": "강세입니다.", "url": "https://c.example", "why": "한경"},
    ]
    labeled = _label_picked_items(picked, pref, [], ["경제/주식/국내증시"], role="stock_analyst")
    stocks = [row for row in labeled if row["kind"] == "종목"]
    assert len(stocks) == 1
    assert stocks[0]["ticker"] == "000660"
    assert "추천이 아닙니다" in stocks[0]["blurb"]


def test_label_picked_items_skips_stock_for_investor():
    from app.services.digest import _label_picked_items

    pref = Preference(
        roles="investor",
        role_settings='{"investor_match_stock": true}',
        notes="",
    )
    picked = [
        {"kind": "아티클", "title": "삼성전자 실적", "blurb": "반도체입니다.", "url": "https://a.example", "why": "한경"},
        {"kind": "아티클", "title": "AI 서버", "blurb": "투자입니다.", "url": "https://b.example", "why": "한경"},
        {"kind": "아티클", "title": "코스피", "blurb": "강세입니다.", "url": "https://c.example", "why": "한경"},
    ]
    labeled = _label_picked_items(picked, pref, [], ["경제"], role="investor")
    assert all(row["kind"] != "종목" for row in labeled)


def test_llm_curate_ignores_match_stock_for_investor(db_session, monkeypatch):
    payload = {
        "title": "하루만장",
        "items": [
            {"kind": "아티클", "title": "t1", "blurb": "b입니다.", "url": "https://a.example", "angle": "흐름"},
            {"kind": "유튜브", "title": "t2", "blurb": "b입니다.", "url": "https://b.example", "angle": "이슈"},
            {"kind": "커뮤니티", "title": "t3", "blurb": "b입니다.", "url": "https://c.example", "angle": "인물"},
        ],
        "match_stock": {"name": "삼성전자", "ticker": "005930", "market": "KR", "why": "실적 이슈"},
    }
    monkeypatch.setenv("LLM_API_KEY", "k")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.services.digest.llm_service.chat_completion",
        lambda *_a, **_k: (json.dumps(payload), None),
    )
    try:
        user = _approved_user(db_session, "investor-stock@example.com")
        pref = Preference(user_id=user.id, topics="경제", roles="investor", notes="")
        db_session.add(pref)
        db_session.flush()
        result, reason, _raw = _llm_curate(db_session, user, pref, _cands())
        assert reason == ""
        assert result is not None
        _title, _body, items = result
        assert all(row["kind"] != "종목" for row in items)
        assert len(items) == 3
    finally:
        get_settings.cache_clear()


def test_llm_curate_uses_match_stock_from_json(db_session, monkeypatch):
    payload = {"match_stock": {"name": "삼성전자", "ticker": "005930", "market": "KR", "why": "실적 이슈"}}
    monkeypatch.setenv("LLM_API_KEY", "k")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.services.digest.llm_service.chat_completion",
        lambda *_a, **_k: (json.dumps(payload), None),
    )
    try:
        user = _approved_user(db_session, "stock@example.com")
        pref = Preference(user_id=user.id, topics="경제", roles="stock_analyst", notes="")
        db_session.add(pref)
        db_session.flush()
        items, _title, curator, reason, _raw = _curate_stock_pick(
            db_session,
            user,
            pref,
            _cands(),
            {},
            None,
        )
        assert reason == ""
        assert curator == "llm"
        assert len(items) == 1
        assert items[0]["kind"] == "종목"
        assert "005930" in items[0]["title"]
        assert "매수 추천이 아닙니다" in items[0]["blurb"]
    finally:
        get_settings.cache_clear()

