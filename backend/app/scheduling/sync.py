"""External calendar sync provider interface.

Syncing with Google Calendar / Outlook is a documented future: an adapter
implements ``CalendarSyncProvider`` and the service pulls/pushes through it.
No vendor SDKs are wired at MVP — this file is the seam, not a stub pretending
to sync.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from .schemas import CalendarEvent


@runtime_checkable
class CalendarSyncProvider(Protocol):
    """Bidirectional sync with an external calendar system."""

    @property
    def name(self) -> str: ...

    async def list_remote_events(self, calendar_ref: str, start: datetime,
                                 end: datetime) -> list[CalendarEvent]: ...
    """Read busy time from the external calendar (for conflict detection)."""

    async def push_event(self, calendar_ref: str, event: CalendarEvent) -> str:
        """Create/update the event remotely; returns the remote event id."""
        ...


# Documented future adapters (do NOT implement without an ADR + credentials):
#   - GoogleCalendarProvider: OAuth2 via SecretBroker refs; uses the Google
#     Calendar API v3 events.list/watch. Respects tenant data-residency policy.
#   - OutlookCalendarProvider: Microsoft Graph /me/calendars with delegated
#     OAuth2. Same credential discipline.
