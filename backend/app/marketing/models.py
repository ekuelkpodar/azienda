"""SQLAlchemy ORM models for marketing. Mirrors DATABASE.md §10 (marketing tables).

Tables: campaigns, campaign_steps, audiences, audience_members, content_assets,
campaign_sends (send log; marketing-local so analytics don't cross the seam).
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


class CampaignModel(Base):
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="draft")
    audience_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    approval_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    launched_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class CampaignStepModel(Base):
    __tablename__ = "campaign_steps"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    campaign_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    position: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    action: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    name: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    template_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    asset_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    delay_minutes: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_utcnow)


class AudienceModel(Base):
    __tablename__ = "audiences"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    filter: Mapped[dict[str, Any]] = mapped_column(sa.JSON, nullable=False, default=dict)
    member_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class AudienceMemberModel(Base):
    __tablename__ = "audience_members"

    audience_id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    contact_id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    added_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_utcnow)


class ContentAssetModel(Base):
    __tablename__ = "content_assets"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    title: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    body: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="draft")
    brand_check: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON, nullable=True)
    created_by: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class CampaignSendModel(Base):
    __tablename__ = "campaign_sends"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    campaign_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    step_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    contact_ref: Mapped[str] = mapped_column(sa.String(320), nullable=False)
    message_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="queued")
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
