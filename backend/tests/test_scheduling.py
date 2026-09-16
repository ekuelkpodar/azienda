"""Tests for the scheduling package: availability, conflicts, permissions,
policy-gated mutations, idempotency, reminders, and cross-tenant isolation."""
from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest

from app.core.contracts import DomainEvent, TenantContext
from app.scheduling import schemas as S
from app.scheduling.service import (
    BookingConflictError,
    CalendarNotFoundError,
    PermissionDeniedError,
    PolicyDeniedError,
    SchedulingService,
)
from tests.conftest import FakePolicyEngine


class _Bus:
    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    async def publish(self, event: DomainEvent) -> None:
        self.events.append(event)

    def subscribe(self, topic: str, handler):  # noqa: ANN001, ANN202
        return None


def _svc(policy=None, bus=None) -> SchedulingService:  # noqa: ANN001
    return SchedulingService(
        policy=policy if policy is not None else FakePolicyEngine(),
        events=bus if bus is not None else _Bus())


def _future_day() -> datetime:
    return datetime.now(UTC).replace(hour=0, minute=0, second=0,
                                    microsecond=0) + timedelta(days=2)


async def _calendar_with_hours(svc: SchedulingService,
                               tenant: TenantContext) -> tuple[S.Calendar, datetime]:
    cal = await svc.create_calendar(tenant, S.CalendarCreate(
        name="Sales", timezone="America/New_York"))
    day = _future_day()
    await svc.set_availability_rule(tenant, cal.id, S.AvailabilityRuleCreate(
        weekday=day.weekday(), start_time=time(9, 0), end_time=time(17, 0)))
    return cal, day


# ------------------------------------------------------------ availability
async def test_availability_slots_respect_rules(tenant: TenantContext) -> None:
    svc = _svc()
    cal, day = await _calendar_with_hours(svc, tenant)
    slots = await svc.availability(
        tenant, cal.id, S.AvailabilityQuery(
            start=day.replace(hour=9), end=day.replace(hour=17),
            duration_minutes=30, min_notice_minutes=0),
        now=datetime.now(UTC))
    assert slots, "expected open slots inside the availability window"
    assert all((s.ends_at - s.starts_at).total_seconds() == 30 * 60 for s in slots)
    # 15-minute grid alignment
    assert all(s.starts_at.minute % 15 == 0 for s in slots)


async def test_booking_blocks_later_availability(tenant: TenantContext) -> None:
    svc = _svc()
    cal, day = await _calendar_with_hours(svc, tenant)
    start = day.replace(hour=10, minute=0)
    await svc.book(tenant, cal.id, S.BookingCreate(
        contact_id="c1", title="Call", starts_at=start,
        ends_at=start + timedelta(minutes=30)))
    slots = await svc.availability(
        tenant, cal.id, S.AvailabilityQuery(
            start=day.replace(hour=9), end=day.replace(hour=12),
            duration_minutes=30, min_notice_minutes=0),
        now=datetime.now(UTC))
    booked = [s for s in slots if s.starts_at == start]
    assert booked == [], "booked window must not appear as available"


# ---------------------------------------------------------------- conflicts
async def test_overlapping_booking_rejected(tenant: TenantContext) -> None:
    svc = _svc()
    cal, day = await _calendar_with_hours(svc, tenant)
    start = day.replace(hour=10, minute=0)
    await svc.book(tenant, cal.id, S.BookingCreate(
        contact_id="c1", title="A", starts_at=start,
        ends_at=start + timedelta(minutes=30)))
    with pytest.raises(BookingConflictError):
        await svc.book(tenant, cal.id, S.BookingCreate(
            contact_id="c2", title="B", starts_at=start + timedelta(minutes=15),
            ends_at=start + timedelta(minutes=45)))
    # half-open boundary: ends exactly when the next begins is fine
    ok = await svc.book(tenant, cal.id, S.BookingCreate(
        contact_id="c3", title="C", starts_at=start + timedelta(minutes=30),
        ends_at=start + timedelta(minutes=60)))
    assert ok.status == S.BookingStatus.CONFIRMED


# --------------------------------------------------------------- policy gate
async def test_booking_denied_by_policy(tenant: TenantContext) -> None:
    policy = FakePolicyEngine()
    policy.deny_actions.add("scheduling.booking.create")
    svc = _svc(policy)
    cal, day = await _calendar_with_hours(svc, tenant)
    start = day.replace(hour=10, minute=0)
    with pytest.raises(PolicyDeniedError):
        await svc.book(tenant, cal.id, S.BookingCreate(
            contact_id="c1", title="A", starts_at=start,
            ends_at=start + timedelta(minutes=30)))


async def test_mutation_fail_closed_without_policy(tenant: TenantContext) -> None:
    svc = SchedulingService(policy=None, events=_Bus())
    cal, day = await _calendar_with_hours(svc, tenant)
    start = day.replace(hour=10, minute=0)
    with pytest.raises(PolicyDeniedError, match="no policy engine"):
        await svc.book(tenant, cal.id, S.BookingCreate(
            contact_id="c1", title="A", starts_at=start,
            ends_at=start + timedelta(minutes=30)))


