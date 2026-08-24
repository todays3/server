"""DART financial query API. Independent of the morning digest pipeline."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.auth import get_current_user
from app.deps.rate_limit import rate_limit_hooks
from app.models import User
from app.schemas import FinanceQueryIn, FinanceQueryOut
from app.services.dart_query import answer_finance_query

router = APIRouter(prefix="/finance", tags=["finance"])


@router.post("/query", response_model=FinanceQueryOut)
async def finance_query(
    payload: FinanceQueryIn,
    _user: Annotated[User, Depends(get_current_user)],
    _: Annotated[None, Depends(rate_limit_hooks)],
) -> FinanceQueryOut:
    return await answer_finance_query(payload, user_id=_user.id)
