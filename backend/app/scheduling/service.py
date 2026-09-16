"""SchedulingService — calendars, availability, governed booking.

Permission model (binding — never bypassed):
- Calendar management (create/update/delete/rules/events): the calendar owner
  (``calendar.owner_id == tenant.user_id``) or a tenant admin
  (``"admin" in tenant.roles`` or ``"owner" in tenant.roles``).
- Booking a free slot: any authenticated tenant principal.
- Reschedule/cancel a booking: the calendar owner/admin, or the principal who
  created the booking (``booking.created_by == tenant.user_id``).

Customer-visible mutations (book/reschedule/cancel) additionally pass the
policy engine BEFORE execution; fail closed when no engine is configured.

Conflict detection is half-open overlap ``[start, end)`` against calendar
events and confirmed bookings. Reminders are computed by ``due_reminders``
(designed for an ARQ worker) and emitted as ``scheduling.reminder.due``
events; actual delivery goes through the comms package.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.contracts import (
    ActionRequest,
    DomainEvent,
    EventBus,
    PolicyDecision,
    PolicyEffect,
    PolicyEngine,
    TenantContext,
)

from .availability import compute_availability, overlaps
from .repository import InMemorySchedulingRepository, SchedulingRepository
from .schemas import (
    AvailabilityQuery,
    AvailabilityRule,
    AvailabilityRuleCreate,
    Booking,
    BookingCancel,
    BookingCreate,
    BookingReschedule,
    BookingStatus,
    Calendar,
    CalendarCreate,
    CalendarEvent,
    CalendarEventCreate,
    CalendarUpdate,
    ReminderDue,
    TimeSlot,
)
from .sync import CalendarSyncProvider


# ------------------------------------------------------------------ errors
class SchedulingError(Exception):
    """Base for scheduling domain errors."""


class CalendarNotFoundError(SchedulingError):
    pass


class BookingNotFoundError(SchedulingError):
    pass


class BookingConflictError(SchedulingError):
    """Requested time overlaps existing events/bookings (409)."""


class PermissionDeniedError(SchedulingError):
    """Calendar permission check failed — never bypassed (403)."""


class BookingStateError(SchedulingError):
    """Illegal operation for the booking's status (409)."""


class PolicyDeniedError(SchedulingError):
    def __init__(self, reasons: tuple[str, ...]):
        self.reasons = reasons
        super().__init__(f"scheduling action denied by policy: {'; '.join(reasons) or 'no reason'}")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


_ADMIN_ROLES = frozenset({"admin", "owner"})


