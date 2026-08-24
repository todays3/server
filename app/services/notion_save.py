"""Optional Notion page create for Kakao 「N번 저장」 replies."""

from __future__ import annotations

import logging

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

NOTION_VERSION = "2022-06-28"


def save_action_to_notion(*, action_item: str, ticker: str = "", insight: str = "") -> bool:
    """POST a page to the configured Notion database. Returns False when unset or on error."""
    settings = get_settings()
    if not settings.notion_configured:
        log.info("notion skipped: NOTION_API_KEY / NOTION_DATABASE_ID unset")
        return False
    label = (action_item or insight or "하루만장 저장").strip()
    title = (f"[{ticker}] {label}" if ticker else label)[:100]
    payload = {
        "parent": {"database_id": settings.notion_database_id},
        "properties": {
            "Name": {"title": [{"text": {"content": title or "저장"}}]},
        },
    }
    headers = {
        "Authorization": f"Bearer {settings.notion_api_key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0, connect=2.0)) as client:
            res = client.post("https://api.notion.com/v1/pages", json=payload, headers=headers)
            res.raise_for_status()
        return True
    except httpx.HTTPError:
        log.exception("notion save failed")
        return False
