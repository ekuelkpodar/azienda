"""SQLAlchemy ORM models for scheduling. Mirrors DATABASE.md §10 (scheduling tables).

Tables: calendars, calendar_events, bookings, availability_rules.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, time
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


class CalendarModel(Base):
    __tablename__ = "calendars"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    owner_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    timezone: Mapped[str] = mapped_column(sa.String(64), nullable=False, default="UTC")
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_utcnow)


class AvailabilityRuleModel(Base):
    __tablename__ = "availability_rules"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    calendar_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    weekday: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    start_time: Mapped[time] = mapped_column(sa.Time, nullable=False)
    end_time: Mapped[time] = mapped_column(sa.Time, nullable=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_utcnow)


class CalendarEventModel(Base):
    __tablename__ = "calendar_events"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    calendar_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    title: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    location: Mapped[str | None] = mapped_column(sa.String(300), nullable=True)
    event_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", sa.JSON,
                                                      nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_utcnow)


class BookingModel(Base):
    __tablename__ = "bookings"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    calendar_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    event_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    contact_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    title: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="confirmed")
    reminders: Mapped[list[dict[str, Any]]] = mapped_column(sa.JSON, nullable=False, default=list)
    reminders_sent: Mapped[list[str]] = mapped_column(sa.JSON, nullable=False, default=list)
    cancelled_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_utcnow)
