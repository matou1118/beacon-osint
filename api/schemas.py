from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PhoneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone: str = Field(min_length=6, max_length=32)


class PhoneLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone: str = Field(min_length=6, max_length=32)
    code: str = Field(min_length=4, max_length=12)


class WechatLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=512)


class SubscriptionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gov_levels: list[str] = Field(default_factory=list, max_length=20)
    region: str | None = Field(default=None, max_length=128)
    categories: list[str] = Field(default_factory=list, max_length=50)
    target_group: str | None = Field(default=None, max_length=256)
    target_industry: str | None = Field(default=None, max_length=256)
    keywords: list[str] = Field(default_factory=list, max_length=50)
    push_frequency: Literal["daily", "weekly", "none"] = "daily"
    enabled: bool = True


class SubscriptionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gov_levels: list[str] | None = Field(default=None, max_length=20)
    region: str | None = Field(default=None, max_length=128)
    categories: list[str] | None = Field(default=None, max_length=50)
    target_group: str | None = Field(default=None, max_length=256)
    target_industry: str | None = Field(default=None, max_length=256)
    keywords: list[str] | None = Field(default=None, max_length=50)
    push_frequency: Literal["daily", "weekly", "none"] | None = None
    enabled: bool | None = None


class PushPreferencePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    push_frequency: Literal["daily", "weekly", "none"]
    enabled: bool = True


class FeedbackPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    announcement_id: int = Field(gt=0)
    useful: bool
    comment: str | None = Field(default=None, max_length=500)
