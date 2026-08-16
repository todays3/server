"""Time-of-day hello at the top of the Kakao digest body."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

SEOUL = ZoneInfo("Asia/Seoul")

# hour → short check-ins. {name} becomes "박준석님".
_BY_HOUR: dict[int, tuple[str, ...]] = {
    0: ("아직 안 주무셨군요, {name}님.", "고요한 밤입니다, {name}님."),
    1: ("늦은 밤입니다, {name}님.", "편히 쉬세요, {name}님."),
    2: ("깊은 밤입니다, {name}님.", "무리하지 마세요, {name}님."),
    3: ("새벽이네요, {name}님.", "해가 뜨기 전입니다, {name}님."),
    4: ("이른 새벽입니다, {name}님.", "조용한 시간입니다, {name}님."),
    5: ("좋은 새벽입니다, {name}님.", "하루가 시작됐습니다, {name}님."),
    6: ("좋은 아침입니다, {name}님.", "상쾌한 아침이에요, {name}님."),
    7: ("출근길은 괜찮으신가요, {name}님?", "아침 잘 열고 계신가요, {name}님?"),
    8: ("커피는 드셨나요, {name}님?", "오전 잘 보내고 계신가요, {name}님?"),
    9: ("좋은 오전입니다, {name}님.", "오전 업무 파이팅입니다, {name}님."),
    10: ("집중 잘 되고 계신가요, {name}님?", "오전 중반입니다, {name}님."),
    11: ("점심이 가까워집니다, {name}님.", "오전 마무리 잘하세요, {name}님."),
    12: ("점심식사는 하셨나요, {name}님?", "맛있는 점심 되세요, {name}님."),
    13: ("식후입니다, {name}님.", "점심 잘 드셨나요, {name}님?"),
    14: ("나른한 오후입니다, {name}님.", "오후도 화이팅입니다, {name}님."),
    15: ("오후 잘 가고 계신가요, {name}님?", "잠깐 숨 고르셨나요, {name}님?"),
    16: ("오후가 깊어집니다, {name}님.", "오늘 하루 어땠나요, {name}님?"),
    17: ("퇴근 시간입니다, {name}님.", "저녁이 다가옵니다, {name}님."),
    18: ("좋은 저녁입니다, {name}님.", "하루 수고하셨습니다, {name}님."),
    19: ("저녁식사는 하셨나요, {name}님?", "저녁 시간입니다, {name}님."),
    20: ("편안한 밤 되세요, {name}님.", "저녁 잘 보내고 계신가요, {name}님?"),
    21: ("좋은 밤입니다, {name}님.", "오늘은 이만 쉬어가세요, {name}님."),
    22: ("밤 공기가 좋습니다, {name}님.", "내일도 응원합니다, {name}님."),
    23: ("오늘도 고생하셨어요, {name}님.", "이제 잘 시간입니다, {name}님."),
}


def greeting_line(name: str, *, now: datetime) -> str:
    local = now.astimezone(SEOUL) if now.tzinfo else now.replace(tzinfo=SEOUL)
    hour = local.hour
    options = _BY_HOUR[hour]
    label = (name or "당신").strip() or "당신"
    pick = (local.timetuple().tm_yday + hour + len(label)) % len(options)
    return options[pick].format(name=label)
