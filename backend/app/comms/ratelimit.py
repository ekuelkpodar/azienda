"""Rate limiting for outbound sends.

``RateLimiter`` is the protocol the service depends on. ``InMemoryRateLimiter``
is a fixed-window counter per (tenant, channel kind). It is process-local:
in a multi-replica deployment replace it with a Redis-backed implementation
(ADR-003 reserves Redis for exactly this kind of ephemeral state).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class RateLimitedError(Exception):
    """Raised when a send would exceed the configured rate limit."""

    def __init__(self, kind: str, limit: int, window_seconds: int, retry_after_seconds: float):
        self.kind = kind
        self.limit = limit
        self.window_seconds = window_seconds
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"rate limit exceeded for {kind}: {limit} sends per {window_seconds}s; "
            f"retry after {retry_after_seconds:.1f}s"
        )


@runtime_checkable
class RateLimiter(Protocol):
    async def check(self, tenant_id: str, kind: str) -> None:
        """Raise RateLimitedError if the send must not proceed; otherwise record it."""
        ...


@dataclass
class InMemoryRateLimiter:
    """Fixed-window rate limiter. Process-local — see module docstring."""

    limit_per_minute: int = 60

    def __init__(self, limit_per_minute: int = 60, window_seconds: int = 60) -> None:
        self.limit_per_minute = limit_per_minute
        self.window_seconds = window_seconds
        self._counts: dict[tuple[str, str, int], int] = {}

    async def check(self, tenant_id: str, kind: str) -> None:
        now = time.time()
        window = int(now // self.window_seconds)
        key = (tenant_id, kind, window)
        used = self._counts.get(key, 0)
        if used >= self.limit_per_minute:
            retry_after = (window + 1) * self.window_seconds - now
            raise RateLimitedError(kind, self.limit_per_minute, self.window_seconds, retry_after)
        self._counts[key] = used + 1
        # Opportunistic cleanup of windows older than the current one.
        for old_key in [k for k in self._counts if k[2] < window]:
            del self._counts[old_key]
