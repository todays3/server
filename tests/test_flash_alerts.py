"""Fingerprint and Kakao body for phone-notification flash alerts."""

from app.services.flash_alerts import already_seen, clear_seen, format_flash_message, notification_fingerprint


def test_fingerprint_ignores_case_and_padding():
    a = notification_fingerprint(title="  속보 금리 ", text="연준")
    b = notification_fingerprint(title="속보 금리", text="연준")
    assert a == b
    assert a != notification_fingerprint(title="속보 금리", text="다른")


def test_format_flash_message_skips_duplicate_text():
    title, body = format_flash_message(app="매일경제", title="속보 원/달러", text="속보 원/달러")
    assert title == "속보 · 매일경제"
    assert "⚡ 매일경제" in body
    assert body.count("속보 원/달러") == 1


def test_flash_expired_fingerprint_is_not_treated_as_seen(monkeypatch):
    clear_seen()
    monkeypatch.setattr("app.services.flash_alerts.time.monotonic", lambda: 0)
    assert already_seen("fp") is False
    monkeypatch.setattr("app.services.flash_alerts.time.monotonic", lambda: 10**9)
    assert already_seen("fp") is False
    clear_seen()
