from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.greeting import greeting_line

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