async def test_cancel_require_approval_denies(tenant: TenantContext) -> None:
    from app.scheduling.service import PolicyDeniedError as PDE
    policy = FakePolicyEngine()
    svc = _svc(policy)
    cal, day = await _calendar_with_hours(svc, tenant)
    start = day.replace(hour=10, minute=0)
    booking = await svc.book(tenant, cal.id, S.BookingCreate(
        contact_id="c1", title="A", starts_at=start,
        ends_at=start + timedelta(minutes=30)))
    policy.require_approval_actions.add("scheduling.booking.cancel")
    with pytest.raises(PDE, match="human approval required"):
        await svc.cancel(tenant, booking.id, S.BookingCancel(reason="x"))
    # booking untouched while approval is pending
    assert (await svc.get_booking(tenant, booking.id)).status == S.BookingStatus.CONFIRMED


# -------------------------------------------------------------- permissions
async def test_calendar_delete_requires_manager(tenant: TenantContext) -> None:
    from app.core.contracts import TenantContext as TC
    svc = _svc()
    other = TC(tenant_id=tenant.tenant_id, user_id="user-9", roles=())
    cal = await svc.create_calendar(other, S.CalendarCreate(
        name="Private", owner_id="user-9", timezone="UTC"))
    # same-tenant non-owner, non-admin cannot delete
    stranger = TC(tenant_id=tenant.tenant_id, user_id="stranger", roles=())
    with pytest.raises(PermissionDeniedError):
        await svc.delete_calendar(stranger, cal.id)
    # tenant admin can
    await svc.delete_calendar(
        TC(tenant_id=tenant.tenant_id, user_id="admin-1", roles=("admin",)), cal.id)
    # owner can
    cal2 = await svc.create_calendar(other, S.CalendarCreate(
        name="Mine", owner_id="user-9", timezone="UTC"))
    await svc.delete_calendar(other, cal2.id)


async def test_reschedule_only_owner_or_creator(tenant: TenantContext) -> None:
    from app.core.contracts import TenantContext as TC
    svc = _svc()
    creator = TC(tenant_id=tenant.tenant_id, user_id="creator-1", roles=())
    cal, day = await _calendar_with_hours(svc, tenant)
    start = day.replace(hour=10, minute=0)
    booking = await svc.book(creator, cal.id, S.BookingCreate(
        contact_id="c1", title="A", starts_at=start,
        ends_at=start + timedelta(minutes=30)))
    stranger = TC(tenant_id=tenant.tenant_id, user_id="stranger", roles=())
    new_start = day.replace(hour=11, minute=0)
    with pytest.raises(PermissionDeniedError):
        await svc.reschedule(stranger, booking.id, S.BookingReschedule(
            starts_at=new_start, ends_at=new_start + timedelta(minutes=30)))
    # booking creator (non-admin, non-owner) may reschedule their own booking
    moved = await svc.reschedule(creator, booking.id, S.BookingReschedule(
        starts_at=new_start, ends_at=new_start + timedelta(minutes=30)))
    assert moved.starts_at == new_start


# --------------------------------------------------------------- idempotency
async def test_booking_idempotent_replay(tenant: TenantContext) -> None:
    svc = _svc()
    cal, day = await _calendar_with_hours(svc, tenant)
    start = day.replace(hour=10, minute=0)
    data = S.BookingCreate(contact_id="c1", title="A", starts_at=start,
                           ends_at=start + timedelta(minutes=30),
                           idempotency_key="book-1")
    first = await svc.book(tenant, cal.id, data)
    second = await svc.book(tenant, cal.id, data)
    assert first.id == second.id
    assert len(await svc.list_bookings(tenant, cal.id)) == 1


# ---------------------------------------------------------------- reminders
async def test_due_reminders_and_sent_keys(tenant: TenantContext) -> None:
    svc = _svc()
    cal, day = await _calendar_with_hours(svc, tenant)
    start = datetime.now(UTC) + timedelta(minutes=10)
    booking = await svc.book(tenant, cal.id, S.BookingCreate(
        contact_id="c1", title="Soon", starts_at=start,
        ends_at=start + timedelta(minutes=30),
        reminders=[{"offset_minutes": 30, "channel": "email"}]))
    due = await svc.due_reminders(tenant)
    assert [d.booking_id for d in due] == [booking.id]
    await svc.mark_reminder_sent(tenant, booking.id, 30, "email")
    assert await svc.due_reminders(tenant) == []


# ------------------------------------------------------- cross-tenant safety
async def test_cross_tenant_invisibility(tenant: TenantContext,
                                         tenant_b: TenantContext) -> None:
    svc = _svc()
    cal, day = await _calendar_with_hours(svc, tenant)
    assert await svc.list_calendars(tenant_b) == []
    with pytest.raises(CalendarNotFoundError):
        await svc.get_calendar(tenant_b, cal.id)
