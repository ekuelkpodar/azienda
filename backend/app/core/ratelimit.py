"""Rate limiting: in-process token bucket (MVP), Redis step-up documented.

Interface-first: ``RateLimiterBackend`` is what the dependency uses. The MVP
ships ``InMemoryRateLimiter``; a Redis backend can be dropped in without
changing callers (ADR-003: Redis is ephemeral-only).
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: float = 0.0


@runtime_checkable
class RateLimiterBackend(Protocol):
    async def check(self, key: str, limit: int, window_seconds: int) -> RateLimitResult: ...


class InMemoryRateLimiter(RateLimiterBackend):
    """Sliding-window counter. Per-process: correct behind one replica, and the
    documented step-up is a Redis backend when the API tier scales out."""

    def __init__(self, max_keys: int = 100_000) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._max_keys = max_keys

    async def check(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        now = time.monotonic()
        cutoff = now - window_seconds
        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= self._max_keys:
                # fail open on saturation would be a DoS vector; fail closed-ish:
                # evict oldest key (documented behavior)
                oldest = min(self._hits, key=lambda k: self._hits[k][0] if self._hits[k] else now)
                del self._hits[oldest]
            hits = self._hits[key] = deque()
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= limit:
            retry_after = max(0.0, hits[0] + window_seconds - now)
            return RateLimitResult(False, limit, 0, retry_after)
        hits.append(now)
        return RateLimitResult(True, limit, limit - len(hits))


def rate_limit_key(tenant_id: str, subject: str, route: str) -> str:
    return f"rl:{tenant_id}:{subject}:{route}"
