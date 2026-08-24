from typing import Annotated
import asyncio
import json
import queue

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user
from app.db import get_db
from app.models import Digest, User
from app.schemas import DigestItemOut, DigestOut, PreviewRequest
from app.services.delivery import deliver_digest
from app.services.digest import create_digest, create_test_digest
from app.routers.prefs import _ensure_pref
from app.services.live_activity import end_activity, start_activity, update_activity

router = APIRouter(prefix="/digests", tags=["digests"])


def _digest_out(digest: Digest) -> DigestOut:
    raw_items: list[DigestItemOut] = []
    try:
        parsed = json.loads(digest.items_json or "[]")
        if isinstance(parsed, list):
            for row in parsed:
                if not isinstance(row, dict):
                    continue
                raw_items.append(
                    DigestItemOut(
                        kind=str(row.get("kind") or "아티클"),
                        title=str(row.get("title") or ""),
                        blurb=str(row.get("blurb") or row.get("summary") or ""),
                        url=str(row.get("url") or ""),
                        topic=str(row.get("topic") or row.get("hint") or ""),
                        insight_q=str(row.get("insight_q") or ""),
                        insight_url=str(row.get("insight_url") or ""),
                        why=str(row.get("why") or ""),
                        angle=str(row.get("angle") or ""),
                    )
                )
    except json.JSONDecodeError:
        raw_items = []
    return DigestOut(
        id=digest.id,
        title=digest.title,
        body=digest.body,
        status=digest.status,
        delivery_channel=digest.delivery_channel,
        error_message=digest.error_message,
        created_at=digest.created_at,
        sent_at=digest.sent_at,
        attempt_count=int(digest.attempt_count or 0),
        next_retry_at=digest.next_retry_at,
        chunks_sent=int(digest.chunks_sent or 0),
        can_resend=digest.status in {"failed", "sending", "partial"},
        items=raw_items,
    )


def _wants_ndjson(accept: str | None) -> bool:
    return "application/x-ndjson" in (accept or "").lower()


async def _finish_delivery(db: Session, user: User, digest: Digest) -> tuple[bool, str]:
    result = await deliver_digest(db, user, digest, wait_ms=0)
    db.refresh(digest)
    if result.skipped:
        return False, digest.error_message or "전송 대기 중입니다"
    if result.ok or digest.status in {"sent", "partial"}:
        if digest.status == "partial":
            return True, digest.error_message
        return True, ""
    return False, result.error or digest.error_message


