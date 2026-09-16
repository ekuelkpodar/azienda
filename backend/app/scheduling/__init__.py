"""Package: scheduling — calendars, availability, governed booking.

Public interface (other packages/agents import ONLY these):
  - ``SchedulingService`` — calendar/rule/event/booking CRUD, availability
    computation, policy-gated book/reschedule/cancel, reminders.
  - ``SchedulingRepository`` / ``InMemorySchedulingRepository`` — persistence seam.
  - ``CalendarSyncProvider`` — external calendar sync seam (future adapters).
  - ``compute_availability`` / ``overlaps`` — pure scheduling logic.
  - schemas (``Calendar``, ``Booking``, ``TimeSlot``, ...).

See README.md for the boundary contract.
"""
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
from .service import (
    BookingConflictError,
    BookingNotFoundError,
    BookingStateError,
    CalendarNotFoundError,
    PermissionDeniedError,
    PolicyDeniedError,
    SchedulingError,
    SchedulingService,
)
from .sync import CalendarSyncProvider

__all__ = [
    "AvailabilityQuery",
    "AvailabilityRule",
    "AvailabilityRuleCreate",
    "Booking",
    "BookingCancel",
    "BookingConflictError",
    "BookingCreate",
    "BookingNotFoundError",
    "BookingReschedule",
    "BookingStateError",
    "Calendar",
    "CalendarCreate",
    "CalendarEvent",
    "CalendarEventCreate",
    "CalendarNotFoundError",
    "CalendarSyncProvider",
    "CalendarUpdate",
    "InMemorySchedulingRepository",
    "PermissionDeniedError",
    "PolicyDeniedError",
    "ReminderDue",
    "SchedulingError",
    "SchedulingRepository",
    "SchedulingService",
    "BookingStatus",
    "TimeSlot",
    "compute_availability",
    "overlaps",
]
