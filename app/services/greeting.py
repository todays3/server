"""Time-of-day hello at the top of the Kakao digest body."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

SEOUL = ZoneInfo("Asia/Seoul")

# hour → short check-ins. {name} becomes "박준석님".
_BY_HOUR: dict[int, tuple[str, ...]] = {
    0: ("아직 안 주무셨어요, {name}님?", "고요한 밤이에요, {name}님."),
    1: ("늦은 밤이에요, {name}님.", "편히 쉬어요, {name}님."),
    2: ("깊은 밤이에요, {name}님.", "무리하지 말아요, {name}님."),
    3: ("새벽이네요, {name}님.", "해가 뜨기 전이에요, {name}님."),
    4: ("이른 새벽이에요, {name}님.", "조용한 시간이에요, {name}님."),
    5: ("좋은 새벽이에요, {name}님.", "하루가 시작됐어요, {name}님."),
    6: ("좋은 아침이에요, {name}님.", "상쾌한 아침이에요, {name}님."),
    7: ("출근길은 괜찮아요, {name}님?", "아침 잘 열고 있어요, {name}님?"),
    8: ("커피는 마셨어요, {name}님?", "오전 잘 보내고 있어요, {name}님?"),
    9: ("좋은 오전이에요, {name}님.", "오전 파이팅이에요, {name}님."),
    10: ("집중 잘 되고 있어요, {name}님?", "오전 중반이에요, {name}님."),
    11: ("점심이 가까워져요, {name}님.", "오전 마무리 잘해요, {name}님."),
    12: ("점심은 먹었어요, {name}님?", "맛있는 점심 먹어요, {name}님."),
    13: ("식후예요, {name}님.", "점심 잘 먹었어요, {name}님?"),
    14: ("나른한 오후예요, {name}님.", "오후도 화이팅이에요, {name}님."),
    15: ("오후 잘 가고 있어요, {name}님?", "잠깐 숨 골랐어요, {name}님?"),
    16: ("오후가 깊어져요, {name}님.", "오늘 하루 어땠어요, {name}님?"),
    17: ("퇴근 시간이에요, {name}님.", "저녁이 다가와요, {name}님."),
    18: ("좋은 저녁이에요, {name}님.", "오늘 수고했어요, {name}님."),
    19: ("저녁은 먹었어요, {name}님?", "저녁 시간이에요, {name}님."),
    20: ("편안한 밤 보내요, {name}님.", "저녁 잘 보내고 있어요, {name}님?"),
    21: ("좋은 밤이에요, {name}님.", "오늘은 이만 쉬어요, {name}님."),
    22: ("밤 공기가 좋아요, {name}님.", "내일도 응원해요, {name}님."),
    23: ("오늘도 고생했어요, {name}님.", "이제 잘 시간이에요, {name}님."),
}


def greeting_line(name: str, *, now: datetime) -> str:
    local = now.astimezone(SEOUL) if now.tzinfo else now.replace(tzinfo=SEOUL)
    hour = local.hour
    options = _BY_HOUR[hour]
    label = (name or "당신").strip() or "당신"
    pick = (local.timetuple().tm_yday + hour + len(label)) % len(options)
    return options[pick].format(name=label)


_INTRO_PATTERNS: tuple[str, ...] = (
    "{assistant}요.",
    "저 {assistant}요.",
    "{assistant} 왔어요.",
    "또 왔네요, {assistant}요.",
    "{assistant}요, 잘 지내요?",
    "오랜만이에요. {assistant} 왔어요.",
    "저예요. {assistant} 왔어요.",
    "{assistant}요. 소식 가져왔어요.",
    "보고 싶었어요. {assistant} 왔어요.",
)


def assistant_intro_line(assistant_name: str, *, now: datetime) -> str:
    assistant = (assistant_name or "하루").strip() or "하루"
    local = now.astimezone(SEOUL) if now.tzinfo else now.replace(tzinfo=SEOUL)
    pick = (local.timetuple().tm_yday + local.hour + len(assistant)) % len(_INTRO_PATTERNS)
    return _INTRO_PATTERNS[pick].format(assistant=assistant)


def digest_opening_line(user_name: str, assistant_name: str, *, now: datetime) -> str:
    """Friendly 요-form hello from the assistant plus a time-of-day check-in."""
    intro = assistant_intro_line(assistant_name, now=now)
    user_line = greeting_line(user_name, now=now)
    return f"{intro} {user_line}"