@router.get("", response_model=list[DigestOut])
def list_digests(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[DigestOut]:
    rows = list(
        db.scalars(
            select(Digest).where(Digest.user_id == user.id).order_by(Digest.created_at.desc()).limit(30)
        ).all()
    )
    return [_digest_out(d) for d in rows]


@router.post("/preview", response_model=None)
async def preview_digest(
    payload: PreviewRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    accept: Annotated[str | None, Header()] = None,
):
    pref = _ensure_pref(db, user)
    if not _wants_ndjson(accept):
        digest = create_digest(
            db,
            user,
            pref,
            status="preview",
            trigger="send_now" if payload.send else "preview",
        )
        if payload.send:
            result = await deliver_digest(db, user, digest, wait_ms=0)
            if not result.ok and not result.skipped:
                pass
        return _digest_out(digest)

    progress_q: queue.Queue[dict[str, object]] = queue.Queue()
    activity_id = start_activity(
        kind="user_send",
        label=f"{user.display_name or user.email} · {'지금 보내기' if payload.send else '미리보기'}",
        phase="trigger",
        detail="digests/preview",
        user_id=user.id,
        display_name=user.display_name or user.email,
    )

    def on_progress(step: str) -> None:
        update_activity(activity_id, phase=step)
        progress_q.put({"type": "step", "step": step})

    def work() -> Digest:
        try:
            return create_digest(
                db,
                user,
                pref,
                status="preview",
                trigger="send_now" if payload.send else "preview",
                on_progress=on_progress,
            )
        except Exception as exc:
            progress_q.put({"type": "error", "step": "crawl", "message": str(exc) or "수집에 실패했습니다"})
            raise

    async def events():
        try:
            loop = asyncio.get_running_loop()
            fut = loop.run_in_executor(None, work)
            while not fut.done():
                try:
                    item = progress_q.get(timeout=0.25)
                    yield json.dumps(item, ensure_ascii=False) + "\n"
                except queue.Empty:
                    yield json.dumps({"type": "ping"}) + "\n"
            while True:
                try:
                    item = progress_q.get_nowait()
                    yield json.dumps(item, ensure_ascii=False) + "\n"
                except queue.Empty:
                    break
            try:
                digest = await fut
            except Exception:
                return
            if payload.send:
                update_activity(activity_id, phase="send")
                yield json.dumps({"type": "step", "step": "send"}, ensure_ascii=False) + "\n"
                digest_id = digest.id
                user_id = user.id
                db.expire_all()
                sender = db.scalar(select(User).options(joinedload(User.kakao)).where(User.id == user_id))
                digest = db.get(Digest, digest_id)
                if sender is None or digest is None:
                    yield json.dumps(
                        {"type": "error", "step": "send", "message": "전송 대상을 다시 불러오지 못했습니다"},
                        ensure_ascii=False,
                    ) + "\n"
                    return
                try:
                    ok, err = await _finish_delivery(db, sender, digest)
                except Exception as exc:
                    yield json.dumps(
                        {"type": "error", "step": "send", "message": str(exc) or "카카오 전송에 실패했습니다"},
                        ensure_ascii=False,
                    ) + "\n"
                    return
                if not ok:
                    db.refresh(digest)
                    if digest.status in {"sent", "partial"}:
                        yield json.dumps(
                            {"type": "done", "digest": _digest_out(digest).model_dump(mode="json")},
                            ensure_ascii=False,
                        ) + "\n"
                        return
                    yield json.dumps(
                        {
                            "type": "error",
                            "step": "send",
                            "message": err or digest.error_message or "카카오 전송에 실패했습니다",
                        },
                        ensure_ascii=False,
                    ) + "\n"
                    return
            yield json.dumps(
                {"type": "done", "digest": _digest_out(digest).model_dump(mode="json")},
                ensure_ascii=False,
            ) + "\n"
        finally:
            end_activity(activity_id)

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _stream_digest_send(
    *,
    user: User,
    db: Session,
    pref,
    build_digest,
):
    """NDJSON progress stream ending in Kakao delivery."""

    progress_q: queue.Queue[dict[str, object]] = queue.Queue()

    def on_progress(step: str) -> None:
        progress_q.put({"type": "step", "step": step})

    def work() -> Digest:
        return build_digest(db, user, pref, on_progress=on_progress)

    async def events():
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(None, work)
        while not fut.done():
            try:
                item = progress_q.get(timeout=0.25)
                yield json.dumps(item, ensure_ascii=False) + "\n"
            except queue.Empty:
                yield json.dumps({"type": "ping"}) + "\n"
        while True:
            try:
                item = progress_q.get_nowait()
                yield json.dumps(item, ensure_ascii=False) + "\n"
            except queue.Empty:
                break
        try:
            digest = await fut
        except Exception as exc:
            yield json.dumps(
                {"type": "error", "step": "crawl", "message": str(exc) or "테스트 발송 준비에 실패했습니다"},
                ensure_ascii=False,
            ) + "\n"
            return
        yield json.dumps({"type": "step", "step": "send"}, ensure_ascii=False) + "\n"
        digest_id = digest.id
        user_id = user.id
        db.expire_all()
        sender = db.scalar(select(User).options(joinedload(User.kakao)).where(User.id == user_id))
        digest = db.get(Digest, digest_id)
        if sender is None or digest is None:
            yield json.dumps(
                {"type": "error", "step": "send", "message": "전송 대상을 다시 불러오지 못했습니다"},
                ensure_ascii=False,
            ) + "\n"
            return
        try:
            ok, err = await _finish_delivery(db, sender, digest)
        except Exception as exc:
            yield json.dumps(
                {"type": "error", "step": "send", "message": str(exc) or "카카오 전송에 실패했습니다"},
                ensure_ascii=False,
            ) + "\n"
            return
        if not ok:
            db.refresh(digest)
            if digest.status in {"sent", "partial"}:
                yield json.dumps(
                    {"type": "done", "digest": _digest_out(digest).model_dump(mode="json")},
                    ensure_ascii=False,
                ) + "\n"
                return
            yield json.dumps(
                {
                    "type": "error",
                    "step": "send",
                    "message": err or digest.error_message or "카카오 전송에 실패했습니다",
                },
                ensure_ascii=False,
            ) + "\n"
            return
        yield json.dumps(
            {"type": "done", "digest": _digest_out(digest).model_dump(mode="json")},
            ensure_ascii=False,
        ) + "\n"

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/test-send", response_model=None)
async def test_send_digest(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    accept: Annotated[str | None, Header()] = None,
):
    pref = _ensure_pref(db, user)
    if not _wants_ndjson(accept):
        digest = create_test_digest(db, user, pref, status="preview")
        result = await deliver_digest(db, user, digest, wait_ms=0)
        if not result.ok and not result.skipped:
            pass
        return _digest_out(digest)

    import time

    def delayed_build(db: Session, user: User, pref, *, on_progress) -> Digest:
        def delayed_progress(step: str) -> None:
            if step == "crawl":
                time.sleep(0.35)
            elif step == "curate":
                time.sleep(0.35)
            elif step == "format":
                time.sleep(0.25)
            on_progress(step)

        return create_test_digest(db, user, pref, status="preview", on_progress=delayed_progress)

    return _stream_digest_send(user=user, db=db, pref=pref, build_digest=delayed_build)


@router.post("/{digest_id}/resend", response_model=DigestOut)
async def resend_digest(
    digest_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DigestOut:
    digest = db.get(Digest, digest_id)
    if digest is None or digest.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="브리프를 찾을 수 없습니다")
    if digest.status == "sent":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 전송된 브리프입니다")
    result = await deliver_digest(db, user, digest, force=True, reset_attempts=True)
    db.refresh(digest)
    if not result.ok and not result.skipped:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=result.error or digest.error_message or "카카오 전송에 실패했습니다",
        )
    return _digest_out(digest)
