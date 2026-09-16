# scheduling — calendars, availability, governed booking

**Status:** implemented (MVP). In-memory repository backs tests; Postgres
persistence is modeled (`models.py` + Alembic migration) and lands when the
core builder's `core/db` session factory exists.

## Boundary
Calendars, availability rules, events (busy time), bookings with conflict
detection, reminders. External calendar sync is a provider interface
(`CalendarSyncProvider`) — Google/Outlook adapters are documented future,
not wired.

## Owned tables
`calendars`, `calendar_events`, `bookings`, `availability_rules`.

## Public interfaces
- `SchedulingService` — calendar CRUD, availability rules, availability
  computation, `book` (idempotent, conflict-checked), `reschedule`, `cancel`,
  `due_reminders` + `mark_reminder_sent` (ARQ-worker pattern).
- `compute_availability` (`availability.py`) — pure function: weekly rules +
  busy events + buffer + min-notice → 15-minute-grid slots.
- `CalendarSyncProvider` (`sync.py`) — external sync seam.

## Permission model (binding — never bypassed)
- Calendar management: calendar owner or tenant admin (`admin`/`owner` role).
- Booking a free slot: any authenticated tenant principal.
- Reschedule/cancel: calendar owner/admin, or the booking's creator.
- book/reschedule/cancel additionally pass the policy engine BEFORE execution
  (fail closed when unconfigured).

## Consumes
`core` (contracts only). Emits: `scheduling.calendar.created/deleted`,
`scheduling.booking.created/rescheduled/cancelled`, `scheduling.reminder.due`.
Reminder *delivery* goes through `comms/` (the worker sends via `CommsPort`).

## Rules for builders
1. Import other packages ONLY through `core/contracts.py` protocols or a
   package's public interface.
2. `tenant_id` on every row; every query filters by it.
3. Never bypass calendar permissions — ownership checks live in the service,
   not the router.