class SchedulingService:
    def __init__(
        self,
        repo: SchedulingRepository | None = None,
        policy: PolicyEngine | None = None,
        events: EventBus | None = None,
        sync_providers: dict[str, CalendarSyncProvider] | None = None,
    ) -> None:
        self._repo = repo or InMemorySchedulingRepository()
        self._policy = policy
        self._events = events
        self._sync_providers = sync_providers or {}

    # ------------------------------------------------------------ calendars
    async def create_calendar(self, tenant: TenantContext, data: CalendarCreate) -> Calendar:
        self._validate_timezone(data.timezone)
        calendar = Calendar(
            id=_new_id(), tenant_id=tenant.tenant_id, name=data.name,
            owner_id=data.owner_id or tenant.user_id, timezone=data.timezone,
            created_at=_utcnow())
        calendar = await self._repo.add_calendar(calendar)
        await self._emit(tenant, "scheduling.calendar.created", calendar.id,
                         {"calendar_id": calendar.id, "name": calendar.name})
        return calendar

    async def get_calendar(self, tenant: TenantContext, calendar_id: str) -> Calendar:
        calendar = await self._repo.get_calendar(tenant.tenant_id, calendar_id)
        if calendar is None:
            raise CalendarNotFoundError(calendar_id)
        return calendar

    async def list_calendars(self, tenant: TenantContext) -> list[Calendar]:
        return await self._repo.list_calendars(tenant.tenant_id)

    async def update_calendar(
        self, tenant: TenantContext, calendar_id: str, data: CalendarUpdate
    ) -> Calendar:
        calendar = await self.get_calendar(tenant, calendar_id)
        self._require_manager(tenant, calendar)
        patch = data.model_dump(exclude_unset=True)
        if "timezone" in patch:
            self._validate_timezone(patch["timezone"])
        return await self._repo.update_calendar(calendar.model_copy(update=patch))

    async def delete_calendar(self, tenant: TenantContext, calendar_id: str) -> None:
        calendar = await self.get_calendar(tenant, calendar_id)
        self._require_manager(tenant, calendar)
        await self._enforce_policy(
            tenant, action="scheduling.calendar.delete",
            resource=f"calendar:{calendar_id}", args={"calendar_id": calendar_id},
            risk_context={"reversible": False, "destructive": True})
        await self._repo.delete_rules(tenant.tenant_id, calendar_id)
        await self._repo.delete_calendar(tenant.tenant_id, calendar_id)
        await self._emit(tenant, "scheduling.calendar.deleted", calendar_id,
                         {"calendar_id": calendar_id})

    @staticmethod
    def _validate_timezone(tz: str) -> None:
        try:
            ZoneInfo(tz)
        except ZoneInfoNotFoundError as exc:
            raise SchedulingError(f"unknown timezone: {tz!r}") from exc

    # ------------------------------------------------------------ permissions
    @staticmethod
    def _is_manager(tenant: TenantContext, calendar: Calendar) -> bool:
        if _ADMIN_ROLES.intersection(tenant.roles):
            return True
        return bool(calendar.owner_id and tenant.user_id == calendar.owner_id)

    def _require_manager(self, tenant: TenantContext, calendar: Calendar) -> None:
        if not self._is_manager(tenant, calendar):
            raise PermissionDeniedError(
                "calendar management requires the calendar owner or a tenant admin")

    def _require_booking_actor(self, tenant: TenantContext, calendar: Calendar,
                               booking: Booking) -> None:
        if self._is_manager(tenant, calendar):
            return
        if booking.created_by and tenant.user_id == booking.created_by:
            return
        raise PermissionDeniedError(
            "only the calendar owner/admin or the booking creator may modify this booking")

    # ------------------------------------------------------------ availability rules
    async def set_availability_rule(
        self, tenant: TenantContext, calendar_id: str, data: AvailabilityRuleCreate
    ) -> AvailabilityRule:
        calendar = await self.get_calendar(tenant, calendar_id)
        self._require_manager(tenant, calendar)
        if data.end_time <= data.start_time:
            raise SchedulingError("availability rule end_time must be after start_time")
        rule = AvailabilityRule(
            id=_new_id(), tenant_id=tenant.tenant_id, calendar_id=calendar_id,
            weekday=data.weekday, start_time=data.start_time, end_time=data.end_time,
            created_at=_utcnow())
        return await self._repo.add_rule(rule)

    async def list_availability_rules(
        self, tenant: TenantContext, calendar_id: str
    ) -> list[AvailabilityRule]:
        await self.get_calendar(tenant, calendar_id)
        return await self._repo.list_rules(tenant.tenant_id, calendar_id)

    # ------------------------------------------------------------ events (busy time)
    async def create_event(
        self, tenant: TenantContext, calendar_id: str, data: CalendarEventCreate
    ) -> CalendarEvent:
        calendar = await self.get_calendar(tenant, calendar_id)
        self._require_manager(tenant, calendar)
        starts_at, ends_at = _as_utc(data.starts_at), _as_utc(data.ends_at)
        if ends_at <= starts_at:
            raise SchedulingError("event ends_at must be after starts_at")
        event = CalendarEvent(
            id=_new_id(), tenant_id=tenant.tenant_id, calendar_id=calendar_id,
            title=data.title, starts_at=starts_at, ends_at=ends_at,
            location=data.location, metadata=dict(data.metadata), created_at=_utcnow())
        return await self._repo.add_event(event)

    # ------------------------------------------------------------ availability
    async def availability(
        self, tenant: TenantContext, calendar_id: str, query: AvailabilityQuery,
        now: datetime | None = None,
    ) -> list[TimeSlot]:
        calendar = await self.get_calendar(tenant, calendar_id)
        rules = await self._repo.list_rules(tenant.tenant_id, calendar_id)
        busy = await self._repo.list_events(
            tenant.tenant_id, calendar_id, _as_utc(query.start), _as_utc(query.end))
        # Confirmed bookings block availability too.
        for booking in await self._repo.list_bookings(
                tenant.tenant_id, calendar_id, _as_utc(query.start), _as_utc(query.end)):
            if booking.status == BookingStatus.CONFIRMED:
                busy.append(CalendarEvent(
                    id=booking.event_id, tenant_id=tenant.tenant_id,
                    calendar_id=calendar_id, title=booking.title,
                    starts_at=booking.starts_at, ends_at=booking.ends_at,
                    created_at=booking.created_at))
        return compute_availability(
            rules, busy, query.start, query.end, query.duration_minutes,
            calendar_tz=calendar.timezone, buffer_minutes=query.buffer_minutes,
            min_notice_minutes=query.min_notice_minutes, now=now)

    # ------------------------------------------------------------ booking
    async def book(
        self, tenant: TenantContext, calendar_id: str, data: BookingCreate
    ) -> Booking:
        await self.get_calendar(tenant, calendar_id)  # validates existence + tenant
        if data.idempotency_key:
            existing = await self._repo.get_booking_by_idempotency(
                tenant.tenant_id, data.idempotency_key)
            if existing is not None:
                return existing
        starts_at, ends_at = _as_utc(data.starts_at), _as_utc(data.ends_at)
        if ends_at <= starts_at:
            raise SchedulingError("booking ends_at must be after starts_at")

        await self._enforce_policy(
            tenant, action="scheduling.booking.create",
            resource=f"calendar:{calendar_id}",
            args={"calendar_id": calendar_id, "starts_at": starts_at.isoformat(),
                  "ends_at": ends_at.isoformat()},
            risk_context={"externally_visible": True, "reversible": True})

        await self._assert_no_conflict(tenant, calendar_id, starts_at, ends_at,
                                       exclude_event_id=None)

        event = await self._repo.add_event(CalendarEvent(
            id=_new_id(), tenant_id=tenant.tenant_id, calendar_id=calendar_id,
            title=data.title, starts_at=starts_at, ends_at=ends_at,
            metadata={"booking": True}, created_at=_utcnow()))
        booking = Booking(
            id=_new_id(), tenant_id=tenant.tenant_id, calendar_id=calendar_id,
            event_id=event.id, contact_id=data.contact_id, title=data.title,
            starts_at=starts_at, ends_at=ends_at, status=BookingStatus.CONFIRMED,
            reminders=list(data.reminders), created_by=tenant.user_id,
            created_at=_utcnow())
        booking = await self._repo.add_booking(booking)
        if data.idempotency_key:
            await self._repo.register_booking_idempotency(
                tenant.tenant_id, data.idempotency_key, booking.id)
        await self._emit(tenant, "scheduling.booking.created", booking.id, {
            "booking_id": booking.id, "calendar_id": calendar_id,
            "starts_at": starts_at.isoformat(), "ends_at": ends_at.isoformat(),
            "contact_id": data.contact_id})
        return booking

    async def _assert_no_conflict(self, tenant: TenantContext, calendar_id: str,
                                  starts_at: datetime, ends_at: datetime,
                                  exclude_event_id: str | None) -> None:
        busy = await self._repo.list_events(tenant.tenant_id, calendar_id,
                                            starts_at, ends_at)
        for event in busy:
            if exclude_event_id and event.id == exclude_event_id:
                continue
            if overlaps(starts_at, ends_at, _as_utc(event.starts_at),
                        _as_utc(event.ends_at)):
                raise BookingConflictError(
                    f"conflicts with '{event.title}' "
                    f"({event.starts_at.isoformat()}–{event.ends_at.isoformat()})")

    async def get_booking(self, tenant: TenantContext, booking_id: str) -> Booking:
        booking = await self._repo.get_booking(tenant.tenant_id, booking_id)
        if booking is None:
            raise BookingNotFoundError(booking_id)
        return booking

    async def list_bookings(self, tenant: TenantContext, calendar_id: str | None = None,
                            start: datetime | None = None,
                            end: datetime | None = None) -> list[Booking]:
        return await self._repo.list_bookings(tenant.tenant_id, calendar_id, start, end)

    async def reschedule(
        self, tenant: TenantContext, booking_id: str, data: BookingReschedule
    ) -> Booking:
        booking = await self.get_booking(tenant, booking_id)
        calendar = await self.get_calendar(tenant, booking.calendar_id)
        if booking.status != BookingStatus.CONFIRMED:
            raise BookingStateError(
                f"only confirmed bookings can be rescheduled (now {booking.status.value})")
        self._require_booking_actor(tenant, calendar, booking)
        starts_at, ends_at = _as_utc(data.starts_at), _as_utc(data.ends_at)
        if ends_at <= starts_at:
            raise SchedulingError("booking ends_at must be after starts_at")

        await self._enforce_policy(
            tenant, action="scheduling.booking.reschedule",
            resource=f"booking:{booking_id}",
            args={"booking_id": booking_id, "starts_at": starts_at.isoformat(),
                  "ends_at": ends_at.isoformat()},
            risk_context={"externally_visible": True, "reversible": True})

        await self._assert_no_conflict(tenant, booking.calendar_id, starts_at, ends_at,
                                       exclude_event_id=booking.event_id)
        event = await self._repo.get_event(tenant.tenant_id, booking.event_id)
        if event:
            await self._repo.update_event(event.model_copy(
                update={"starts_at": starts_at, "ends_at": ends_at}))
        booking = await self._repo.update_booking(booking.model_copy(update={
            "starts_at": starts_at, "ends_at": ends_at, "reminders_sent": []}))
        await self._emit(tenant, "scheduling.booking.rescheduled", booking.id, {
            "booking_id": booking.id, "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat()})
        return booking

    async def cancel(
        self, tenant: TenantContext, booking_id: str, data: BookingCancel
    ) -> Booking:
        booking = await self.get_booking(tenant, booking_id)
        calendar = await self.get_calendar(tenant, booking.calendar_id)
        if booking.status != BookingStatus.CONFIRMED:
            raise BookingStateError(
                f"only confirmed bookings can be cancelled (now {booking.status.value})")
        self._require_booking_actor(tenant, calendar, booking)

        await self._enforce_policy(
            tenant, action="scheduling.booking.cancel",
            resource=f"booking:{booking_id}", args={"booking_id": booking_id},
            risk_context={"externally_visible": True, "reversible": False})

        event = await self._repo.get_event(tenant.tenant_id, booking.event_id)
        if event:
            await self._repo.delete_event(tenant.tenant_id, event.id)
        booking = await self._repo.update_booking(booking.model_copy(update={
            "status": BookingStatus.CANCELLED, "cancelled_at": _utcnow(),
            "cancel_reason": data.reason}))
        await self._emit(tenant, "scheduling.booking.cancelled", booking.id, {
            "booking_id": booking.id, "reason": data.reason})
        return booking

    # ------------------------------------------------------------ reminders
    async def due_reminders(
        self, tenant: TenantContext, now: datetime | None = None
    ) -> list[ReminderDue]:
        """Reminders whose time has come and that have not been sent yet.

        Designed for an ARQ worker: it dispatches each due reminder through
        comms and calls ``mark_reminder_sent``. Emits ``scheduling.reminder.due``.
        """
        now = _as_utc(now or _utcnow())
        due: list[ReminderDue] = []
        for booking in await self._repo.list_bookings(tenant.tenant_id):
            if booking.status != BookingStatus.CONFIRMED or booking.starts_at <= now:
                continue
            for reminder in booking.reminders:
                offset = int(reminder.get("offset_minutes", 0))
                channel = str(reminder.get("channel", "email"))
                key = f"{offset}:{channel}"
                if key in booking.reminders_sent:
                    continue
                remind_at = booking.starts_at - timedelta(minutes=offset)
                if remind_at <= now:
                    due.append(ReminderDue(
                        booking_id=booking.id, calendar_id=booking.calendar_id,
                        contact_id=booking.contact_id, title=booking.title,
                        starts_at=booking.starts_at, remind_at=remind_at,
                        channel=channel, offset_minutes=offset))
                    await self._emit(tenant, "scheduling.reminder.due", booking.id, {
                        "booking_id": booking.id, "remind_at": remind_at.isoformat(),
                        "channel": channel, "offset_minutes": offset})
        return due

    async def mark_reminder_sent(self, tenant: TenantContext, booking_id: str,
                                 offset_minutes: int, channel: str) -> Booking:
        booking = await self.get_booking(tenant, booking_id)
        key = f"{offset_minutes}:{channel}"
        if key not in booking.reminders_sent:
            booking = await self._repo.update_booking(booking.model_copy(
                update={"reminders_sent": [*booking.reminders_sent, key]}))
        return booking

    # ------------------------------------------------------------ policy + events
    async def _enforce_policy(self, tenant: TenantContext, *, action: str,
                              resource: str | None, args: dict[str, Any],
                              risk_context: dict[str, Any]) -> PolicyDecision:
        if self._policy is None:
            raise PolicyDeniedError(("no policy engine configured — refusing mutation",))
        try:
            decision = await self._policy.evaluate(ActionRequest(
                tenant=tenant, action=action, resource=resource, args=args,
                risk_context=risk_context))
        except Exception as exc:
            raise PolicyDeniedError((f"policy evaluation failed: {exc}",)) from exc
        if decision.effect == PolicyEffect.DENY:
            raise PolicyDeniedError(decision.reasons)
        if decision.effect == PolicyEffect.REQUIRE_APPROVAL:
            raise PolicyDeniedError(
                ("human approval required for this scheduling action",))
        return decision

    async def _emit(self, tenant: TenantContext, topic: str, aggregate_id: str,
                    payload: dict[str, Any]) -> None:
        if self._events is None:
            return
        await self._events.publish(DomainEvent(
            topic=topic, tenant_id=tenant.tenant_id, aggregate_id=aggregate_id,
            payload=payload, event_id=_new_id(), occurred_at=_utcnow()))
