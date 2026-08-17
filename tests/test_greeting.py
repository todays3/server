from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.greeting import digest_opening_line, greeting_line

SEOUL = ZoneInfo("Asia/Seoul")


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 16, hour, minute, tzinfo=SEOUL)


def test_evening_six_is_a_simple_hello():
    line = greeting_line("박준석", now=_at(18))
    assert line.endswith("박준석님.") or line.endswith("박준석님?")
    assert "하루만장" not in line
    assert line in {
        "좋은 저녁입니다, 박준석님.",
        "하루 수고하셨습니다, 박준석님.",
    }


def test_nineteen_mentions_dinner():
    line = greeting_line("박준석", now=_at(19))
    assert "박준석님" in line
    assert line in {
        "저녁식사는 하셨나요, 박준석님?",
        "저녁 시간입니다, 박준석님.",
    }


def test_every_hour_has_a_short_line_with_name():
    for hour in range(24):
        line = greeting_line("민수", now=_at(hour))
        assert "민수님" in line
        assert 8 <= len(line) <= 40
        assert "하루만장" not in line


def test_digest_opening_includes_assistant_intro_and_user_hello():
    line = digest_opening_line("박준석", "민준", now=_at(18))
    assert "민준" in line
    assert "박준석님" in line
    assert line.endswith("박준석님.") or line.endswith("박준석님?")


def test_digest_opening_varies_intro_pattern():
    lines = {digest_opening_line("박준석", "서연", now=_at(h)) for h in range(24)}
    assert any("서연입니다." in line for line in lines)
    assert any("브리프" in line for line in lines)
