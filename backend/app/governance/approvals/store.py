"""Approval workflow: request → decide (approve/deny) → timeout == DENY.

Rules (ARCHITECTURE.md §6, API.md §9):
- LOW risk: auto-approved by the policy engine (never reaches the store).
- MEDIUM: configurable per tenant (settings.approval_medium_require).
- HIGH: mandatory human approval.
- Timeout/expiry == DENY (fail closed). Expired approvals can never be approved.
- Every transition is recorded in ``approval_events`` and mirrored to the audit
  ledger by the API layer.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import errors
from app.core.config import Settings
from app.core.contracts import (
    ActionRequest,
    Approval,
    ApprovalStatus,
    PolicyDecision,
    TenantContext,
)
from app.core.time import as_utc
from app.governance.models import ApprovalEvent, ApprovalRecord


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ApprovalStoreImpl:
    """Implements ``core.contracts.ApprovalStore`` on Postgres/SQLite."""

    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    # -- protocol ------------------------------------------------------
    async def request(self, decision: PolicyDecision, request: ActionRequest,
                      risk_score: float = 0.0,
                      risk_factors: tuple[str, ...] = ()) -> Approval:
        """Create a pending approval. Idempotent per (action, idempotency_key)."""
        tenant = request.tenant
        if request.idempotency_key:
            existing = (await self.db.execute(
                select(ApprovalRecord).where(
                    ApprovalRecord.tenant_id == tenant.tenant_id,
                    ApprovalRecord.idempotency_key == request.idempotency_key,
                    ApprovalRecord.status == ApprovalStatus.PENDING.value)
            )).scalar_one_or_none()
            if existing is not None:
                return self._to_approval(existing, request)

        record = ApprovalRecord(
            tenant_id=tenant.tenant_id,
            action=request.action,
            resource=request.resource,
            args=dict(request.args or {}),
            risk_score=risk_score,
            risk_factors=list(risk_factors),
            policy_id=decision.policy_id,
            reasons=list(decision.reasons),
            status=ApprovalStatus.PENDING.value,
            requested_by=tenant.user_id,
            requested_by_kind="agent" if tenant.agent_id else "user",
            expires_at=_utcnow() + timedelta(seconds=self.settings.approval_ttl_seconds),
            idempotency_key=request.idempotency_key)
        self.db.add(record)
        await self.db.flush()
        self.db.add(ApprovalEvent(
            approval_id=record.id, tenant_id=tenant.tenant_id,
            event_type="approval.requested",
            actor=tenant.user_id or tenant.agent_id or "system",
            payload={"action": request.action, "reasons": list(decision.reasons)}))
        await self.db.flush()
        return self._to_approval(record, request)

    async def decide(self, tenant: TenantContext, approval_id: str,
                     approved: bool, note: str = "") -> Approval:
        record = await self._get_record(tenant, approval_id)
        if record is None:
            raise errors.NotFoundError("approval not found")
        self._expire_if_due(record)
        if record.status != ApprovalStatus.PENDING.value:
            raise errors.ConflictError(
                f"approval is {record.status}; only pending approvals can be decided",
                details={"status": record.status})
        record.status = (ApprovalStatus.APPROVED.value if approved
                         else ApprovalStatus.DENIED.value)
        record.decided_by = tenant.user_id
        record.decided_at = _utcnow()
        record.note = note
        self.db.add(ApprovalEvent(
            approval_id=record.id, tenant_id=tenant.tenant_id,
            event_type="approval.approved" if approved else "approval.denied",
            actor=tenant.user_id or "system",
            payload={"note": note}))
        await self.db.flush()
        # Rebuild a minimal ActionRequest for the Approval dataclass.
        request = ActionRequest(tenant=tenant, action=record.action,
                                resource=record.resource, args=dict(record.args or {}))
        return self._to_approval(record, request)

    async def get(self, tenant: TenantContext, approval_id: str) -> Approval | None:
        record = await self._get_record(tenant, approval_id)
        if record is None:
            return None
        self._expire_if_due(record)
        await self.db.flush()
        request = ActionRequest(tenant=tenant, action=record.action,
                                resource=record.resource, args=dict(record.args or {}))
        return self._to_approval(record, request)

    # -- operations ----------------------------------------------------
    async def list_pending(self, tenant: TenantContext, limit: int = 50,
                           offset: int = 0) -> tuple[list[Approval], int]:
        await self.sweep_expired(tenant)
        total = (await self.db.execute(
            select(func.count()).select_from(ApprovalRecord).where(
                ApprovalRecord.tenant_id == tenant.tenant_id,
                ApprovalRecord.status == ApprovalStatus.PENDING.value)
        )).scalar_one()
        rows = (await self.db.execute(
            select(ApprovalRecord).where(
                ApprovalRecord.tenant_id == tenant.tenant_id,
                ApprovalRecord.status == ApprovalStatus.PENDING.value)
            .order_by(ApprovalRecord.created_at).limit(limit).offset(offset)
        )).scalars().all()
        items = [self._to_approval(
            row, ActionRequest(tenant=tenant, action=row.action,
                              resource=row.resource,
                              args=dict(row.args or {}))) for row in rows]
        return items, total

    async def sweep_expired(self, tenant: TenantContext | None = None) -> int:
        """Mark due approvals EXPIRED. Returns count. Timeout == DENY: an expired
        approval can never be approved afterwards (decide() rejects non-pending)."""
        now = _utcnow()
        q = select(ApprovalRecord).where(
            ApprovalRecord.status == ApprovalStatus.PENDING.value,
            ApprovalRecord.expires_at <= now)
        if tenant is not None:
            q = q.where(ApprovalRecord.tenant_id == tenant.tenant_id)
        rows = (await self.db.execute(q)).scalars().all()
        for record in rows:
            record.status = ApprovalStatus.EXPIRED.value
            self.db.add(ApprovalEvent(
                approval_id=record.id, tenant_id=record.tenant_id,
                event_type="approval.expired", actor="system",
                payload={"reason": "timeout == DENY (fail closed)"}))
        if rows:
            await self.db.flush()
        return len(rows)

    async def escalate(self, tenant: TenantContext, approval_id: str,
                       note: str = "") -> Approval:
        """Route to another approver: recorded as an event; approval stays pending
        with a fresh expiry is NOT granted (fail closed — escalation never extends
        the timeout)."""
        record = await self._get_record(tenant, approval_id)
        if record is None:
            raise errors.NotFoundError("approval not found")
        self._expire_if_due(record)
        if record.status != ApprovalStatus.PENDING.value:
            raise errors.ConflictError("only pending approvals can be escalated")
        self.db.add(ApprovalEvent(
            approval_id=record.id, tenant_id=tenant.tenant_id,
            event_type="approval.escalated", actor=tenant.user_id or "system",
            payload={"note": note}))
        await self.db.flush()
        request = ActionRequest(tenant=tenant, action=record.action,
                                resource=record.resource, args=dict(record.args or {}))
        return self._to_approval(record, request)

    # -- internals -----------------------------------------------------
    async def _get_record(self, tenant: TenantContext,
                          approval_id: str) -> ApprovalRecord | None:
        return (await self.db.execute(
            select(ApprovalRecord).where(
                ApprovalRecord.id == approval_id,
                ApprovalRecord.tenant_id == tenant.tenant_id)
        )).scalar_one_or_none()

    def _expire_if_due(self, record: ApprovalRecord) -> None:
        if (record.status == ApprovalStatus.PENDING.value
                and as_utc(record.expires_at) <= _utcnow()):
            record.status = ApprovalStatus.EXPIRED.value
            self.db.add(ApprovalEvent(
                approval_id=record.id, tenant_id=record.tenant_id,
                event_type="approval.expired", actor="system",
                payload={"reason": "timeout == DENY (fail closed)"}))

    @staticmethod
    def _to_approval(record: ApprovalRecord, request: ActionRequest) -> Approval:
        return Approval(
            approval_id=record.id, tenant_id=record.tenant_id, action=request,
            status=ApprovalStatus(record.status),
            requested_by=record.requested_by or "system",
            decided_by=record.decided_by, expires_at=record.expires_at,
            risk_score=float(record.risk_score or 0),
            risk_factors=tuple(record.risk_factors or ()),
            reasons=tuple(record.reasons or ()))
