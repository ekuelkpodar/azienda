# ruff: noqa: UP042 -- keep (str, Enum) for consistency across all bizapp schemas
"""Pydantic schemas for the comms package.

Unified communication layer: channels, message templates, messages, delivery log.
All money is Decimal; all datetimes UTC; every row carries tenant_id.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ChannelKind(str, Enum):
    EMAIL = "email"
    SMS = "sms"
    VOICE = "voice"
    CHAT = "chat"
    PUSH = "push"


class MessageDirection(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageStatus(str, Enum):
    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"


# ------------------------------------------------------------------ channels
class Channel(BaseModel):
    id: str
    tenant_id: str
    kind: ChannelKind
    name: str
    provider: str                     # provider name, e.g. "log_only" (MVP)
    config: dict[str, Any] = Field(default_factory=dict)  # provider config;
    # secrets via SecretBroker refs only
    is_active: bool = True
    created_at: datetime


class ChannelCreate(BaseModel):
    kind: ChannelKind
    name: str = Field(min_length=1, max_length=200)
    provider: str = Field(default="log_only", min_length=1, max_length=100)
    config: dict[str, Any] = Field(default_factory=dict)


class ChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    config: dict[str, Any] | None = None
    is_active: bool | None = None


# ------------------------------------------------------------------ templates
class MessageTemplate(BaseModel):
    id: str
    tenant_id: str
    name: str
    kind: ChannelKind
    subject: str | None = None
    body: str
    variables: list[str] = Field(default_factory=list)
    approved_at: datetime | None = None
    created_at: datetime


class MessageTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: ChannelKind
    subject: str | None = None
    body: str = Field(min_length=1)
    variables: list[str] = Field(default_factory=list)


class TemplateRenderRequest(BaseModel):
    variables: dict[str, Any] = Field(default_factory=dict)


# ------------------------------------------------------------------ messages
class Message(BaseModel):
    id: str
    tenant_id: str
    conversation_id: str | None = None
    channel_id: str
    kind: ChannelKind
    direction: MessageDirection
    to_address: str
    from_address: str | None = None
    subject: str | None = None
    body: str
    template_id: str | None = None
    status: MessageStatus
    provider: str
    provider_message_id: str | None = None
    cost_usd: Decimal = Decimal("0")
    error: str | None = None
    idempotency_key: str | None = None
    created_at: datetime
    sent_at: datetime | None = None
    delivered_at: datetime | None = None


class MessageSend(BaseModel):
    channel_kind: ChannelKind
    to_address: str = Field(min_length=1, max_length=320)
    body: str = Field(min_length=1, max_length=10000)
    subject: str | None = Field(default=None, max_length=300)
    from_address: str | None = Field(default=None, max_length=320)
    template_id: str | None = None
    template_variables: dict[str, Any] = Field(default_factory=dict)
    conversation_id: str | None = None
    channel_id: str | None = None  # explicit channel override; else default active channel
    idempotency_key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DeliveryUpdate(BaseModel):
    status: MessageStatus
    provider_message_id: str | None = None
    error: str | None = None


class UsageSummary(BaseModel):
    tenant_id: str
    period_start: datetime
    period_end: datetime
    by_kind: dict[str, dict[str, Any]]  # kind -> {"units": int, "cost_usd": Decimal-as-str}
