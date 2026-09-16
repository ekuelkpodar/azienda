"""Repository protocols + in-memory implementations for scheduling.

Persistence seam: ``SchedulingRepository``. ``InMemorySchedulingRepository``
backs tests and local dev; Postgres SQLAlchemy implementation (``models.py``)
is future work pending ``core/db``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from .schemas import AvailabilityRule, Booking, Calendar, CalendarEvent


@runtime_checkable
class SchedulingRepository(Protocol):
    # calendars
    async def add_calendar(self, calendar: Calendar) -> Calendar: ...
    async def get_calendar(self, tenant_id: str, calendar_id: str) -> Calendar | None: ...
    async def update_calendar(self, calendar: Calendar) -> Calendar: ...
    async def delete_calendar(self, tenant_id: str, calendar_id: str) -> None: ...
    async def list_calendars(self, tenant_id: str) -> list[Calendar]: ...
    # availability rules
    async def add_rule(self, rule: AvailabilityRule) -> AvailabilityRule: ...
    async def list_rules(self, tenant_id: str, calendar_id: str) -> list[AvailabilityRule]: ...
    async def delete_rules(self, tenant_id: str, calendar_id: str) -> None: ...
    # events
    async def add_event(self, event: CalendarEvent) -> CalendarEvent: ...
    async def get_event(self, tenant_id: str, event_id: str) -> CalendarEvent | None: ...
    async def update_event(self, event: CalendarEvent) -> CalendarEvent: ...
    async def delete_event(self, tenant_id: str, event_id: str) -> None: ...
    async def list_events(self, tenant_id: str, calendar_id: str,
                          start: datetime, end: datetime) -> list[CalendarEvent]: ...
    # bookings
    async def add_booking(self, booking: Booking) -> Booking: ...
    async def get_booking(self, tenant_id: str, booking_id: str) -> Booking | None: ...
    async def update_booking(self, booking: Booking) -> Booking: ...
    async def list_bookings(self, tenant_id: str, calendar_id: str | None = None,
                            start: datetime | None = None,
                            end: datetime | None = None) -> list[Booking]: ...
    async def get_booking_by_idempotency(self, tenant_id: str, key: str) -> Booking | None: ...
    async def register_booking_idempotency(self, tenant_id: str, key: str,
                                           booking_id: str) -> None: ...


class InMemorySchedulingRepository:
    """Process-local repository. Tenant isolation enforced on every access."""

    def __init__(self) -> None:
        self._calendars: dict[tuple[str, str], Calendar] = {}
        self._rules: dict[tuple[str, str], AvailabilityRule] = {}
        self._events: dict[tuple[str, str], CalendarEvent] = {}
        self._bookings: dict[tuple[str, str], Booking] = {}
        self._booking_idem: dict[tuple[str, str], str] = {}

    async def add_calendar(self, calendar: Calendar) -> Calendar:
        self._calendars[(calendar.tenant_id, calendar.id)] = calendar
        return calendar

    async def get_calendar(self, tenant_id: str, calendar_id: str) -> Calendar | None:
        return self._calendars.get((tenant_id, calendar_id))

    async def update_calendar(self, calendar: Calendar) -> Calendar:
        self._calendars[(calendar.tenant_id, calendar.id)] = calendar
        return calendar

    async def delete_calendar(self, tenant_id: str, calendar_id: str) -> None:
        self._calendars.pop((tenant_id, calendar_id), None)

    async def list_calendars(self, tenant_id: str) -> list[Calendar]:
        return [c for (t, _), c in self._calendars.items() if t == tenant_id]

    async def add_rule(self, rule: AvailabilityRule) -> AvailabilityRule:
        self._rules[(rule.tenant_id, rule.id)] = rule
        return rule

    async def list_rules(self, tenant_id: str, calendar_id: str) -> list[AvailabilityRule]:
        return [r for (t, _), r in self._rules.items()
                if t == tenant_id and r.calendar_id == calendar_id]

    async def delete_rules(self, tenant_id: str, calendar_id: str) -> None:
        for key in [k for k, r in self._rules.items()
                    if k[0] == tenant_id and r.calendar_id == calendar_id]:
            del self._rules[key]

    async def add_event(self, event: CalendarEvent) -> CalendarEvent:
        self._events[(event.tenant_id, event.id)] = event
        return event

    async def get_event(self, tenant_id: str, event_id: str) -> CalendarEvent | None:
        return self._events.get((tenant_id, event_id))

    async def update_event(self, event: CalendarEvent) -> CalendarEvent:
        self._events[(event.tenant_id, event.id)] = event
        return event

    async def delete_event(self, tenant_id: str, event_id: str) -> None:
        self._events.pop((tenant_id, event_id), None)

    async def list_events(self, tenant_id: str, calendar_id: str,
                          start: datetime, end: datetime) -> list[CalendarEvent]:
        return [e for (t, _), e in self._events.items()
                if t == tenant_id and e.calendar_id == calendar_id
                and e.starts_at < end and e.ends_at > start]

    async def add_booking(self, booking: Booking) -> Booking:
        self._bookings[(booking.tenant_id, booking.id)] = booking
        return booking

    async def get_booking(self, tenant_id: str, booking_id: str) -> Booking | None:
        return self._bookings.get((tenant_id, booking_id))

    async def update_booking(self, booking: Booking) -> Booking:
        self._bookings[(booking.tenant_id, booking.id)] = booking
        return booking

    async def list_bookings(self, tenant_id: str, calendar_id: str | None = None,
                            start: datetime | None = None,
                            end: datetime | None = None) -> list[Booking]:
        items = [
            b for (t, _), b in self._bookings.items()
            if t == tenant_id
            and (calendar_id is None or b.calendar_id == calendar_id)
            and (start is None or b.ends_at > start)
            and (end is None or b.starts_at < end)
        ]
        items.sort(key=lambda b: b.starts_at)
        return items

    async def get_booking_by_idempotency(self, tenant_id: str, key: str) -> Booking | None:
        booking_id = self._booking_idem.get((tenant_id, key))
        return self._bookings.get((tenant_id, booking_id)) if booking_id else None

    async def register_booking_idempotency(self, tenant_id: str, key: str,
                                           booking_id: str) -> None:
        self._booking_idem[(tenant_id, key)] = booking_id
