"""Process-local registry of in-flight pipeline work for the admin live panel."""

from __future__ import annotations

import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator


KIND_LABELS = {
    "shared_crawl": "공유 수집",
    "prepare": "예약 작성",
    "send": "예약 전송",
    "admin_preview": "어드민 E2E",
    "user_send": "지금 보내기",
}

PHASE_LABELS = {
    "queued": "대기",
    "trigger": "트리거",
    "crawl": "수집",
    "preprocess": "전처리",
    "curate": "AI 큐레이션",
    "format": "본문 포맷",
    "enrich": "에이전트 보강",
    "wait": "슬롯 대기",
    "send": "카카오 전송",
    "done": "완료",
}


@dataclass
class LiveActivity:
    id: str
    kind: str
    phase: str
    label: str
    detail: str = ""
    user_id: int | None = None
    display_name: str = ""
    slot_label: str = ""
    cluster_key: str = ""
    started_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        elapsed_ms = max(0, int((time.time() - self.started_at) * 1000))
        return {
            "id": self.id,
            "kind": self.kind,
            "kind_label": KIND_LABELS.get(self.kind, self.kind),
            "phase": self.phase,
            "phase_label": PHASE_LABELS.get(self.phase, self.phase),
            "label": self.label,
            "detail": self.detail,
            "user_id": self.user_id,
            "display_name": self.display_name,
            "slot_label": self.slot_label,
            "cluster_key": self.cluster_key,
            "started_at": self.started_at,
            "elapsed_ms": elapsed_ms,
        }


_lock = threading.Lock()
_activities: dict[str, LiveActivity] = {}


def clear_live_activities() -> None:
    with _lock:
        _activities.clear()


def start_activity(
    *,
    kind: str,
    label: str,
    phase: str = "queued",
    detail: str = "",
    user_id: int | None = None,
    display_name: str = "",
    slot_label: str = "",
    cluster_key: str = "",
    activity_id: str | None = None,
) -> str:
    aid = activity_id or uuid.uuid4().hex
    row = LiveActivity(
        id=aid,
        kind=kind,
        phase=phase,
        label=label,
        detail=detail,
        user_id=user_id,
        display_name=display_name,
        slot_label=slot_label,
        cluster_key=cluster_key,
    )
    with _lock:
        _activities[aid] = row
    return aid


def update_activity(
    activity_id: str,
    *,
    phase: str | None = None,
    detail: str | None = None,
    label: str | None = None,
    display_name: str | None = None,
) -> None:
    with _lock:
        row = _activities.get(activity_id)
        if row is None:
            return
        if phase is not None:
            row.phase = phase
        if detail is not None:
            row.detail = detail
        if label is not None:
            row.label = label
        if display_name is not None:
            row.display_name = display_name


def end_activity(activity_id: str) -> None:
    with _lock:
        _activities.pop(activity_id, None)


@contextmanager
def track_activity(
    *,
    kind: str,
    label: str,
    phase: str = "queued",
    detail: str = "",
    user_id: int | None = None,
    display_name: str = "",
    slot_label: str = "",
    cluster_key: str = "",
) -> Iterator[str]:
    aid = start_activity(
        kind=kind,
        label=label,
        phase=phase,
        detail=detail,
        user_id=user_id,
        display_name=display_name,
        slot_label=slot_label,
        cluster_key=cluster_key,
    )
    try:
        yield aid
    finally:
        end_activity(aid)


def snapshot() -> dict:
    with _lock:
        items = [row.to_dict() for row in _activities.values()]
    items.sort(key=lambda row: row["started_at"])
    by_kind: dict[str, int] = {}
    by_phase: dict[str, int] = {}
    for row in items:
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
        by_phase[row["phase"]] = by_phase.get(row["phase"], 0) + 1
    user_ids = {row["user_id"] for row in items if row.get("user_id") is not None}
    return {
        "active_count": len(items),
        "user_count": len(user_ids),
        "by_kind": by_kind,
        "by_phase": by_phase,
        "items": items,
        "summary": _summary_line(len(items), len(user_ids), by_kind),
        "server_time": time.time(),
    }


def _summary_line(active: int, users: int, by_kind: dict[str, int]) -> str:
    if active == 0:
        return "지금 돌아가는 처리 없음"
    parts: list[str] = []
    if by_kind.get("shared_crawl"):
        parts.append(f"공유수집 {by_kind['shared_crawl']}")
    if by_kind.get("prepare"):
        parts.append(f"작성 {by_kind['prepare']}")
    if by_kind.get("send"):
        parts.append(f"전송 {by_kind['send']}")
    if by_kind.get("admin_preview"):
        parts.append(f"E2E {by_kind['admin_preview']}")
    if by_kind.get("user_send"):
        parts.append(f"지금보내기 {by_kind['user_send']}")
    detail = " · ".join(parts) if parts else f"{active}건"
    if users:
        return f"처리 중 {active}건 · 대상 {users}명 · {detail}"
    return f"처리 중 {active}건 · {detail}"
