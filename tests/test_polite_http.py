"""Classify whether a fetch looks like a bot block (no stealth evasion)."""

from app.services.polite_http import classify_bot_risk


def test_classify_rss_with_captcha_word_is_still_clear():
    risk, _ = classify_bot_risk(
        status_code=200,
        body="<?xml version='1.0'?><rss><item><title>New captcha bypass research</title></item></rss>",
        error="",
        robots_allowed=True,
    )
    assert risk == "clear"
    risk, signal = classify_bot_risk(
        status_code=200,
        body="<?xml version='1.0'?><rss></rss>",
        error="",
        robots_allowed=True,
    )
    assert risk == "clear"
    assert "챌린지" not in signal
    assert "429" not in signal


def test_classify_blocked_on_403_and_429():
    risk, _ = classify_bot_risk(status_code=403, body="", error="HTTP 403", robots_allowed=True)
    assert risk == "blocked"
    risk, signal = classify_bot_risk(status_code=429, body="", error="HTTP 429", robots_allowed=True)
    assert risk == "blocked"
    assert "429" in signal


def test_classify_blocked_on_challenge_html():
    risk, signal = classify_bot_risk(
        status_code=200,
        body="<html>Just a moment... cdn-cgi/challenge</html>",
        error="",
        robots_allowed=True,
    )
    assert risk == "blocked"
    assert "챌린지" in signal


def test_classify_blocked_when_robots_disallow():
    risk, signal = classify_bot_risk(status_code=None, body="", error="", robots_allowed=False)
    assert risk == "blocked"
    assert "robots" in signal.lower()
