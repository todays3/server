from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.greeting import assistant_intro_line, digest_opening_line, greeting_line

SEOUL = ZoneInfo("Asia/Seoul")


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 16, hour, minute, tzinfo=SEOUL)


def test_evening_six_is_a_simple_hello():
    line = greeting_line("박준석", now=_at(18))
    assert "박준석님" in line
    assert "하루만장" not in line
    assert "입니다" not in line
    assert "요" in line
    assert "보고 싶" not in line
    assert "오랜만" not in line


def test_nineteen_mentions_dinner():
    line = greeting_line("박준석", now=_at(19))
    assert "박준석님" in line
    assert "저녁" in line
    assert "요" in line


def test_every_hour_has_a_short_line_with_name():
    for hour in range(24):
        line = greeting_line("민수", now=_at(hour))
        assert "민수님" in line
        assert 8 <= len(line) <= 48
        assert "하루만장" not in line
        assert "입니다" not in line
        assert "보고 싶" not in line
        assert "오랜만" not in line


def test_digest_opening_includes_assistant_intro_and_user_hello():
    line = digest_opening_line("박준석", "민준", now=_at(18))
    assert "민준" in line
    assert "박준석님" in line
    assert "브리프" not in line
    assert "입니다" not in line
    assert "보고 싶" not in line


def test_digest_opening_varies_intro_pattern():
    lines = {digest_opening_line("박준석", "서연", now=_at(h)) for h in range(24)}
    assert len(lines) > 3
    assert all("서연" in line for line in lines)
    assert all("브리프" not in line for line in lines)
    assert all("입니다" not in line for line in lines)
    assert all("보고 싶" not in line for line in lines)
    assert all("오랜만" not in line for line in lines)
    assert any("서연이에요" in line or "서연이 소식" in line for line in lines)


def test_assistant_intro_uses_yo_form():
    intro = assistant_intro_line("하람", now=_at(8))
    assert "하람" in intro
    assert "입니다" not in intro
    assert "하람요" not in intro
    assert intro.endswith("요.") or intro.endswith("요?") or intro.endswith("요")
