from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    # Nullable for Kakao-only OAuth users (no password)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # pending | approved | rejected
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    preference: Mapped["Preference"] = relationship(back_populates="user", uselist=False)
    kakao: Mapped["KakaoAccount | None"] = relationship(back_populates="user", uselist=False)
    digests: Mapped[list["Digest"]] = relationship(back_populates="user")


class Preference(Base):
    __tablename__ = "preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    topics: Mapped[str] = mapped_column(Text, default="경제/주식/국내증시,경제/주식/미국증시")
    tone: Mapped[str] = mapped_column(String(64), default="")  # unused; keep for schema compat
    send_hour: Mapped[int] = mapped_column(Integer, default=7)
    send_minute: Mapped[int] = mapped_column(Integer, default=30)
    # comma-separated Seoul times: "07:30,12:00,18:00"
    send_times: Mapped[str] = mapped_column(Text, default="07:30")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Seoul")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")  # answer customization prompt
    # comma-separated reference site ids (naver-finance,toss-securities,…)
    sources: Mapped[str] = mapped_column(Text, default="naver-finance,toss-securities,yahoo-finance")
    # per-item follow-up insight question + helper URL
    insight_questions: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[User] = relationship(back_populates="preference")


class KakaoAccount(Base):
    __tablename__ = "kakao_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    kakao_id: Mapped[str] = mapped_column(String(64), unique=True)
    access_token: Mapped[str] = mapped_column(Text, default="")
    refresh_token: Mapped[str] = mapped_column(Text, default="")
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    access_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refresh_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="kakao")


class Digest(Base):
    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    delivery_channel: Mapped[str] = mapped_column(String(32), default="kakao_me")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # JSON array of curated items for structured clients
    items_json: Mapped[str] = mapped_column(Text, default="[]")

    user: Mapped[User] = relationship(back_populates="digests")


class CrawlRun(Base):
    """One crawl snapshot (kind counts) for admin review."""

    __tablename__ = "crawl_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    digest_id: Mapped[int | None] = mapped_column(ForeignKey("digests.id"), nullable=True)
    # schedule | send_now | preview | admin_preview
    trigger: Mapped[str] = mapped_column(String(32), default="schedule", index=True)
    slot_label: Mapped[str] = mapped_column(String(16), default="")
    kinds_json: Mapped[str] = mapped_column(Text, default="{}")
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    trigger_ms: Mapped[int] = mapped_column(Integer, default=0)
    crawl_ms: Mapped[int] = mapped_column(Integer, default=0)
    aggregation_ms: Mapped[int] = mapped_column(Integer, default=0)
    llm_ms: Mapped[int] = mapped_column(Integer, default=0)
    format_ms: Mapped[int] = mapped_column(Integer, default=0)
    wait_ms: Mapped[int] = mapped_column(Integer, default=0)
    send_ms: Mapped[int] = mapped_column(Integer, default=0)
    total_ms: Mapped[int] = mapped_column(Integer, default=0)
    lead_ms: Mapped[int] = mapped_column(Integer, default=0)
    curator: Mapped[str] = mapped_column(String(32), default="")
    llm_skip_reason: Mapped[str] = mapped_column(String(64), default="")
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    user: Mapped[User] = relationship()


class LlmUsage(Base):
    """Per-call LLM token meter for admin serving dashboard."""

    __tablename__ = "llm_usages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(64), default="digest_curate", index=True)
    provider: Mapped[str] = mapped_column(String(32), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    user: Mapped[User] = relationship()
