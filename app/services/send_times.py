"""Helpers for multi-slot Seoul send times stored as 'HH:MM,HH:MM'."""

from __future__ import annotations

from app.schemas import SendTimeSlot

DEFAULT_SEND_TIMES = "07:30"
MAX_SEND_TIMES = 3


def format_hm(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def parse_send_times_raw(raw: str | None, *, hour: int = 7, minute: int = 30) -> list[SendTimeSlot]:
    text = (raw or "").strip()
    slots: list[SendTimeSlot] = []
    if text:
        for part in text.split(","):
            piece = part.strip()
            if not piece or ":" not in piece:
                continue
            h_s, m_s = piece.split(":", 1)
            try:
                h, m = int(h_s), int(m_s)
            except ValueError:
                continue
            if 0 <= h <= 23 and 0 <= m <= 59:
                slots.append(SendTimeSlot(hour=h, minute=m))
    if not slots:
        slots = [SendTimeSlot(hour=hour, minute=minute)]
    return normalize_slots(slots)


def normalize_slots(slots: list[SendTimeSlot]) -> list[SendTimeSlot]:
    seen: set[tuple[int, int]] = set()
    out: list[SendTimeSlot] = []
    for slot in sorted(slots, key=lambda s: (s.hour, s.minute)):
        key = (slot.hour, slot.minute)
        if key in seen:
            continue
        seen.add(key)
        out.append(SendTimeSlot(hour=slot.hour, minute=slot.minute))
        if len(out) >= MAX_SEND_TIMES:
            break
    if not out:
        out = [SendTimeSlot(hour=7, minute=30)]
    return out


def encode_send_times(slots: list[SendTimeSlot]) -> str:
    normalized = normalize_slots(slots)
    return ",".join(format_hm(s.hour, s.minute) for s in normalized)


def slot_set(slots: list[SendTimeSlot]) -> set[tuple[int, int]]:
    return {(s.hour, s.minute) for s in normalize_slots(slots)}
