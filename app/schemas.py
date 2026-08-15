from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)

    @field_validator("password")
    @classmethod
    def password_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("비밀번호를 입력하세요")
        return value


class LoginRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class RegisterResponse(BaseModel):
    message: str
    status: Literal["pending"] = "pending"


class KakaoOAuthStartOut(BaseModel):
    configured: bool
    url: str | None = None
    message: str | None = None


class KakaoCompleteRequest(BaseModel):
    ticket: str = Field(min_length=16, max_length=200)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    display_name: str
    status: str
    is_admin: bool
    kakao_connected: bool


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    display_name: str
    status: str
    is_admin: bool
    created_at: datetime
    approved_at: datetime | None


class SendTimeSlot(BaseModel):
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)


class PreferenceUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    topics: list[str] | None = None
    tone: str | None = Field(default=None, max_length=64)  # deprecated
    send_hour: int | None = Field(default=None, ge=0, le=23)
    send_minute: int | None = Field(default=None, ge=0, le=59)
    send_times: list[SendTimeSlot] | None = None
    timezone: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)  # customization prompt
    sources: list[str] | None = None
    insight_questions: bool | None = None

    @field_validator("topics")
    @classmethod
    def topics_non_empty_items(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        cleaned = [t.strip() for t in value if t and t.strip()]
        if len(cleaned) > 60:
            raise ValueError("관심 주제는 최대 60개입니다")
        return cleaned

    @field_validator("sources")
    @classmethod
    def sources_clean(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        cleaned = [t.strip() for t in value if t and t.strip()]
        if len(cleaned) > 40:
            raise ValueError("참고 사이트는 최대 40개입니다")
        return cleaned

    @field_validator("send_times")
    @classmethod
    def send_times_bounded(cls, value: list[SendTimeSlot] | None) -> list[SendTimeSlot] | None:
        if value is None:
            return value
        if len(value) < 1:
            raise ValueError("발송 시간을 하나 이상 설정하세요")
        if len(value) > 5:
            raise ValueError("발송 시간은 최대 5개입니다")
        return value


class PreferenceOut(BaseModel):
    topics: list[str]
    tone: str
    send_hour: int
    send_minute: int
    send_times: list[SendTimeSlot]
    timezone: str
    enabled: bool
    notes: str
    sources: list[str]
    insight_questions: bool


class DigestItemOut(BaseModel):
    kind: str
    title: str
    blurb: str = ""
    url: str
    topic: str = ""
    insight_q: str = ""
    insight_url: str = ""


class DigestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    body: str
    status: str
    delivery_channel: str
    error_message: str
    created_at: datetime
    sent_at: datetime | None
    items: list[DigestItemOut] = Field(default_factory=list)


class PreviewRequest(BaseModel):
    send: bool = False


class AdminDigestPreviewRequest(BaseModel):
    user_id: int


class DigestCandidateOut(BaseModel):
    kind: str
    title: str
    url: str
    summary: str = ""
    source: str = ""


class AdminDigestPreviewOut(BaseModel):
    user_id: int
    email: str
    display_name: str
    curator: str
    llm_configured: bool
    llm_skip_reason: str = ""
    llm_raw: str = ""
    topics: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    title: str
    body: str
    items: list[DigestItemOut] = Field(default_factory=list)
    candidates: list[DigestCandidateOut] = Field(default_factory=list)
    sent_to_kakao: bool = False


class KakaoStatusOut(BaseModel):
    configured: bool
    connected: bool
    kakao_id: str | None = None


class KakaoConnectOut(BaseModel):
    configured: bool
    url: str | None = None
    message: str | None = None


class KakaoDisconnectOut(BaseModel):
    connected: bool


class HealthOut(BaseModel):
    status: str
    kakao_configured: bool
    llm_configured: bool
    llm_provider: str
    sources: str
    scheduler: str


class NotificationHookIn(BaseModel):
    app: str = Field(default="", max_length=80)
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(default="", max_length=2000)
    source: str = Field(default="", max_length=80)


class NotificationHookOut(BaseModel):
    accepted: bool
    duplicate: bool = False
    sent: bool = False
    skipped: str = ""


class AdminUsageSummary(BaseModel):
    calls: int = 0
    success_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class AdminDailyPoint(BaseModel):
    """One Seoul calendar day of admin trend metrics."""

    date: str
    users_total: int = 0
    users_approved: int = 0
    users_pending: int = 0
    tokens: int = 0
    tokens_cumulative: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    success_calls: int = 0
    calls_cumulative: int = 0


class AdminUsageEvent(BaseModel):
    id: int
    user_id: int
    email: str
    purpose: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    success: bool
    error_message: str
    created_at: datetime


class AdminPrefDetail(BaseModel):
    topics: list[str]
    sources: list[str]
    notes: str
    send_hour: int
    send_minute: int
    send_times: list[SendTimeSlot]
    timezone: str
    enabled: bool
    insight_questions: bool = False


class AdminUserDetail(BaseModel):
    id: int
    email: EmailStr
    display_name: str
    status: str
    is_admin: bool
    kakao_connected: bool
    created_at: datetime
    approved_at: datetime | None
    preference: AdminPrefDetail | None
    usage_all: AdminUsageSummary
    usage_today: AdminUsageSummary


class AdminOverview(BaseModel):
    users_total: int
    users_pending: int
    users_approved: int
    llm_configured: bool
    llm_provider: str
    llm_model: str
    usage_all: AdminUsageSummary
    usage_today: AdminUsageSummary
    series: list[AdminDailyPoint]
    recent: list[AdminUsageEvent]
    last_run_cpu_peak_percent: int = 0
    last_run_rss_peak_bytes: int = 0
    last_run_rss_delta_bytes: int = 0
    runs_cpu_peak_max_percent: int = 0
    runs_rss_peak_max_bytes: int = 0


class SourceFeedProbeOut(BaseModel):
    channel: str
    kind: str
    name: str
    url: str
    ok: bool
    status_code: int | None = None
    item_count: int = 0
    sample_titles: list[str] = Field(default_factory=list)
    error: str = ""
    bot_risk: str = "unknown"
    bot_signal: str = ""


class SourceSiteProbeOut(BaseModel):
    site_id: str
    label: str
    ok: bool | None = None
    probed_at: datetime | None = None
    duration_ms: int = 0
    item_count: int = 0
    feeds: list[SourceFeedProbeOut] = Field(default_factory=list)
    bot_risk: str = "unknown"


class SourceProbeListOut(BaseModel):
    ok_count: int = 0
    fail_count: int = 0
    unknown_count: int = 0
    blocked_count: int = 0
    caution_count: int = 0
    clear_count: int = 0
    sites: list[SourceSiteProbeOut]


class RefSiteOut(BaseModel):
    id: str
    label: str
    blurb: str
    url: str


class RefSiteGroupOut(BaseModel):
    id: str
    label: str
    match: list[str]
    sites: list[RefSiteOut]


class RefSiteCatalogOut(BaseModel):
    groups: list[RefSiteGroupOut]
    mega_map: dict[str, list[str]]


class CrawlRunOut(BaseModel):
    id: int
    user_id: int
    email: str
    display_name: str
    digest_id: int | None = None
    trigger: str
    slot_label: str = ""
    kinds: dict[str, int]
    total: int
    created_at: datetime


class CrawlRunListOut(BaseModel):
    runs: list[CrawlRunOut]
    total_runs: int


class LatencyLayerOut(BaseModel):
    id: str
    label: str
    p50_ms: int = 0
    p90_ms: int = 0
    mean_ms: int = 0


class LatencyRunOut(BaseModel):
    id: int
    user_id: int
    email: str
    display_name: str
    digest_id: int | None = None
    trigger: str
    slot_label: str = ""
    curator: str = ""
    llm_skip_reason: str = ""
    created_at: datetime
    ready_at: datetime | None = None
    sent_at: datetime | None = None
    lead_ms: int = 0
    prep_ms: int = 0
    e2e_ms: int = 0
    layers: dict[str, int]
    cpu_peak_percent: int = 0
    rss_peak_bytes: int = 0
    rss_delta_bytes: int = 0


class LatencyListOut(BaseModel):
    lead_minutes: int
    sample_size: int
    layers: list[LatencyLayerOut]
    runs: list[LatencyRunOut]
    cpu_peak_max_percent: int = 0
    rss_peak_max_bytes: int = 0


