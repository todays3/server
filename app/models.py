from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    occupation: Mapped[str] = mapped_column(String(80), default="")
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Nullable for Kakao-only OAuth users (no password)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # pending | approved | rejected | stopped
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    preference: Mapped["Preference"] = relationship(back_populates="user", uselist=False)
    kakao: Mapped["KakaoAccount | None"] = relationship(back_populates="user", uselist=False)
    digests: Mapped[list["Digest"]] = relationship(back_populates="user")
    push_devices: Mapped[list["PushDevice"]] = relationship(back_populates="user")
    sticky_notes: Mapped[list["StickyNote"]] = relationship(back_populates="user")
    sticky_note_likes: Mapped[list["StickyNoteLike"]] = relationship(back_populates="user")


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
    # comma-separated desk role ids (investor, developer, …)
    roles: Mapped[str] = mapped_column(Text, default="")
    role_settings: Mapped[str] = mapped_column(Text, default="{}")

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
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    chunks_sent: Mapped[int] = mapped_column(Integer, default=0)
    miss_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    delivery_trace_json: Mapped[str] = mapped_column(Text, default="[]")
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
    cpu_peak_percent: Mapped[int] = mapped_column(Integer, default=0)
    rss_peak_bytes: Mapped[int] = mapped_column(Integer, default=0)
    rss_delta_bytes: Mapped[int] = mapped_column(Integer, default=0)
    # Small title/source sample for the user's briefing history; raw article bodies are not stored.
    crawled_items_json: Mapped[str] = mapped_column(Text, default="[]")
    curator: Mapped[str] = mapped_column(String(32), default="")
    llm_skip_reason: Mapped[str] = mapped_column(String(64), default="")
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    user: Mapped[User] = relationship()


class PushDevice(Base):
    """One FCM token per browser/app install. A Kakao user may have many devices."""

    __tablename__ = "push_devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token: Mapped[str] = mapped_column(String(4096), unique=True)
    device_id: Mapped[str] = mapped_column(String(80), default="", index=True)
    platform: Mapped[str] = mapped_column(String(16), default="web")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="push_devices")


class StickyNote(Base):
    """Public suggestion slip. Nickname is snapshotted; user_id stays server-only."""

    __tablename__ = "sticky_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    nickname: Mapped[str] = mapped_column(String(120), default="")
    body: Mapped[str] = mapped_column(String(800))
    # suggestion | update — members can only create suggestion
    kind: Mapped[str] = mapped_column(String(32), default="suggestion", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    user: Mapped[User] = relationship(back_populates="sticky_notes")
    likes: Mapped[list["StickyNoteLike"]] = relationship(back_populates="note", cascade="all, delete-orphan")


class StickyNoteLike(Base):
    """One heart per user per note. UniqueConstraint is the idempotency lock."""

    __tablename__ = "sticky_note_likes"
    __table_args__ = (UniqueConstraint("note_id", "user_id", name="uq_sticky_note_like_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    note_id: Mapped[int] = mapped_column(ForeignKey("sticky_notes.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    note: Mapped[StickyNote] = relationship(back_populates="likes")
    user: Mapped[User] = relationship(back_populates="sticky_note_likes")


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
    ttft_ms: Mapped[int] = mapped_column(Integer, default=0)
    total_ms: Mapped[int] = mapped_column(Integer, default=0)
    tps: Mapped[float] = mapped_column(Float, default=0.0)
    streamed: Mapped[bool] = mapped_column(Boolean, default=False)
    faithfulness: Mapped[float | None] = mapped_column(Float, nullable=True)
    hallucination_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    answer_relevance: Mapped[float | None] = mapped_column(Float, nullable=True)
    context_precision: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    user: Mapped[User] = relationship()
