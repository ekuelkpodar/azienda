# ruff: noqa: UP042 -- keep (str, Enum) for consistency across all bizapp schemas
"""Pydantic schemas for the scheduling package.

Calendars, availability rules, events, bookings, reminders.
External calendar sync is a provider interface (``sync.py``) + documented future.
"""
from __future__ import annotations

from datetime import datetime, time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class BookingStatus(str, Enum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"


class Calendar(BaseModel):
    id: str
    tenant_id: str
    name: str
    owner_id: str | None = None
    timezone: str = "UTC"
    is_active: bool = True
    created_at: datetime


class CalendarCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    owner_id: str | None = None
    timezone: str = Field(default="UTC", min_length=1, max_length=64)


class CalendarUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    is_active: bool | None = None


class AvailabilityRule(BaseModel):
    id: str
    tenant_id: str
    calendar_id: str
    weekday: int = Field(ge=0, le=6)     # Monday=0 … Sunday=6
    start_time: time
    end_time: time
    created_at: datetime


class AvailabilityRuleCreate(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start_time: time
    end_time: time


class CalendarEvent(BaseModel):
    id: str
    tenant_id: str
    calendar_id: str
    title: str
    starts_at: datetime
    ends_at: datetime
    location: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class CalendarEventCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    starts_at: datetime
    ends_at: datetime
    location: str | None = Field(default=None, max_length=300)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Booking(BaseModel):
    id: str
    tenant_id: str
    calendar_id: str
    event_id: str
    contact_id: str | None = None
    title: str
    starts_at: datetime
    ends_at: datetime
    status: BookingStatus
    reminders: list[dict[str, Any]] = Field(default_factory=list)
    reminders_sent: list[str] = Field(default_factory=list)  # sent reminder keys
    cancelled_at: datetime | None = None
    cancel_reason: str | None = None
    created_by: str | None = None
    created_at: datetime


class BookingCreate(BaseModel):
    contact_id: str | None = None
    title: str = Field(min_length=1, max_length=300)
    starts_at: datetime
    ends_at: datetime
    reminders: list[dict[str, Any]] = Field(default_factory=list)
    idempotency_key: str | None = None


class BookingReschedule(BaseModel):
    starts_at: datetime
    ends_at: datetime


class BookingCancel(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class TimeSlot(BaseModel):
    starts_at: datetime
    ends_at: datetime


class AvailabilityQuery(BaseModel):
    start: datetime
    end: datetime
    duration_minutes: int = Field(gt=0, le=1440)
    buffer_minutes: int = Field(default=0, ge=0)
    min_notice_minutes: int = Field(default=0, ge=0)


class ReminderDue(BaseModel):
    booking_id: str
    calendar_id: str
    contact_id: str | None
    title: str
    starts_at: datetime
    remind_at: datetime
    channel: str = "email"
    offset_minutes: int
