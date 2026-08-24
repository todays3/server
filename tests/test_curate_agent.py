import json

from app.auth import hash_password
from app.config import get_settings
from app.models import Preference, User
from app.services.curate_agent import (
    CRITIQUE_PURPOSE,
    MAX_FEEDBACK_LOOPS,
    Critique,
    parse_critique,
    should_retry,
)
from app.services.digest import _curate_three
from app.services.sources import SourceItem


def _user(db, email: str = "agent@example.com") -> User:
    user = User(
        email=email,
        display_name="테스터",
        password_hash=hash_password("abcdefgh"),
        status="approved",
    )
    db.add(user)
    db.flush()
    return user


def _items_payload() -> dict:
    return {
        "title": "하루만장",
        "items": [
            {
                "kind": "아티클",
                "title": "시황입니다.",
                "blurb": "흐름입니다.",
                "url": "https://a.example",
                "angle": "흐름",
            },
            {
                "kind": "유튜브",
                "title": "업데이트입니다.",
                "blurb": "출시입니다.",
                "url": "https://b.example",
                "angle": "이슈",
            },
            {
                "kind": "커뮤니티",
                "title": "인터뷰입니다.",
                "blurb": "인물입니다.",
                "url": "https://c.example",
                "angle": "인물",
            },
        ],
    }


def test_parse_critique_reads_ok_false():
    got = parse_critique('{"ok":false,"reason":"한 사이트","missing":["인물"],"advice":"다른 URL"}')
    assert got is not None
    assert got.ok is False
    assert "인물" in got.missing
    assert "다른 URL" in got.advice


def test_critique_prompt_is_english_instructions():
    from app.services.curate_agent import build_critique_prompt

    prompt = build_critique_prompt(
        items=[{"kind": "아티클", "title": "시황입니다.", "url": "https://a.example", "angle": "흐름"}],
        candidate_block="1. keep",
        profile="Job: 간호사",
        topics="경제",
    )
    assert "JSON only" in prompt
    assert "너는" not in prompt
    assert "흐름" in prompt
    assert "시황입니다." in prompt


def test_should_retry_caps_at_one_loop():
    bad = Critique(ok=False, advice="다시")
    assert should_retry(bad, 0) is True
    assert should_retry(bad, MAX_FEEDBACK_LOOPS) is False
    assert should_retry(Critique(ok=True), 0) is False
    assert should_retry(None, 0) is False


def test_curate_three_retries_once_when_critique_fails(db_session, monkeypatch):
    calls: list[str] = []

    def fake(*_a, **kwargs):
        purpose = kwargs.get("purpose") or _a[2]
        calls.append(purpose)
        if purpose == CRITIQUE_PURPOSE:
            return json.dumps(
                {"ok": False, "reason": "같은 사이트", "missing": ["인물"], "advice": "인물 후보 URL을 넣으세요"}
            ), None
        return json.dumps(_items_payload()), None

    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    get_settings.cache_clear()
    monkeypatch.setattr("app.services.digest.llm_service.chat_completion", fake)
    try:
        user = _user(db_session)
        pref = Preference(user_id=user.id, topics="IT", notes="")
        db_session.add(pref)
        db_session.flush()
        cands = [
            SourceItem(kind="아티클", title="시황", url="https://a.example", summary="s", source="s"),
            SourceItem(kind="유튜브", title="업데이트", url="https://b.example", summary="s", source="s"),
            SourceItem(kind="커뮤니티", title="인터뷰", url="https://c.example", summary="s", source="s"),
        ]
        items, _title, curator, _skip, _raw = _curate_three(
            db_session, user, pref, cands, ["IT"], "seed", {}, None, cands
        )
    finally:
        get_settings.cache_clear()
    assert curator == "llm"
    assert [row["angle"] for row in items[:3]] == ["흐름", "이슈", "인물"]
    assert calls.count("digest_curate") == 2
    assert calls.count(CRITIQUE_PURPOSE) == 1
    assert len(calls) == 3


def test_curate_three_skips_second_critique_after_retry(db_session, monkeypatch):
    calls: list[str] = []

    def fake(*_a, **kwargs):
        purpose = kwargs.get("purpose") or _a[2]
        calls.append(purpose)
        if purpose == CRITIQUE_PURPOSE:
            return json.dumps({"ok": False, "reason": "일률적", "missing": [], "advice": "사이트를 나누세요"}), None
        return json.dumps(_items_payload()), None

    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    get_settings.cache_clear()
    monkeypatch.setattr("app.services.digest.llm_service.chat_completion", fake)
    try:
        user = _user(db_session, "agent2@example.com")
        pref = Preference(user_id=user.id, topics="경제", notes="")
        db_session.add(pref)
        db_session.flush()
        cands = [
            SourceItem(kind="아티클", title="A1", url="https://a.example", summary="s", source="s"),
            SourceItem(kind="유튜브", title="B1", url="https://b.example", summary="s", source="s"),
            SourceItem(kind="커뮤니티", title="C1", url="https://c.example", summary="s", source="s"),
        ]
        _curate_three(db_session, user, pref, cands, ["경제"], "seed", {}, None, cands)
    finally:
        get_settings.cache_clear()
    assert calls == ["digest_curate", CRITIQUE_PURPOSE, "digest_curate"]


def test_curate_three_does_not_retry_when_critique_ok(db_session, monkeypatch):
    calls: list[str] = []

    def fake(*_a, **kwargs):
        purpose = kwargs.get("purpose") or _a[2]
        calls.append(purpose)
        if purpose == CRITIQUE_PURPOSE:
            return json.dumps({"ok": True, "reason": "세 관점이 있습니다", "missing": [], "advice": ""}), None
        return json.dumps(_items_payload()), None

    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    get_settings.cache_clear()
    monkeypatch.setattr("app.services.digest.llm_service.chat_completion", fake)
    try:
        user = _user(db_session, "agent3@example.com")
        pref = Preference(user_id=user.id, topics="경제", notes="")
        db_session.add(pref)
        db_session.flush()
        cands = [
            SourceItem(kind="아티클", title="A1", url="https://a.example", summary="s", source="s"),
            SourceItem(kind="유튜브", title="B1", url="https://b.example", summary="s", source="s"),
            SourceItem(kind="커뮤니티", title="C1", url="https://c.example", summary="s", source="s"),
        ]
        _curate_three(db_session, user, pref, cands, ["경제"], "seed", {}, None, cands)
    finally:
        get_settings.cache_clear()
    assert calls == ["digest_curate", CRITIQUE_PURPOSE]


def test_curate_three_skips_critique_on_hybrid_local(db_session, monkeypatch):
    calls: list[str] = []

    def fake(*_a, **kwargs):
        purpose = kwargs.get("purpose") or _a[2]
        calls.append(purpose)
        return json.dumps(_items_payload()), None

    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "hybrid")
    monkeypatch.setenv("LLM_LOCAL_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr("app.services.digest.llm_service.chat_completion", fake)
    try:
        user = _user(db_session, "hybrid-skip@example.com")
        pref = Preference(user_id=user.id, topics="경제", notes="")
        db_session.add(pref)
        db_session.flush()
        cands = [
            SourceItem(kind="아티클", title="A1", url="https://a.example", summary="s", source="s"),
            SourceItem(kind="유튜브", title="B1", url="https://b.example", summary="s", source="s"),
            SourceItem(kind="커뮤니티", title="C1", url="https://c.example", summary="s", source="s"),
        ]
        _curate_three(db_session, user, pref, cands, ["경제"], "seed", {}, None, cands)
    finally:
        get_settings.cache_clear()
    assert calls == ["digest_curate"]
