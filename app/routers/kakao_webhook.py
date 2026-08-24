"""Kakao skill webhook: finance query command and 「N번 저장」 Notion queue."""

from __future__ import annotations

import hmac
import logging
import re
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.db import SessionLocal, get_db
from app.deps.rate_limit import rate_limit_hooks
from app.models import KakaoAccount, User
from app.schemas import FinanceQueryIn
from app.services.dart_query import answer_finance_query
from app.services.dart_router import QueryRoute, route_finance_question
from app.services.finance_db import get_digest_action
from app.services.kakao import send_digest_via_kakao
from app.services.notion_save import save_action_to_notion

router = APIRouter(tags=["webhook"])
log = logging.getLogger(__name__)

SAVE_PATTERN = re.compile(r"(\d+)번 저장")
FINANCE_PATTERN = re.compile(r"!재무분석\s+(\S+)\s+(.+)")


def parse_save_index(text: str) -> int | None:
    match = SAVE_PATTERN.search(text or "")
    if match is None:
        return None
    return int(match.group(1))


def parse_finance_command(text: str) -> tuple[str, str] | None:
    match = FINANCE_PATTERN.search(text or "")
    if match is None:
        return None
    return match.group(1), match.group(2).strip()


def extract_utterance(payload: dict[str, Any]) -> tuple[str, str]:
    """Return (utterance, kakao_user_id) from Kakao skill JSON or a simple test body."""
    if not payload:
        return "", ""
    if isinstance(payload.get("userRequest"), dict):
        req = payload["userRequest"]
        utterance = str(req.get("utterance") or "")
        user = req.get("user") if isinstance(req.get("user"), dict) else {}
        kakao_id = str(user.get("id") or "")
        return utterance, kakao_id
    return str(payload.get("text") or payload.get("utterance") or ""), str(
        payload.get("kakao_id") or payload.get("user_id") or ""
    )


def _require_optional_secret(
    request: Request,
    x_webhook_secret: Annotated[str | None, Header()] = None,
) -> None:
    settings = get_settings()
    expected = (settings.kakao_webhook_secret or "").strip()
    if not expected:
        return
    provided = (x_webhook_secret or "").strip()
    auth = request.headers.get("authorization") or ""
    if not provided and auth.lower().startswith("bearer "):
        provided = auth[7:].strip()
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="웹훅 시크릿이 올바르지 않습니다")


def _skill_text(message: str) -> dict[str, Any]:
    return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": message}}]}}


def _user_by_kakao(db: Session, kakao_id: str) -> User | None:
    if not kakao_id:
        return None
    account = db.scalar(select(KakaoAccount).options(joinedload(KakaoAccount.user)).where(KakaoAccount.kakao_id == kakao_id))
    if account is not None:
        return account.user
    if kakao_id.isdigit():
        return db.get(User, int(kakao_id))
    return None


async def _deliver_finance_analysis(user_id: int, ticker: str, question: str) -> None:
    try:
        result = await answer_finance_query(
            FinanceQueryIn(ticker=ticker, question=question), user_id=user_id
        )
        db = SessionLocal()
        try:
            user = db.get(User, user_id)
            if user is None:
                return
            await send_digest_via_kakao(user, f"재무분석 {ticker}", result.answer, db=db)
        finally:
            db.close()
    except Exception:
        log.exception("kakao finance analysis delivery failed")


@router.post("/webhook/kakao")
async def kakao_save_webhook(
    payload: dict[str, Any],
    background: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[None, Depends(rate_limit_hooks)],
    __: Annotated[None, Depends(_require_optional_secret)],
) -> dict[str, Any]:
    utterance, kakao_id = extract_utterance(payload)
    finance = parse_finance_command(utterance)
    if finance is not None:
        ticker, question = finance
        user = _user_by_kakao(db, kakao_id)
        if user is None:
            return _skill_text("연결된 계정을 찾지 못했습니다.")
        routed = route_finance_question(question)
        if routed.route == QueryRoute.ANALYSIS:
            background.add_task(_deliver_finance_analysis, user.id, ticker, question)
            return _skill_text("분석을 시작했습니다. 결과는 카카오 메모로 보내 드립니다.")
        result = await answer_finance_query(
            FinanceQueryIn(ticker=ticker, question=question), user_id=user.id
        )
        return _skill_text(result.answer)

    index = parse_save_index(utterance)
    if index is None:
        return _skill_text("저장 번호가 없습니다. 「1번 저장」처럼 보내 주세요.")
    user = _user_by_kakao(db, kakao_id)
    if user is None:
        return _skill_text("연결된 계정을 찾지 못했습니다.")
    row = get_digest_action(user.id, index)
    if row is None:
        return _skill_text(f"{index}번 실행 포인트가 없습니다.")
    background.add_task(
        save_action_to_notion,
        action_item=row["action_item"],
        ticker=row["ticker"],
        insight=row["insight"],
    )
    return _skill_text(f"{index}번을 저장 큐에 넣었습니다.")
