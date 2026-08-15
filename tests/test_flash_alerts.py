"""Fingerprint and Kakao body for phone-notification flash alerts."""

from app.services.flash_alerts import format_flash_message, notification_fingerprint


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
