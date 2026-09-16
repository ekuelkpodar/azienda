"""Hash-chained append-only audit ledger.

``hash = sha256(prev_hash || seq || canonical_json(payload) || actor || action)``
with ``prev_hash = GENESIS`` for the first entry of a tenant. Verification
recomputes every link; any tampering breaks the chain at exactly one point.

Append-only is enforced in two layers: the application never issues
UPDATE/DELETE, and the migration revokes them from the app role on Postgres.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.contracts import AuditEntry, AuditLedger, TenantContext
from app.governance.dlp import sanitize_audit_payload
from app.governance.models import AuditEntry as AuditRow

GENESIS_HASH = "GENESIS"


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      default=str, ensure_ascii=False)


def chain_hash(prev_hash: str, seq: int, actor: str, action: str,
               payload: dict[str, Any]) -> str:
    h = hashlib.sha256()
    h.update(prev_hash.encode("utf-8"))
    h.update(b"\x00")
    h.update(str(seq).encode("utf-8"))
    h.update(b"\x00")
    h.update(actor.encode("utf-8"))
    h.update(b"\x00")
    h.update(action.encode("utf-8"))
    h.update(b"\x00")
    h.update(canonical_json(payload).encode("utf-8"))
    return h.hexdigest()


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AuditLedgerImpl(AuditLedger):
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def append(self, tenant: TenantContext, actor: str, action: str,
                     payload: dict[str, object]) -> AuditEntry:
        # DLP: secrets are redacted BEFORE hashing so the ledger never holds them.
        # Documented: the chain covers the redacted record (what auditors see).
        clean = sanitize_audit_payload(dict(payload))
        last_seq = (await self.db.execute(
            select(func.max(AuditRow.seq)).where(
                AuditRow.tenant_id == tenant.tenant_id)
        )).scalar() or 0
        last = None
        if last_seq:
            last = (await self.db.execute(
                select(AuditRow.hash).where(
                    AuditRow.tenant_id == tenant.tenant_id,
                    AuditRow.seq == last_seq)
            )).scalar_one_or_none()
        prev_hash = last or GENESIS_HASH
        seq = last_seq + 1
        digest = chain_hash(prev_hash, seq, actor, action, clean)
        row = AuditRow(tenant_id=tenant.tenant_id, seq=seq, actor=actor,
                       action=action, payload=clean, prev_hash=prev_hash,
                       hash=digest, occurred_at=_utcnow())
        self.db.add(row)
        await self.db.flush()
        return AuditEntry(tenant_id=tenant.tenant_id, seq=seq, actor=actor,
                          action=action, payload=clean, prev_hash=prev_hash,
                          hash=digest, occurred_at=row.occurred_at)

    async def verify_chain(self, tenant: TenantContext, from_seq: int = 0) -> bool:
        rows = (await self.db.execute(
            select(AuditRow).where(
                AuditRow.tenant_id == tenant.tenant_id,
                AuditRow.seq >= from_seq).order_by(AuditRow.seq)
        )).scalars().all()
        expected_prev = GENESIS_HASH if from_seq <= 1 else None
        if from_seq > 1 and rows:
            # anchor: the stored prev_hash of the first verified row must match
            # the stored hash of the row before it.
            anchor = (await self.db.execute(
                select(AuditRow.hash).where(
                    AuditRow.tenant_id == tenant.tenant_id,
                    AuditRow.seq == from_seq - 1)
            )).scalar_one_or_none()
            expected_prev = anchor
        for row in rows:
            if expected_prev is not None and row.prev_hash != expected_prev:
                return False
            recomputed = chain_hash(row.prev_hash, row.seq, row.actor,
                                    row.action, dict(row.payload))
            if recomputed != row.hash:
                return False
            expected_prev = row.hash
        return True

    async def list_entries(self, tenant: TenantContext, limit: int = 50,
                           offset: int = 0, action: str | None = None,
                           actor: str | None = None,
                           ) -> tuple[list[AuditEntry], int]:
        q = select(AuditRow).where(AuditRow.tenant_id == tenant.tenant_id)
        if action:
            q = q.where(AuditRow.action == action)
        if actor:
            q = q.where(AuditRow.actor == actor)
        total = (await self.db.execute(
            select(func.count()).select_from(q.subquery()))).scalar_one()
        rows = (await self.db.execute(
            q.order_by(AuditRow.seq.desc()).limit(limit).offset(offset)
        )).scalars().all()
        return [self._to_entry(row) for row in rows], total

    async def get_by_seq(self, tenant: TenantContext, seq: int) -> AuditEntry | None:
        row = (await self.db.execute(
            select(AuditRow).where(AuditRow.tenant_id == tenant.tenant_id,
                                   AuditRow.seq == seq)
        )).scalar_one_or_none()
        return self._to_entry(row) if row is not None else None

    @staticmethod
    def _to_entry(row: AuditRow) -> AuditEntry:
        return AuditEntry(tenant_id=row.tenant_id, seq=row.seq, actor=row.actor,
                          action=row.action, payload=dict(row.payload),
                          prev_hash=row.prev_hash, hash=row.hash,
                          occurred_at=row.occurred_at)
