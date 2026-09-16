# ruff: noqa: UP042 -- keep (str, Enum) for consistency across all bizapp schemas
"""Pydantic schemas for the marketing package.

Campaigns, journey steps, audiences, content assets, send log, analytics.
Raw transport is comms/'s job; marketing owns orchestration and measurement.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class CampaignStatus(str, Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    AWAITING_APPROVAL = "awaiting_approval"
    SENDING = "sending"
    SENT = "sent"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


class StepAction(str, Enum):
    EMAIL = "email"
    SMS = "sms"
    WAIT = "wait"          # delay step, no send
    CHAT = "chat"


class AssetStatus(str, Enum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    ARCHIVED = "archived"


class SendStatus(str, Enum):
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


# ------------------------------------------------------------------ campaigns
class Campaign(BaseModel):
    id: str
    tenant_id: str
    name: str
    status: CampaignStatus
    audience_id: str | None = None
    scheduled_at: datetime | None = None
    approval_id: str | None = None
    launched_at: datetime | None = None
    completed_at: datetime | None = None
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    audience_id: str | None = None
    scheduled_at: datetime | None = None


class CampaignUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    audience_id: str | None = None
    scheduled_at: datetime | None = None
    status: CampaignStatus | None = None  # draft<->scheduled<->paused<->cancelled only


class CampaignStep(BaseModel):
    id: str
    tenant_id: str
    campaign_id: str
    position: int = Field(ge=0)
    action: StepAction
    name: str | None = None
    template_id: str | None = None      # comms message template
    asset_id: str | None = None        # marketing content asset
    delay_minutes: int = Field(default=0, ge=0)  # wait after previous step
    created_at: datetime


class CampaignStepCreate(BaseModel):
    position: int = Field(ge=0)
    action: StepAction
    name: str | None = Field(default=None, max_length=200)
    template_id: str | None = None
    asset_id: str | None = None
    delay_minutes: int = Field(default=0, ge=0)


class CampaignStepUpdate(BaseModel):
    position: int | None = Field(default=None, ge=0)
    name: str | None = Field(default=None, max_length=200)
    template_id: str | None = None
    asset_id: str | None = None
    delay_minutes: int | None = Field(default=None, ge=0)


# ------------------------------------------------------------------ audiences
class Audience(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: str | None = None
    filter: dict[str, Any] = Field(default_factory=dict)  # segmentation rule tree
    member_count: int = 0               # last resolved count (cached)
    created_at: datetime
    updated_at: datetime


class AudienceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    filter: dict[str, Any] = Field(default_factory=dict)


class AudienceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    filter: dict[str, Any] | None = None


# ------------------------------------------------------------------ assets
class ContentAsset(BaseModel):
    id: str
    tenant_id: str
    kind: str                          # "email_html" | "email_text" | "sms" | "social" | ...
    title: str
    body: str
    status: AssetStatus
    brand_check: dict[str, Any] | None = None  # brand-safety results, when checked
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


class ContentAssetCreate(BaseModel):
    kind: str = Field(min_length=1, max_length=50)
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1)


# ------------------------------------------------------------------ sends + analytics
class CampaignSend(BaseModel):
    id: str
    tenant_id: str
    campaign_id: str
    step_id: str
    contact_ref: str                   # opaque contact reference (id or email)
    message_id: str | None = None      # comms message id, when sent
    status: SendStatus
    error: str | None = None
    sent_at: datetime | None = None


class CampaignAnalytics(BaseModel):
    campaign_id: str
    tenant_id: str
    audience_size: int
    sent: int
    failed: int
    skipped: int
    by_step: dict[str, dict[str, int]]  # step_id -> {"sent": n, "failed": n, ...}


class LaunchResult(BaseModel):
    campaign_id: str
    status: CampaignStatus
    approval_id: str | None = None     # set when policy required approval
    audience_size: int = 0
    sends_queued: int = 0
    note: str | None = None
