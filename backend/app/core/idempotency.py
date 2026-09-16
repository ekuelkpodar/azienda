"""DB-backed idempotency for mutating POSTs (API.md §1).

``Idempotency-Key: <uuid>`` on a mutating POST: the first request executes and
its (status, body) is stored; replays within the TTL return the stored response.
A replay with the SAME key but a DIFFERENT request body is a 409 — it signals a
client bug, not a retry.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import errors
from app.core.config import Settings
from app.core.models import IdempotencyKey


def _utcnow() -> datetime:
    return datetime.now(UTC)


def request_fingerprint(method: str, path: str, body: bytes) -> str:
    h = hashlib.sha256()
    h.update(method.encode())
    h.update(b"\x00")
    h.update(path.encode())
    h.update(b"\x00")
    h.update(body or b"")
    return h.hexdigest()


class IdempotencyStore:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    async def check(self, tenant_id: str, key: str, method: str, path: str,
                    body: bytes) -> tuple[int, dict[str, Any]] | None:
        """Return the stored (status, body) on replay, else None.

        Raises IdempotencyConflict if the key was used with a different payload.
        """
        now = _utcnow()
        # opportunistic expiry cleanup
        await self.db.execute(
            delete(IdempotencyKey).where(IdempotencyKey.expires_at < now))
        row = (await self.db.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.tenant_id == tenant_id,
                IdempotencyKey.key == key))).scalar_one_or_none()
        if row is None:
            return None
        if row.request_hash != request_fingerprint(method, path, body):
            raise errors.ConflictError(
                "Idempotency-Key was already used with a different request body",
                details={"code": errors.ErrorCode.idempotency_conflict.value})
        return row.status_code, row.response_body

    async def store(self, tenant_id: str, key: str, method: str, path: str,
                    body: bytes, status_code: int, response_body: dict[str, Any]) -> None:
        # ensure JSON-serializable
        safe_body = json.loads(json.dumps(response_body, default=str))
        row = IdempotencyKey(
            tenant_id=tenant_id, key=key, method=method, path=path,
            request_hash=request_fingerprint(method, path, body),
            status_code=status_code, response_body=safe_body,
            expires_at=_utcnow() + timedelta(hours=self.settings.idempotency_ttl_hours))
        self.db.add(row)
        # commit left to the request lifecycle; flush so replays in-flight see it
        await self.db.flush()

    async def acquire(self, tenant_id: str, key: str, method: str, path: str,
                      body: bytes) -> tuple[int, dict[str, Any]] | str:
        """Check-then-reserve. Returns stored response on replay, else the key.

        Callers: ``result = await store.acquire(...); if isinstance(result, tuple):
        return replay``.
        """
        existing = await self.check(tenant_id, key, method, path, body)
        if existing is not None:
            return existing
        return key
