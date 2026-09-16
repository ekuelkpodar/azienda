"""Availability computation (pure logic, no I/O).

Given availability rules (weekly windows), existing busy events, and booking
constraints, compute free slots of a requested duration.

Semantics:
- Rules are weekly recurring windows in the calendar's timezone.
- ``min_notice_minutes`` pushes the earliest bookable time forward from now.
- ``buffer_minutes`` pads every busy event on both sides.
- Slots are generated on a 15-minute grid inside each free window; a slot is
  kept only if the full ``[start, start+duration)`` fits inside free time.
- All inputs/outputs are timezone-aware datetimes; naive inputs are assumed UTC
  (callers should pass aware datetimes — the router validates this).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from .schemas import AvailabilityRule, CalendarEvent, TimeSlot

_SLOT_GRID_MINUTES = 15


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """Half-open overlap: [a_start, a_end) vs [b_start, b_end)."""
    return a_start < b_end and b_start < a_end


def _day_windows(rules: list[AvailabilityRule], day: datetime,
                 tz: ZoneInfo) -> list[tuple[datetime, datetime]]:
    weekday = day.weekday()
    windows: list[tuple[datetime, datetime]] = []
    for rule in rules:
        if rule.weekday != weekday:
            continue
        start = datetime.combine(day.date(), rule.start_time, tzinfo=tz)
        end = datetime.combine(day.date(), rule.end_time, tzinfo=tz)
        if end > start:
            windows.append((start, end))
    return windows


def compute_availability(
    rules: list[AvailabilityRule],
    busy: list[CalendarEvent],
    window_start: datetime,
    window_end: datetime,
    duration_minutes: int,
    calendar_tz: str = "UTC",
    buffer_minutes: int = 0,
    min_notice_minutes: int = 0,
    now: datetime | None = None,
) -> list[TimeSlot]:
    """Compute bookable slots. Pure function — fully unit-testable."""
    tz = ZoneInfo(calendar_tz)
    now = _as_utc(now or datetime.now(UTC))
    window_start, window_end = _as_utc(window_start), _as_utc(window_end)
    earliest = now + timedelta(minutes=min_notice_minutes)
    if earliest > window_start:
        window_start = earliest
    if window_start >= window_end or duration_minutes <= 0:
        return []

    buffered_busy = [
        (_as_utc(e.starts_at) - timedelta(minutes=buffer_minutes),
         _as_utc(e.ends_at) + timedelta(minutes=buffer_minutes))
        for e in busy
    ]
    duration = timedelta(minutes=duration_minutes)
    grid = timedelta(minutes=_SLOT_GRID_MINUTES)

    slots: list[TimeSlot] = []
    day = window_start.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = window_end.astimezone(tz)
    while day <= end_day:
        for w_start, w_end in _day_windows(rules, day, tz):
            seg_start = max(w_start, window_start)
            seg_end = min(w_end, window_end)
            if seg_end - seg_start < duration:
                continue
            # Align to the grid, then walk.
            cursor = seg_start
            remainder = (cursor - day).total_seconds() / 60 % _SLOT_GRID_MINUTES
            if remainder:
                cursor += timedelta(minutes=_SLOT_GRID_MINUTES - remainder)
            while cursor + duration <= seg_end + timedelta(seconds=1):
                slot_end = cursor + duration
                if not any(overlaps(cursor, slot_end, b_s, b_e)
                           for b_s, b_e in buffered_busy):
                    slots.append(TimeSlot(starts_at=cursor, ends_at=slot_end))
                cursor += grid
        day += timedelta(days=1)
    return slots
