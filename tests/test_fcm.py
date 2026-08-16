"""FCM device registry: one Kakao user, many device tokens."""

from app.models import Digest, PushDevice, User
from app.services.delivery import deliver_digest
from app.services.fcm import (
    MAX_DEVICES_PER_USER,
    fcm_web_message,
    firebase_messaging_sw_source,
    notify_digest_sent,
    prune_invalid_tokens,
    register_push_device,
    unregister_push_device,
)


def _user(db_session, email: str = "p@example.com") -> User:
    user = User(email=email, display_name="푸시", status="approved")
    db_session.add(user)
    db_session.flush()
    return user


def test_register_keeps_multiple_tokens_per_user(db_session):
    user = _user(db_session)
    register_push_device(db_session, user.id, token="token-phone", platform="web", device_id="phone")
    register_push_device(db_session, user.id, token="token-laptop", platform="web", device_id="laptop")
    db_session.commit()
    tokens = {row.token for row in db_session.query(PushDevice).filter_by(user_id=user.id)}
    assert tokens == {"token-phone", "token-laptop"}


def test_same_token_moves_to_the_logged_in_user(db_session):
    a = _user(db_session, "a@example.com")
    b = _user(db_session, "b@example.com")
    register_push_device(db_session, a.id, token="shared-token", platform="web", device_id="d1")
    register_push_device(db_session, b.id, token="shared-token", platform="web", device_id="d1")
    db_session.commit()
    rows = db_session.query(PushDevice).filter_by(token="shared-token").all()
    assert len(rows) == 1
    assert rows[0].user_id == b.id


def test_same_device_id_rotates_token(db_session):
    user = _user(db_session)
    register_push_device(db_session, user.id, token="old-token", platform="web", device_id="browser-1")
    register_push_device(db_session, user.id, token="new-token", platform="web", device_id="browser-1")
    db_session.commit()
    rows = db_session.query(PushDevice).filter_by(user_id=user.id).all()
    assert [row.token for row in rows] == ["new-token"]


def test_fcm_web_message_is_data_only_so_chrome_does_not_auto_display():
    payload = fcm_web_message("device-token", "하루만장 · 8/16", "미리보기")
    message = payload["message"]
    assert "notification" not in message
    assert "notification" not in message.get("webpush", {})
    assert message["data"]["title"] == "하루만장 · 8/16"
    assert message["data"]["body"] == "미리보기"
    assert message["data"]["url"] == "/app"


def test_fcm_sw_skips_show_when_fcm_already_displayed_notification(monkeypatch):
    monkeypatch.setenv("FIREBASE_WEB_API_KEY", "web-key")
    monkeypatch.setenv("FIREBASE_WEB_APP_ID", "1:1:web:abc")
    monkeypatch.setenv("FIREBASE_WEB_MESSAGING_SENDER_ID", "123")
    monkeypatch.setenv("FIREBASE_WEB_VAPID_KEY", "vapid")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-proj")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        src = firebase_messaging_sw_source()
    finally:
        get_settings.cache_clear()
    assert "onBackgroundMessage" in src
    assert "if (n.title || n.body)" in src
    assert "showNotification" in src
    assert "todays3-digest" in src


def test_notify_sends_once_per_device_even_when_kakao_body_is_long(db_session):
    user = _user(db_session)
    register_push_device(db_session, user.id, token="only-phone", platform="web", device_id="phone")
    db_session.commit()
    sent: list[tuple[str, str]] = []

    def send_one(token: str, title: str, body: str) -> str:
        sent.append((token, title))
        _ = body
        return "ok"

    n = notify_digest_sent(db_session, user.id, "하루만장", "가" * 2500, send_one=send_one)
    assert n == 1
    assert sent == [("only-phone", "하루만장")]


def test_notify_sends_to_every_device_and_drops_gone_tokens(db_session):
    user = _user(db_session)
    register_push_device(db_session, user.id, token="alive", platform="web", device_id="a")
    register_push_device(db_session, user.id, token="dead", platform="web", device_id="b")
    db_session.commit()
    sent: list[str] = []

    def send_one(token: str, title: str, body: str) -> str:
        _ = (title, body)
        sent.append(token)
        return "gone" if token == "dead" else "ok"

    n = notify_digest_sent(db_session, user.id, "하루만장", "본문입니다", send_one=send_one)
    assert n == 1
    assert sent == ["alive", "dead"] or set(sent) == {"alive", "dead"}
    leftover = {row.token for row in db_session.query(PushDevice).filter_by(user_id=user.id)}
    assert leftover == {"alive"}


def test_notify_skips_when_sender_disabled(db_session, monkeypatch):
    user = _user(db_session)
    register_push_device(db_session, user.id, token="t", platform="web", device_id="a")
    db_session.commit()
    monkeypatch.setattr("app.services.fcm.fcm_send_configured", lambda: False)

    def boom(*_a: object) -> str:
        raise AssertionError("should not send")

    monkeypatch.setattr("app.services.fcm.fcm_send_one", boom)
    assert notify_digest_sent(db_session, user.id, "t", "b") == 0


def test_unregister_removes_only_that_token(db_session):
    user = _user(db_session)
    register_push_device(db_session, user.id, token="keep", platform="web", device_id="a")
    register_push_device(db_session, user.id, token="drop", platform="web", device_id="b")
    unregister_push_device(db_session, user.id, token="drop")
    db_session.commit()
    tokens = {row.token for row in db_session.query(PushDevice).filter_by(user_id=user.id)}
    assert tokens == {"keep"}


def test_prune_invalid_tokens():
    assert prune_invalid_tokens(["a", "b"], ["ok", "gone"]) == ["b"]


def test_device_cap_drops_oldest(db_session):
    user = _user(db_session)
    for i in range(MAX_DEVICES_PER_USER + 3):
        register_push_device(db_session, user.id, token=f"tok-{i}", platform="web", device_id=f"d-{i}")
    db_session.commit()
    count = db_session.query(PushDevice).filter_by(user_id=user.id).count()
    assert count == MAX_DEVICES_PER_USER


async def test_deliver_digest_pushes_only_after_kakao_ok(db_session, monkeypatch):
    user = _user(db_session)
    digest = Digest(user_id=user.id, title="하루만장 · 8/16", body="본문", status="draft")
    db_session.add(digest)
    db_session.commit()
    pushed: list[tuple[int, str]] = []

    async def kakao_ok(*_a, **_k):
        return True, ""

    monkeypatch.setattr("app.services.delivery.send_digest_via_kakao", kakao_ok)
    monkeypatch.setattr(
        "app.services.delivery.notify_digest_sent",
        lambda db, user_id, title, body, **k: pushed.append((user_id, title)) or 1,
    )
    ok, err = await deliver_digest(db_session, user, digest, wait_ms=0)
    assert ok and err == ""
    assert pushed == [(user.id, "하루만장 · 8/16")]


async def test_deliver_digest_skips_push_when_kakao_fails(db_session, monkeypatch):
    user = _user(db_session)
    digest = Digest(user_id=user.id, title="하루만장", body="본문", status="draft")
    db_session.add(digest)
    db_session.commit()
    pushed: list[int] = []

    async def kakao_fail(*_a, **_k):
        return False, "kakao down"

    monkeypatch.setattr("app.services.delivery.send_digest_via_kakao", kakao_fail)
    monkeypatch.setattr(
        "app.services.delivery.notify_digest_sent",
        lambda *_a, **_k: pushed.append(1) or 0,
    )
    ok, err = await deliver_digest(db_session, user, digest, wait_ms=0)
    assert not ok
    assert "kakao" in err
    assert pushed == []
