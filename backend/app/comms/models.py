"""SQLAlchemy ORM models for comms. Mirrors DATABASE.md §10 (comms tables).

Table ownership: channels, conversations, messages, message_templates, comms_usage.
``conversations`` is included for cross-channel threading (support/ links here);
message writes to a conversation are done through this package's service.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.models import GUID


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ChannelModel(Base):
    __tablename__ = "channels"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    provider: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(sa.JSON, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class ConversationModel(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    channel_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    subject_type: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)
    subject_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="open")
    started_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_utcnow)


class MessageModel(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    conversation_id: Mapped[str | None] = mapped_column(GUID(), nullable=True, index=True)
    channel_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    kind: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    direction: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    to_address: Mapped[str] = mapped_column(sa.String(320), nullable=False)
    from_address: Mapped[str | None] = mapped_column(sa.String(320), nullable=True)
    subject: Mapped[str | None] = mapped_column(sa.String(300), nullable=True)
    body: Mapped[str] = mapped_column(sa.Text, nullable=False)
    template_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="queued")
    provider: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    cost_usd: Mapped[float] = mapped_column(sa.Numeric(19, 4), nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)


class MessageTemplateModel(Base):
    __tablename__ = "message_templates"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    kind: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    subject: Mapped[str | None] = mapped_column(sa.String(300), nullable=True)
    body: Mapped[str] = mapped_column(sa.Text, nullable=False)
    variables: Mapped[dict[str, Any]] = mapped_column(sa.JSON, nullable=False, default=list)
    approved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_utcnow)


class CommsUsageModel(Base):
    __tablename__ = "comms_usage"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    period: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    units: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(sa.Numeric(19, 4), nullable=False, default=0)
