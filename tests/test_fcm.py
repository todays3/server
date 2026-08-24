"""FCM device registry: one Kakao user, many device tokens."""

from app.models import Digest, PushDevice, User
from app.services.delivery import deliver_digest
from app.services.fcm import (
    DIGEST_PUSH_URL,
    MAX_DEVICES_PER_USER,
    OPEN_KAKAO_PATH,
    UPDATE_PUSH_BODY,
    UPDATE_PUSH_TITLE,
    UPDATE_PUSH_URL,
    fcm_web_message,
    firebase_messaging_sw_source,
    notify_all_devices,
    notify_digest_missed,
    notify_digest_sent,
    prune_invalid_tokens,
    register_push_device,
    unregister_push_device,
    webpush_click_link,
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
    assert message["data"]["tag"] == "todays3-digest"


def test_digest_push_click_opens_kakaotalk_with_https_fallback():
    assert DIGEST_PUSH_URL == "kakaotalk://"
    assert webpush_click_link(DIGEST_PUSH_URL, "https://oday.example") == "https://oday.example/open-kakao.html"
    payload = fcm_web_message(
        "device-token",
        "하루만장 · 8/16",
        "미리보기",
        url=DIGEST_PUSH_URL,
        tag="todays3-digest",
    )
    data = payload["message"]["data"]
    assert data["url"] == "kakaotalk://"
    assert payload["message"]["webpush"]["fcm_options"]["link"].endswith(OPEN_KAKAO_PATH)
    assert OPEN_KAKAO_PATH == "/open-kakao.html"


def test_notify_digest_sent_targets_kakaotalk(db_session, monkeypatch):
    user = _user(db_session)
    register_push_device(db_session, user.id, token="phone", platform="web", device_id="phone")
    db_session.commit()
    seen: dict[str, str] = {}

    def fake_send(token: str, title: str, body: str, *, url: str = "/app", tag: str = "todays3-digest") -> str:
        _ = (token, title, body)
        seen["url"] = url
        seen["tag"] = tag
        return "ok"

    monkeypatch.setattr("app.services.fcm.fcm_send_configured", lambda: True)
    monkeypatch.setattr("app.services.fcm.fcm_send_one", fake_send)
    notify_digest_sent(db_session, user.id, "하루만장", "본문")
    assert seen["url"] == "kakaotalk://"
    assert seen["tag"] == "todays3-digest"


def test_missed_digest_push_is_not_sent(db_session, monkeypatch):
    user = _user(db_session)
    register_push_device(db_session, user.id, token="phone", platform="web", device_id="phone")
    db_session.commit()
    sent: list[str] = []

    def fake_send(token: str, title: str, body: str, *, url: str = "/app", tag: str = "todays3-digest") -> str:
        sent.append(tag)
        _ = (token, title, body, url)
        return "ok"

    monkeypatch.setattr("app.services.fcm.fcm_send_configured", lambda: True)
    monkeypatch.setattr("app.services.fcm.fcm_send_one", fake_send)
    assert notify_digest_missed(db_session, user.id, "07:30") == 0
    assert sent == []


def test_fcm_web_message_can_open_notes_for_update_news():
    payload = fcm_web_message(
        "device-token",
        UPDATE_PUSH_TITLE,
        UPDATE_PUSH_BODY,
        url=UPDATE_PUSH_URL,
        tag="todays3-update",
    )
    data = payload["message"]["data"]
    assert data["title"] == "하루만장"
    assert data["body"] == "업데이트 소식이 있습니다"
    assert "홈화면" not in data["body"]
    assert data["url"] == "/app/notes"
    assert data["tag"] == "todays3-update"
    webpush = payload["message"]["webpush"]
    assert webpush["fcm_options"]["link"].endswith("/app/notes")


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
    assert "data.tag" in src
    assert "kakaotalk:" in src
    assert "/open-kakao.html" in src
    assert "kakaotalk:') === 0 || url.indexOf('intent:') === 0) return url" not in src
    assert "includeUncontrolled" in src
    assert "todays3-open-kakao" in src
    assert "postMessage" in src
    assert "client.navigate" in src
    assert "clients.openWindow" in src
    assert "notificationUrl" in src
    assert "/app/notes" in src or "clickTarget" in src
    assert "APP_ORIGIN" in src


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


def test_notify_all_devices_reaches_every_user_and_drops_gone(db_session):
    a = _user(db_session, "a@example.com")
    b = _user(db_session, "b@example.com")
    register_push_device(db_session, a.id, token="alive-a", platform="web", device_id="a1")
    register_push_device(db_session, a.id, token="dead-a", platform="web", device_id="a2")
    register_push_device(db_session, b.id, token="alive-b", platform="web", device_id="b1")
    db_session.commit()
    sent: list[tuple[str, str, str]] = []

    def send_one(token: str, title: str, body: str) -> str:
        sent.append((token, title, body))
        return "gone" if token.startswith("dead") else "ok"

    n = notify_all_devices(
        db_session,
        UPDATE_PUSH_TITLE,
        UPDATE_PUSH_BODY,
        url=UPDATE_PUSH_URL,
        send_one=send_one,
    )
    assert n == 2
    assert {row[0] for row in sent} == {"alive-a", "dead-a", "alive-b"}
    assert all(row[1] == "하루만장" and row[2] == "업데이트 소식이 있습니다" for row in sent)
    leftover = {row.token for row in db_session.query(PushDevice).all()}
    assert leftover == {"alive-a", "alive-b"}


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
        return True, "", 1

    monkeypatch.setattr("app.services.delivery.send_digest_via_kakao", kakao_ok)
    monkeypatch.setattr(
        "app.services.delivery.notify_digest_sent",
        lambda db, user_id, title, body, **k: pushed.append((user_id, title)) or 1,
    )
    result = await deliver_digest(db_session, user, digest, wait_ms=0)
    assert result.ok and result.error == ""
    assert pushed == [(user.id, "하루만장 · 8/16")]


async def test_deliver_digest_skips_push_when_kakao_fails(db_session, monkeypatch):
    user = _user(db_session)
    digest = Digest(user_id=user.id, title="하루만장", body="본문", status="draft")
    db_session.add(digest)
    db_session.commit()
    pushed: list[int] = []

    async def kakao_fail(*_a, **_k):
        return False, "kakao down", 0

    monkeypatch.setattr("app.services.delivery.send_digest_via_kakao", kakao_fail)
    monkeypatch.setattr(
        "app.services.delivery.notify_digest_sent",
        lambda *_a, **_k: pushed.append(1) or 0,
    )
    result = await deliver_digest(db_session, user, digest, wait_ms=0)
    assert not result.ok
    assert "kakao" in result.error
    assert pushed == []


def test_probe_fcm_token_skips_when_not_configured(monkeypatch):
    monkeypatch.setattr("app.services.fcm.fcm_send_configured", lambda: False)
    from app.services.fcm import probe_fcm_token

    assert probe_fcm_token("device-token") == "skip"


def test_probe_fcm_token_returns_gone_for_unregistered(monkeypatch):
    monkeypatch.setattr("app.services.fcm.fcm_send_configured", lambda: True)
    monkeypatch.setattr("app.services.fcm._google_access_token", lambda: "access")
    monkeypatch.setattr(
        "app.services.fcm.get_settings",
        lambda: type(
            "S",
            (),
            {"firebase_project_id": "demo-proj", "frontend_origin": "https://example.com"},
        )(),
    )

    class FakeResponse:
        status_code = 404
        text = '{"error":{"status":"NOT_FOUND","message":"Requested entity was not found."}}'

    monkeypatch.setattr("app.services.fcm.httpx.post", lambda *args, **kwargs: FakeResponse())
    from app.services.fcm import probe_fcm_token

    assert probe_fcm_token("gone-token") == "gone"
