"""Timezone helpers.

Postgres ``timestamptz`` columns come back timezone-aware; SQLite
``DateTime(timezone=True)`` columns come back **naive** (SQLite stores no tz
info). All Python-side comparisons against "now" must normalize the DB value
with :func:`as_utc` first, otherwise SQLite raises
``TypeError: can't compare offset-naive and offset-aware datetimes``.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import overload


def utcnow() -> datetime:
    return datetime.now(UTC)


@overload
def as_utc(value: None) -> None: ...
@overload
def as_utc(value: datetime) -> datetime: ...
@overload
def as_utc(value: datetime | None) -> datetime | None: ...
def as_utc(value: datetime | None) -> datetime | None:
    """Attach UTC to a naive datetime; pass aware values (and None) through."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
