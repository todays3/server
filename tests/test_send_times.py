from app.schemas import SendTimeSlot
from app.services.send_times import encode_send_times, normalize_slots, parse_send_times_raw


def test_parse_falls_back_to_hour_minute():
    slots = parse_send_times_raw("", hour=8, minute=15)
    assert slots == [SendTimeSlot(hour=8, minute=15)]


def test_normalize_sorts_and_dedupes():
    slots = normalize_slots(
        [
            SendTimeSlot(hour=12, minute=0),
            SendTimeSlot(hour=7, minute=30),
            SendTimeSlot(hour=7, minute=30),
            SendTimeSlot(hour=18, minute=0),
        ]
    )
    assert [encode_send_times([s]) for s in slots] == ["07:30", "12:00", "18:00"]
    assert encode_send_times(slots) == "07:30,12:00,18:00"
