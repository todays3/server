"""Time-of-day hello at the top of the Kakao digest body."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

SEOUL = ZoneInfo("Asia/Seoul")

# Natural Kakao 해요체 — short, time-aware, no translationese / clingy "missed you" tone.
_BY_HOUR: dict[int, tuple[str, ...]] = {
    0: ("{name}님, 아직 안 주무시네요.", "{name}님, 밤이 깊었어요."),
    1: ("{name}님, 늦은 밤이에요.", "{name}님, 편히 쉬세요."),
    2: ("{name}님, 깊은 밤이네요.", "{name}님, 무리하지 마세요."),
    3: ("{name}님, 새벽이에요.", "{name}님, 해가 뜨기 전이네요."),
    4: ("{name}님, 이른 새벽이에요.", "{name}님, 조용한 시간이네요."),
    5: ("{name}님, 좋은 아침이에요.", "{name}님 안녕하세요. 이른 아침이에요."),
    6: ("{name}님, 좋은 아침이에요.", "{name}님 안녕하세요."),
    7: ("{name}님, 좋은 아침이에요.", "{name}님, 출근길 조심하세요."),
    8: ("{name}님, 좋은 아침이에요.", "{name}님 안녕하세요."),
    9: ("{name}님 안녕하세요.", "{name}님, 오전에도 화이팅이에요."),
    10: ("{name}님 안녕하세요.", "{name}님, 오전 잘 보내고 계세요?"),
    11: ("{name}님 안녕하세요.", "{name}님, 곧 점심이겠어요."),
    12: ("{name}님, 점심 맛있게 드세요.", "{name}님, 점심은 드셨어요?"),
    13: ("{name}님, 점심은 드셨어요?", "{name}님 안녕하세요."),
    14: ("{name}님 안녕하세요.", "{name}님, 오후도 잘 보내고 계세요?"),
    15: ("{name}님 안녕하세요.", "{name}님, 잠깐 쉬고 계세요?"),
    16: ("{name}님 안녕하세요.", "{name}님, 오늘 하루 어땠어요?"),
    17: ("{name}님, 퇴근하실 시간이네요.", "{name}님, 오늘도 수고하셨어요."),
    18: ("{name}님, 좋은 저녁이에요.", "{name}님, 오늘도 수고하셨어요."),
    19: ("{name}님, 저녁은 드셨어요?", "{name}님, 좋은 저녁이에요."),
    20: ("{name}님, 편안한 저녁 되세요.", "{name}님 안녕하세요."),
    21: ("{name}님, 좋은 밤 되세요.", "{name}님, 오늘 하루도 고생하셨어요."),
    22: ("{name}님, 좋은 밤 되세요.", "{name}님, 내일도 응원할게요."),
    23: ("{name}님, 오늘도 고생하셨어요.", "{name}님, 이제 푹 쉬세요."),
}


def greeting_line(name: str, *, now: datetime) -> str:
    local = now.astimezone(SEOUL) if now.tzinfo else now.replace(tzinfo=SEOUL)
    hour = local.hour
    options = _BY_HOUR[hour]
    label = (name or "당신").strip() or "당신"
    pick = (local.timetuple().tm_yday + hour + len(label)) % len(options)
    return options[pick].format(name=label)


# Assistant self-intro — like a brief Kakao ping, not a reunion.
_INTRO_PATTERNS: tuple[str, ...] = (
    "{assistant}이에요.",
    "저 {assistant}이에요.",
    "안녕하세요, {assistant}이에요.",
    "{assistant}이에요. 오늘 소식 정리했어요.",
    "{assistant}이 소식 들고 왔어요.",
    "저예요. {assistant}이에요.",
)


def assistant_intro_line(assistant_name: str, *, now: datetime) -> str:
    assistant = (assistant_name or "하루").strip() or "하루"
    local = now.astimezone(SEOUL) if now.tzinfo else now.replace(tzinfo=SEOUL)
    pick = (local.timetuple().tm_yday + local.hour + len(assistant)) % len(_INTRO_PATTERNS)
    return _INTRO_PATTERNS[pick].format(assistant=assistant)


def digest_opening_line(user_name: str, assistant_name: str, *, now: datetime) -> str:
    """Korean Kakao-style hello: assistant intro + time-of-day check-in."""
    intro = assistant_intro_line(assistant_name, now=now)
    user_line = greeting_line(user_name, now=now)
    return f"{intro} {user_line}"
