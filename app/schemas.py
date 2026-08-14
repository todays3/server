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


class PreferenceUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    topics: list[str] | None = None
    tone: str | None = Field(default=None, max_length=64)  # deprecated
    send_hour: int | None = Field(default=None, ge=0, le=23)
    send_minute: int | None = Field(default=None, ge=0, le=59)
    timezone: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)  # customization prompt

    @field_validator("topics")
    @classmethod
    def topics_non_empty_items(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        cleaned = [t.strip() for t in value if t and t.strip()]
        if len(cleaned) > 20:
            raise ValueError("관심 주제는 최대 20개입니다")
        return cleaned


class PreferenceOut(BaseModel):
    topics: list[str]
    tone: str
    send_hour: int
    send_minute: int
    timezone: str
    enabled: bool
    notes: str


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


class PreviewRequest(BaseModel):
    send: bool = False


class HealthOut(BaseModel):
    status: str
    kakao_configured: bool
    llm_configured: bool
    scheduler: str
