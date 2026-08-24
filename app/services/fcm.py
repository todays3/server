"""Firebase Cloud Messaging HTTP v1 — one user, many device tokens."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime, timezone

import httpx
from jose import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import PushDevice

MAX_DEVICES_PER_USER = 20
SendOne = Callable[[str, str, str], str]

UPDATE_PUSH_TITLE = "하루만장"
UPDATE_PUSH_BODY = "업데이트 소식이 있습니다"
UPDATE_PUSH_URL = "/app/notes"
DIGEST_PUSH_URL = "kakaotalk://"
OPEN_KAKAO_PATH = "/open-kakao.html"
UPDATE_NOTE_KIND = "update"
SUGGESTION_NOTE_KIND = "suggestion"
UPDATE_NOTE_NICKNAME = "하루만장"

_token_cache: tuple[str, float] | None = None


def is_external_app_url(url: str) -> bool:
    text = (url or "").strip()
    return text.startswith("kakaotalk:") or text.startswith("intent:")


def normalize_data_url(url: str) -> str:
    text = (url or "").strip() or "/app"
    if is_external_app_url(text) or text.startswith("http://") or text.startswith("https://"):
        return text
    return text if text.startswith("/") else f"/{text}"


def webpush_click_link(url: str, origin: str) -> str:
    """HTTPS link FCM may open. Custom schemes fall back to the in-app Kakao launch page."""
    data_url = normalize_data_url(url)
    base = origin.rstrip("/")
    if is_external_app_url(data_url):
        return f"{base}{OPEN_KAKAO_PATH}"
    if data_url.startswith("http://") or data_url.startswith("https://"):
        return data_url
    return f"{base}{data_url}"

FCM_SW_STUB = """/* todays3 fcm */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
"""


def fcm_send_configured() -> bool:
    return get_settings().firebase_send_configured


def fcm_web_configured() -> bool:
    return get_settings().firebase_web_configured


def public_web_config() -> dict[str, object]:
    settings = get_settings()
    return {
        "configured": settings.firebase_web_configured,
        "api_key": settings.firebase_web_api_key if settings.firebase_web_configured else "",
        "auth_domain": settings.firebase_auth_domain if settings.firebase_web_configured else "",
        "project_id": settings.firebase_project_id if settings.firebase_web_configured else "",
        "messaging_sender_id": settings.firebase_web_messaging_sender_id if settings.firebase_web_configured else "",
        "app_id": settings.firebase_web_app_id if settings.firebase_web_configured else "",
        "vapid_key": settings.firebase_web_vapid_key if settings.firebase_web_configured else "",
    }


def firebase_messaging_sw_source() -> str:
    cfg = public_web_config()
    if not cfg["configured"]:
        return FCM_SW_STUB
    firebase_cfg = {
        "apiKey": cfg["api_key"],
        "authDomain": cfg["auth_domain"],
        "projectId": cfg["project_id"],
        "messagingSenderId": cfg["messaging_sender_id"],
        "appId": cfg["app_id"],
    }
    return (
        "/* todays3 fcm */\n"
        "importScripts('https://www.gstatic.com/firebasejs/11.6.0/firebase-app-compat.js');\n"
        "importScripts('https://www.gstatic.com/firebasejs/11.6.0/firebase-messaging-compat.js');\n"
        f"firebase.initializeApp({json.dumps(firebase_cfg)});\n"
        "const messaging = firebase.messaging();\n"
        "messaging.onBackgroundMessage((payload) => {\n"
        "  const n = (payload && payload.notification) || {};\n"
        "  if (n.title || n.body) {\n"
        "    return;\n"
        "  }\n"
        "  const data = (payload && payload.data) || {};\n"
        "  return self.registration.showNotification(data.title || '하루만장', {\n"
        "    body: data.body || '카카오톡으로 하루만장을 보냈습니다',\n"
        "    icon: '/pwa-192x192.png',\n"
        "    tag: data.tag || 'todays3-digest',\n"
        "    data: data,\n"
        "  });\n"
        "});\n"
        f"const APP_ORIGIN = {json.dumps(get_settings().frontend_origin.rstrip('/'))};\n"
        "function clickTarget(url) {\n"
        "  if (!url) return APP_ORIGIN + '/app';\n"
        "  if (url.indexOf('kakaotalk:') === 0 || url.indexOf('intent:') === 0) {\n"
        f"    return APP_ORIGIN + {json.dumps(OPEN_KAKAO_PATH)};\n"
        "  }\n"
        "  if (url.indexOf('http://') === 0 || url.indexOf('https://') === 0) return url;\n"
        "  return APP_ORIGIN + (url.charAt(0) === '/' ? url : '/' + url);\n"
        "}\n"
        "function notificationUrl(data) {\n"
        "  if (!data) return '/app';\n"
        "  if (data.url) return data.url;\n"
        "  var nested = data.FCM_MSG && data.FCM_MSG.data;\n"
        "  if (nested && nested.url) return nested.url;\n"
        "  return '/app';\n"
        "}\n"
        "self.addEventListener('notificationclick', (event) => {\n"
        "  event.notification.close();\n"
        "  const url = notificationUrl(event.notification.data);\n"
        "  const target = clickTarget(url);\n"
        "  event.waitUntil((async () => {\n"
        "    const list = await clients.matchAll({ type: 'window', includeUncontrolled: true });\n"
        "    for (const client of list) {\n"
        "      try { client.postMessage({ type: 'todays3-open-kakao', url: target }); } catch (e) {}\n"
        "    }\n"
        "    if (list.length) {\n"
        "      const client = list[0];\n"
        "      if ('focus' in client) await client.focus();\n"
        "      if ('navigate' in client) {\n"
        "        try { await client.navigate(target); } catch (e) {}\n"
        "      }\n"
        "      return;\n"
        "    }\n"
        "    if (clients.openWindow) await clients.openWindow(target);\n"
        "  })());\n"
        "});\n"
        "self.addEventListener('install', () => self.skipWaiting());\n"
        "self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));\n"
    )


def push_body_preview(body: str) -> str:
    line = " ".join((body or "").split())
    if not line:
        return "카카오톡으로 하루만장을 보냈습니다"
    return line[:80]


def device_count(db: Session, user_id: int) -> int:
    return db.query(PushDevice).filter(PushDevice.user_id == user_id).count()


def total_device_count(db: Session) -> int:
    return db.query(PushDevice).count()


def register_push_device(
    db: Session,
    user_id: int,
    *,
    token: str,
    platform: str = "web",
    device_id: str = "",
) -> PushDevice:
    now = datetime.now(timezone.utc)
    token = (token or "").strip()
    device_id = (device_id or "").strip()[:80]
    if platform not in {"web", "android", "ios"}:
        platform = "web"
    row = db.scalar(select(PushDevice).where(PushDevice.token == token))
    if row is None and device_id:
        row = db.scalar(
            select(PushDevice).where(PushDevice.user_id == user_id, PushDevice.device_id == device_id)
        )
    if row is None:
        row = PushDevice(
            user_id=user_id,
            token=token,
            device_id=device_id,
            platform=platform,
            last_seen_at=now,
        )
        db.add(row)
    else:
        row.user_id = user_id
        row.token = token
        if device_id:
            row.device_id = device_id
        row.platform = platform
        row.last_seen_at = now
    db.flush()
    _enforce_cap(db, user_id)
    return row


def unregister_push_device(db: Session, user_id: int, *, token: str) -> None:
    row = db.scalar(select(PushDevice).where(PushDevice.user_id == user_id, PushDevice.token == token.strip()))
    if row is not None:
        db.delete(row)
        db.flush()


def _enforce_cap(db: Session, user_id: int) -> None:
    rows = db.scalars(
        select(PushDevice)
        .where(PushDevice.user_id == user_id)
        .order_by(PushDevice.last_seen_at.asc(), PushDevice.id.asc())
    ).all()
    extra = len(rows) - MAX_DEVICES_PER_USER
    if extra <= 0:
        return
    for row in rows[:extra]:
        db.delete(row)
    db.flush()


def prune_invalid_tokens(tokens: list[str], results: list[str]) -> list[str]:
    return [token for token, result in zip(tokens, results, strict=False) if result == "gone"]


def notify_digest_missed(
    db: Session,
    user_id: int,
    slot_label: str,
    *,
    send_one: SendOne | None = None,
) -> int:
    """Scheduled-send failure used to prompt in-app resend. Push is disabled."""
    _ = (db, user_id, slot_label, send_one)
    return 0


def notify_digest_sent(
    db: Session,
    user_id: int,
    title: str,
    body: str,
    *,
    send_one: SendOne | None = None,
) -> int:
    devices = db.scalars(select(PushDevice).where(PushDevice.user_id == user_id)).all()
    return _notify_devices(db, devices, title, body, url=DIGEST_PUSH_URL, tag="todays3-digest", send_one=send_one)


def notify_all_devices(
    db: Session,
    title: str,
    body: str,
    *,
    url: str = UPDATE_PUSH_URL,
    tag: str = "todays3-update",
    send_one: SendOne | None = None,
) -> int:
    devices = db.scalars(select(PushDevice)).all()
    return _notify_devices(db, devices, title, body, url=url, tag=tag, send_one=send_one)


def _notify_devices(
    db: Session,
    devices: list[PushDevice],
    title: str,
    body: str,
    *,
    url: str,
    tag: str,
    send_one: SendOne | None,
) -> int:
    if send_one is None and not fcm_send_configured():
        return 0
    sender = send_one or (lambda token, short_title, preview: fcm_send_one(token, short_title, preview, url=url, tag=tag))
    preview = push_body_preview(body)
    short_title = (title or "하루만장")[:80]
    sent = 0
    for device in devices:
        result = sender(device.token, short_title, preview)
        if result == "ok":
            sent += 1
        elif result == "gone":
            db.delete(device)
    db.flush()
    return sent


def fcm_web_message(
    token: str,
    title: str,
    body: str,
    *,
    url: str = "/app",
    tag: str = "todays3-digest",
) -> dict[str, object]:
    """Data-only web payload. A `notification` block makes Chrome display once and
    the service worker display again."""
    settings = get_settings()
    data_url = normalize_data_url(url)
    return {
        "message": {
            "token": token,
            "data": {"title": title, "body": body, "url": data_url, "tag": tag},
            "webpush": {
                "fcm_options": {"link": webpush_click_link(data_url, settings.frontend_origin)},
            },
        }
    }


def fcm_send_one(
    token: str,
    title: str,
    body: str,
    *,
    url: str = "/app",
    tag: str = "todays3-digest",
) -> str:
    if not fcm_send_configured():
        return "skip"
    settings = get_settings()
    try:
        access = _google_access_token()
        url_fcm = f"https://fcm.googleapis.com/v1/projects/{settings.firebase_project_id}/messages:send"
        payload = fcm_web_message(token, title, body, url=url, tag=tag)
        response = httpx.post(
            url_fcm,
            headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json"},
            json=payload,
            timeout=15.0,
        )
        if response.status_code == 200:
            return "ok"
        text = response.text or ""
        if response.status_code in {400, 404} and ("UNREGISTERED" in text or "NOT_FOUND" in text):
            return "gone"
        return "error"
    except Exception:
        return "error"


def probe_fcm_token(token: str) -> str:
    """Validate a device token without delivering a notification."""
    if not fcm_send_configured():
        return "skip"
    settings = get_settings()
    try:
        access = _google_access_token()
        url_fcm = f"https://fcm.googleapis.com/v1/projects/{settings.firebase_project_id}/messages:send"
        payload = {
            "validate_only": True,
            "message": fcm_web_message(token, "probe", "probe")["message"],
        }
        response = httpx.post(
            url_fcm,
            headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json"},
            json=payload,
            timeout=15.0,
        )
        if response.status_code == 200:
            return "ok"
        text = response.text or ""
        if response.status_code in {400, 404} and ("UNREGISTERED" in text or "NOT_FOUND" in text):
            return "gone"
        return "error"
    except Exception:
        return "error"


def _google_access_token() -> str:
    global _token_cache
    now = time.time()
    if _token_cache and _token_cache[1] > now + 60:
        return _token_cache[0]
    settings = get_settings()
    iat = int(now)
    assertion = jwt.encode(
        {
            "iss": settings.firebase_client_email,
            "sub": settings.firebase_client_email,
            "aud": "https://oauth2.googleapis.com/token",
            "iat": iat,
            "exp": iat + 3600,
            "scope": "https://www.googleapis.com/auth/firebase.messaging",
        },
        settings.firebase_private_key_pem,
        algorithm="RS256",
    )
    response = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        },
        timeout=15.0,
    )
    response.raise_for_status()
    access = str(response.json().get("access_token") or "")
    if not access:
        raise RuntimeError("empty google access token")
    _token_cache = (access, float(iat + 3500))
    return access
