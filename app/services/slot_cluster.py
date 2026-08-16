"""Group nearby HH:MM send slots so one crawl can cover them."""

from __future__ import annotations

CLUSTER_WINDOW_MINUTES = 30


def slot_minutes(label: str) -> int:
    hour_s, minute_s = label.split(":", 1)
    return int(hour_s) * 60 + int(minute_s)


def cluster_slot_labels(
    labels: list[str],
    *,
    window_minutes: int = CLUSTER_WINDOW_MINUTES,
) -> list[list[str]]:
    uniq = sorted({label for label in labels if label}, key=slot_minutes)
    if not uniq:
        return []
    clusters: list[list[str]] = []
    start = uniq[0]
    current = [start]
    start_min = slot_minutes(start)
    window = max(0, int(window_minutes))
    for label in uniq[1:]:
        if slot_minutes(label) - start_min <= window:
            current.append(label)
            continue
        clusters.append(current)
        start = label
        start_min = slot_minutes(label)
        current = [label]
    clusters.append(current)
    return clusters


def cluster_key(day: str, start_label: str) -> str:
    return f"{day}|{start_label}"
