"""Startup schema patch, seeds, lifespan, scheduler, LLM, DB, config extras."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.auth import create_access_token, get_admin_user, hash_password, verify_password
from app.bootstrap import ensure_schema, seed_accounts
from app.config import get_settings
from app.db import Base, get_db
from app.models import Digest, Preference, User
from app.schemas import PreferenceUpdate, RegisterRequest, SendTimeSlot
from app.services import llm as llm_service
from app.services import scheduler as sch
from app.services.digest import (
    _format_body,
    _heuristic_pick,
    _llm_curate,
    _static_fallback,
    create_digest,
)
from app.routers.admin import _daily_series, _pref_detail
from app.services.send_times import encode_send_times, parse_send_times_raw, slot_set
from app.services.sources import SourceItem
from app.catalog.ref_sites import groups_for_mega, site_label
from app.deps.rate_limit import SlidingWindowLimiter, client_ip


def test_ensure_schema_empty_and_legacy(tmp_path, monkeypatch):
    db = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR, "
                "display_name VARCHAR, password_hash VARCHAR, created_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE preferences (id INTEGER PRIMARY KEY, user_id INTEGER, topics VARCHAR, "
                "tone VARCHAR, send_hour INTEGER, send_minute INTEGER, timezone VARCHAR, "
                "enabled BOOLEAN, notes VARCHAR)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE digests (id INTEGER PRIMARY KEY, user_id INTEGER, title VARCHAR, "
                "body VARCHAR, status VARCHAR)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE kakao_accounts (id INTEGER PRIMARY KEY, user_id INTEGER, "
                "kakao_id VARCHAR, access_token VARCHAR, refresh_token VARCHAR)"
            )
        )
    monkeypatch.setattr("app.bootstrap.engine", engine)
    ensure_schema()
    with engine.connect() as conn:
        user_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(users)")).fetchall()}
        pref_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(preferences)")).fetchall()}
        assert "status" in user_cols
        assert "is_admin" in user_cols
        assert "send_times" in pref_cols
        assert "insight_questions" in pref_cols

    empty = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    monkeypatch.setattr("app.bootstrap.engine", empty)
    ensure_schema()


def test_seed_accounts_creates_and_repairs(monkeypatch):
    engine = create_engine("sqlite://")
    TestingSession = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    monkeypatch.setattr("app.bootstrap.SessionLocal", TestingSession)
    seed_accounts()
    db = TestingSession()
    admin = db.query(User).filter(User.email == "admin@example.com").one()
    assert admin.is_admin is True
    admin.status = "pending"
    admin.is_admin = False
    db.commit()
    seed_accounts()
    db.refresh(admin)
    assert admin.status == "approved"
    assert admin.is_admin is True
    db.close()


def test_verify_password_and_admin_guard():
    assert verify_password("x", None) is False
    hashed = hash_password("secret123")
    assert verify_password("secret123", hashed) is True
    with pytest.raises(Exception):
        get_admin_user(SimpleNamespace(is_admin=False))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_lifespan_and_scheduler_start_stop(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr("app.main.Base.metadata.create_all", lambda **_k: calls.append("create"))
    monkeypatch.setattr("app.main.ensure_schema", lambda: calls.append("schema"))
    monkeypatch.setattr("app.main.seed_accounts", lambda: calls.append("seed"))
    monkeypatch.setattr("app.main.start_scheduler", lambda: calls.append("start"))
    monkeypatch.setattr("app.main.stop_scheduler", lambda: calls.append("stop"))
    from app.main import app, lifespan

    async with lifespan(app):
        assert "start" in calls
    assert calls[-1] == "stop"

    class FakeSched:
        running = False

        def add_job(self, *_a, **_k):
            return None

        def start(self):
            self.running = True

        def shutdown(self, wait=False):
            self.running = False

    monkeypatch.setattr(sch, "scheduler", FakeSched())
    sch.start_scheduler()
    sch.start_scheduler()
    sch.stop_scheduler()
    sch.stop_scheduler()


@pytest.mark.asyncio
async def test_tick_morning_digests_paths(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'tick.db'}",
        connect_args={"check_same_thread": False},
    )
    TestingSession = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    db = TestingSession()
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    user = User(
        email="slot@example.com",
        display_name="슬롯",
        password_hash=hash_password("abcdefgh"),
        status="approved",
    )
    pending = User(
        email="p2@example.com",
        display_name="대기",
        password_hash=hash_password("abcdefgh"),
        status="pending",
    )
    db.add_all([user, pending])
    db.flush()
    db.add(
        Preference(
            user_id=user.id,
            topics="경제",
            timezone="Asia/Seoul",
            enabled=True,
            send_times=f"{now.hour:02d}:{now.minute:02d}",
            send_hour=now.hour,
            send_minute=now.minute,
        )
    )
    db.add(Preference(user_id=pending.id, topics="경제", enabled=True, timezone="Asia/Seoul"))
    digest = Digest(
        user_id=user.id,
        title="이미보냄",
        body="x",
        status="sent",
        sent_at=datetime.now(timezone.utc),
    )
    db.add(digest)
    db.commit()
    # Force sent_at local hour/minute match
    digest.sent_at = now.astimezone(timezone.utc)
    db.commit()
    db.close()

    monkeypatch.setattr(sch, "SessionLocal", TestingSession)
    sch._sent_slots.clear()
    await sch.tick_morning_digests()
    await sch.tick_morning_digests()

    db = TestingSession()
    user = db.query(User).filter(User.email == "slot@example.com").one()
    pref = user.preference
    pref.send_times = f"{(now.hour + 1) % 24:02d}:{now.minute:02d}"
    db.commit()
    db.close()
    await sch.tick_morning_digests()

    db = TestingSession()
    user = db.query(User).filter(User.email == "slot@example.com").one()
    user.preference.send_times = f"{now.hour:02d}:{now.minute:02d}"
    db.query(Digest).delete()
    db.commit()
    db.close()
    sch._sent_slots.clear()

    items = [
        SourceItem(kind="아티클", title="A", url="https://a.example", summary="s", source="s"),
        SourceItem(kind="유튜브", title="B", url="https://b.example", summary="s", source="s"),
        SourceItem(kind="커뮤니티", title="C", url="https://c.example", summary="s", source="s"),
    ]
    monkeypatch.setattr("app.services.digest.gather_candidates", lambda *a, **k: items)

    async def send_ok(*_a, **_k):
        return True, ""

    monkeypatch.setattr("app.services.delivery.send_digest_via_kakao", send_ok)
    await sch.tick_morning_digests()


def test_chat_completion_not_configured_and_mocked(monkeypatch):
    get_settings.cache_clear()
    text, usage = llm_service.chat_completion(
        SimpleNamespace(), user_id=1, purpose="t", messages=[{"role": "user", "content": "hi"}]
    )
    assert text is None and usage is None

    monkeypatch.setenv("LLM_API_KEY", "k")
    get_settings.cache_clear()
    try:

        class FakeUsage:
            prompt_tokens = 1
            completion_tokens = 2
            total_tokens = 0

        class FakeMsg:
            content = " hello "

        class FakeChoice:
            message = FakeMsg()

        class FakeResp:
            choices = [FakeChoice()]
            usage = FakeUsage()

        class FakeCompletions:
            def create(self, **_k):
                return FakeResp()

        class FakeChat:
            completions = FakeCompletions()

        class FakeClient:
            def __init__(self, **_k):
                self.chat = FakeChat()

        added: list = []

        class FakeDb:
            def add(self, row):
                added.append(row)

            def flush(self):
                return None

        monkeypatch.setattr("app.services.llm.OpenAI", FakeClient)
        out, row = llm_service.chat_completion(
            FakeDb(), user_id=1, purpose="digest_curate", messages=[{"role": "user", "content": "x"}]
        )
        assert out == "hello"
        assert row.total_tokens == 3

        class Boom:
            def __init__(self, **_k):
                self.chat = SimpleNamespace(
                    completions=SimpleNamespace(create=lambda **_k: (_ for _ in ()).throw(RuntimeError("down")))
                )

        monkeypatch.setattr("app.services.llm.OpenAI", Boom)
        none, err_row = llm_service.chat_completion(
            FakeDb(), user_id=1, purpose="digest_curate", messages=[]
        )
        assert none is None
        assert err_row.success is False
    finally:
        get_settings.cache_clear()


def test_resolved_llm_urls_and_models(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert "openai.com" in s.resolved_llm_base_url
        assert s.resolved_llm_model == "gpt-4o-mini"
        monkeypatch.setenv("LLM_PROVIDER", "custom")
        get_settings.cache_clear()
        s2 = get_settings()
        assert s2.resolved_llm_base_url.endswith("/v1") or "groq" in s2.resolved_llm_base_url
        monkeypatch.setenv("LLM_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("LLM_MODEL", "mine")
        get_settings.cache_clear()
        s3 = get_settings()
        assert s3.resolved_llm_base_url == "https://example.com/v1"
        assert s3.resolved_llm_model == "mine"
    finally:
        get_settings.cache_clear()


def test_get_db_closes(monkeypatch):
    closed = {"n": 0}

    class S:
        def close(self):
            closed["n"] += 1

    monkeypatch.setattr("app.db.SessionLocal", S)
    gen = get_db()
    next(gen)
    gen.close()
    assert closed["n"] == 1


def test_send_times_parse_junk_and_empty_normalize():
    slots = parse_send_times_raw("nope,25:00,07:xx,08:15", hour=7, minute=30)
    assert slots == [SendTimeSlot(hour=8, minute=15)]
    assert slot_set([]) == {(7, 30)}
    assert encode_send_times([]) == "07:30"


def test_schema_validators():
    with pytest.raises(Exception):
        RegisterRequest(email="a@b.com", password="        ", display_name="n")
    with pytest.raises(Exception):
        PreferenceUpdate(topics=["t"] * 61)
    with pytest.raises(Exception):
        PreferenceUpdate(sources=["s"] * 41)
    with pytest.raises(Exception):
        PreferenceUpdate(send_times=[])
    with pytest.raises(Exception):
        PreferenceUpdate(send_times=[SendTimeSlot(hour=1, minute=0)] * 6)
    empty = PreferenceUpdate()
    assert empty.topics is None


def test_catalog_unknown_and_mega():
    assert site_label("not-a-site") == "not-a-site"
    groups = groups_for_mega("IT")
    assert groups


def test_rate_limit_and_client_ip():
    limiter = SlidingWindowLimiter()
    limiter.check("k", max_calls=1, window_seconds=60)
    with pytest.raises(Exception):
        limiter.check("k", max_calls=1, window_seconds=60)
    req = SimpleNamespace(headers={"x-forwarded-for": "1.1.1.1, 2.2.2.2"}, client=None)
    assert client_ip(req) == "1.1.1.1"
    req2 = SimpleNamespace(headers={}, client=SimpleNamespace(host="9.9.9.9"))
    assert client_ip(req2) == "9.9.9.9"
    req3 = SimpleNamespace(headers={}, client=None)
    assert client_ip(req3) == "unknown"


def test_heuristic_llm_and_create_digest(monkeypatch):
    engine = create_engine("sqlite://")
    Session = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    db = Session()
    user = User(
        email="d@example.com",
        display_name="",
        password_hash=hash_password("abcdefgh"),
        status="approved",
    )
    db.add(user)
    db.flush()
    pref = Preference(
        user_id=user.id,
        topics="경제",
        timezone="UTC",
        notes="짧게",
        insight_questions=True,
        sources="hn",
    )
    db.add(pref)
    db.commit()

    cands = [
        SourceItem(kind="아티클", title="금리 연준", url="https://a.example", summary="아티클", source="아티클"),
        SourceItem(kind="아티클", title="A2", url="https://a2.example", summary="s", source="s"),
        SourceItem(kind="유튜브", title="B", url="https://b.example", summary="s", source="s"),
        SourceItem(kind="커뮤니티", title="C", url="https://c.example", summary="s", source="s"),
    ]
    picked = _heuristic_pick(cands, "seed")
    assert len(picked) == 3
    assert _heuristic_pick([], "s") == []
    assert _static_fallback(["경제"], "seed")

    monkeypatch.setattr("app.services.digest.gather_candidates", lambda *a, **k: cands)
    monkeypatch.setenv("LLM_API_KEY", "")
    get_settings.cache_clear()
    try:
        digest = create_digest(db, user, pref, status="draft")
        assert digest.id
        assert pref.timezone == "Asia/Seoul"
        body = _format_body("너", picked, pref, ["경제"])
        assert "요청 반영" in body or "오늘의 3" in body
    finally:
        get_settings.cache_clear()

    monkeypatch.setenv("LLM_API_KEY", "k")
    get_settings.cache_clear()
    try:
        none, reason, _ = _llm_curate(db, user, pref, [])
        assert reason == "no_candidates"

        def fake_chat(*_a, **_k):
            return None, None

        monkeypatch.setattr("app.services.digest.llm_service.chat_completion", fake_chat)
        none, reason, _ = _llm_curate(db, user, pref, cands)
        assert reason == "empty_response"

        def few(*_a, **_k):
            return '{"title":"","items":[{"kind":"아티클","title":"t","blurb":"b","url":"https://nope.example"}]}', None

        monkeypatch.setattr("app.services.digest.llm_service.chat_completion", few)
        none, reason, _ = _llm_curate(db, user, pref, cands)
        assert reason == "fewer_than_3_items"

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
        import json

        monkeypatch.setattr(
            "app.services.digest.llm_service.chat_completion",
            lambda *_a, **_k: (f"```json\n{json.dumps(payload)}\n```", None),
        )
        result, reason, raw = _llm_curate(db, user, pref, cands)
        assert result is not None
        assert reason == ""

        monkeypatch.setattr(
            "app.services.digest.llm_service.chat_completion",
            lambda *_a, **_k: ("not-json", None),
        )
        none, reason, _ = _llm_curate(db, user, pref, cands)
        assert reason == "invalid_json"

        bad_urls = {
            "title": "x",
            "items": [
                {"kind": "아티클", "title": "t", "blurb": "b", "url": "https://zzz.example"},
                {"kind": "아티클", "title": "t", "blurb": "b", "url": "https://yyy.example"},
                {"kind": "아티클", "title": "t", "blurb": "b", "url": "https://xxx.example"},
            ],
        }
        monkeypatch.setattr(
            "app.services.digest.llm_service.chat_completion",
            lambda *_a, **_k: (json.dumps(bad_urls), None),
        )
        none, reason, _ = _llm_curate(db, user, pref, cands)
        assert reason == "fewer_than_3_valid_urls"
    finally:
        get_settings.cache_clear()
    db.close()


def test_admin_series_and_pref_detail():
    old = datetime.now(timezone.utc) - timedelta(days=40)
    users = [
        SimpleNamespace(created_at=None, approved_at=None, status="approved"),
        SimpleNamespace(created_at=old, approved_at=None, status="approved"),
        SimpleNamespace(
            created_at=datetime.now(timezone.utc),
            approved_at=None,
            status="pending",
        ),
    ]
    usage = [
        SimpleNamespace(created_at=None, total_tokens=1, prompt_tokens=1, completion_tokens=0, success=True),
        SimpleNamespace(created_at=old, total_tokens=5, prompt_tokens=2, completion_tokens=3, success=True),
        SimpleNamespace(
            created_at=datetime.now(timezone.utc) + timedelta(days=400),
            total_tokens=1,
            prompt_tokens=0,
            completion_tokens=0,
            success=False,
        ),
    ]
    series = _daily_series(users, usage, days=14)
    assert len(series) == 14
    assert _pref_detail(None) is None
    pref = Preference(
        topics="a,b",
        sources="hn",
        notes="",
        send_times="07:30",
        timezone="Asia/Seoul",
        enabled=True,
    )
    detail = _pref_detail(pref)
    assert detail is not None
    assert detail.topics == ["a", "b"]


def test_create_access_token_roundtrip():
    token = create_access_token(1)
    assert isinstance(token, str) and len(token) > 10
