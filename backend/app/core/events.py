"""In-process asyncio EventBus implementing ``core.contracts.EventBus``.

Transport only — the Postgres ledger is truth (ADR-007). Handlers run as
asyncio tasks; a handler exception is logged, never propagated to the publisher.
Redis Streams is the documented step-up when a single process is insufficient.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.core.contracts import DomainEvent, EventBus

log = logging.getLogger(__name__)


def new_event(topic: str, tenant_id: str, aggregate_id: str,
              payload: dict[str, Any], causation_id: str | None = None,
              correlation_id: str | None = None) -> DomainEvent:
    return DomainEvent(
        topic=topic, tenant_id=tenant_id, aggregate_id=aggregate_id,
        payload=payload, event_id=uuid.uuid4().hex,
        occurred_at=datetime.now(UTC),
        causation_id=causation_id, correlation_id=correlation_id)


class InProcessEventBus(EventBus):
    """Asyncio pub/sub inside one process. Not durable by design."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[[DomainEvent], Awaitable[None]]]] = \
            defaultdict(list)

    def subscribe(self, topic: str,
                  handler: Callable[[DomainEvent], Awaitable[None]]) -> None:
        self._handlers[topic].append(handler)

    def unsubscribe(self, topic: str,
                    handler: Callable[[DomainEvent], Awaitable[None]]) -> None:
        handlers = self._handlers.get(topic, [])
        if handler in handlers:
            handlers.remove(handler)

    async def publish(self, event: DomainEvent) -> None:
        handlers = list(self._handlers.get(event.topic, []))
        if not handlers:
            return
        results = await asyncio.gather(
            *(h(event) for h in handlers), return_exceptions=True)
        for handler, result in zip(handlers, results, strict=True):
            if isinstance(result, Exception):
                log.exception("EventBus handler failed",
                              extra={"topic": event.topic,
                                     "handler": getattr(handler, "__name__", repr(handler)),
                                     "event_id": event.event_id})


# Process-wide default bus. The app lifespan may replace it (e.g. Redis Streams).
default_bus = InProcessEventBus()
