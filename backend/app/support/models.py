"""SQLAlchemy ORM models for support. Mirrors DATABASE.md §10 (support tables).

Tables: tickets, ticket_messages, sla_policies, escalation_rules, kb_articles,
macros.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.models import GUID


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TicketModel(Base):
    __tablename__ = "tickets"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="open")
    priority: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="normal")
    requester_contact_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    assignee_user_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    assignee_agent_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    queue: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    channel: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    sla_policy_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    first_response_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True)
    first_response_breached: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    resolution_breached: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    opened_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    resolution: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(sa.JSON, nullable=False, default=list)


class TicketMessageModel(Base):
    __tablename__ = "ticket_messages"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    ticket_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    author_type: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    author_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    channel: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    body: Mapped[str] = mapped_column(sa.Text, nullable=False)
    is_internal_note: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_utcnow)


class SLAPolicyModel(Base):
    __tablename__ = "sla_policies"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    priority: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    first_response_minutes: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    resolution_hours: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)


class EscalationRuleModel(Base):
    __tablename__ = "escalation_rules"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    priorities: Mapped[list[str]] = mapped_column(sa.JSON, nullable=False, default=list)
    breach_type: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="any")
    min_age_minutes: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    action_assign_user_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    action_assign_queue: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    action_set_priority: Mapped[str | None] = mapped_column(sa.String(16), nullable=True)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_utcnow)


class KBArticleModel(Base):
    __tablename__ = "kb_articles"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    title: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    body: Mapped[str] = mapped_column(sa.Text, nullable=False)
    category: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    tags: Mapped[list[str]] = mapped_column(sa.JSON, nullable=False, default=list)
    is_faq: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="draft")
    view_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    created_by: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class MacroModel(Base):
    __tablename__ = "macros"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    body: Mapped[str] = mapped_column(sa.Text, nullable=False)
    shared: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_utcnow)
