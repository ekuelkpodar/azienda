"""Router: approvals (the human queue). Contract: API.md §9.

Decisions are audited. Timeout elsewhere (sweeper/CLI) marks approvals EXPIRED;
an expired approval can never be approved — fail closed.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.deps import (
    get_approval_store,
    get_audit_ledger,
    get_idempotency_store,
    get_tenant_context,
    idempotency_check,
    idempotency_store,
    replay_response,
    require_roles,
)
from app.api.schemas import PaginationParams, paginate
from app.core.contracts import Approval, ApprovalStore, AuditLedger, TenantContext
from app.core.idempotency import IdempotencyStore

router = APIRouter(prefix="/approvals", tags=["approvals"])


class DecideIn(BaseModel):
    approved: bool
    note: str = Field(default="", max_length=2000)


class EscalateIn(BaseModel):
    note: str = Field(default="", max_length=2000)


def _view(approval: Approval) -> dict[str, Any]:
    return {
        "id": approval.approval_id,
        "tenant_id": approval.tenant_id,
        "action": approval.action.action,
        "resource": approval.action.resource,
        "args": approval.action.args,
        "status": approval.status.value,
        "requested_by": approval.requested_by,
        "decided_by": approval.decided_by,
        "expires_at": approval.expires_at,
    }


@router.get("")
async def list_approvals(p: Annotated[PaginationParams, Depends()],
                         store: ApprovalStore = Depends(get_approval_store),
                         tenant: TenantContext = Depends(get_tenant_context)) -> dict[str, Any]:
    rows, total = await store.list_pending(tenant, limit=p.page_size,
                                           offset=p.offset())
    items = []
    for r in rows:
        items.append({"id": r.approval_id, "action": r.action.action,
                      "resource": r.action.resource, "args": r.action.args,
                      "risk_score": str(r.risk_score or 0),
                      "risk_factors": list(r.risk_factors),
                      "reasons": list(r.reasons),
                      "status": r.status.value, "requested_by": r.requested_by,
                      "decided_by": r.decided_by, "expires_at": r.expires_at})
    page = paginate(items, p.page_size, 0)
    return {"items": page.items, "next_page_token": page.next_page_token,
            "total": total}


@router.get("/{approval_id}")
async def get_approval(approval_id: str,
                       store: ApprovalStore = Depends(get_approval_store),
                       tenant: TenantContext = Depends(get_tenant_context)) -> dict[str, Any]:
    approval = await store.get(tenant, approval_id)
    if approval is None:
        from app.core import errors
        raise errors.NotFoundError("approval not found")
    return _view(approval)


@router.post("/{approval_id}/decide",
             response_model=None,
             dependencies=[Depends(require_roles("owner", "admin"))])
async def decide_approval(approval_id: str, body: DecideIn, request: Request,
                          idem: tuple[int, dict[str, Any]] | str | None
                          = Depends(idempotency_check),
                          store: ApprovalStore = Depends(get_approval_store),
                          tenant: TenantContext = Depends(get_tenant_context),
                          audit: AuditLedger = Depends(get_audit_ledger),
                          idem_store: IdempotencyStore = Depends(get_idempotency_store)
                          ) -> dict[str, Any] | JSONResponse:
    if isinstance(idem, tuple):
        return replay_response(idem)
    approval = await store.decide(tenant, approval_id, body.approved, body.note)
    await audit.append(tenant, tenant.user_id or "system",
                       f"approval.{'approved' if body.approved else 'denied'}",
                       {"approval_id": approval_id, "note": body.note})
    out = _view(approval)
    if isinstance(idem, str):
        await idempotency_store(request, idem, 200, out, tenant, idem_store)
    return out


@router.post("/{approval_id}/escalate",
             dependencies=[Depends(require_roles("owner", "admin"))])
async def escalate_approval(approval_id: str, body: EscalateIn,
                            store: ApprovalStore = Depends(get_approval_store),
                            tenant: TenantContext = Depends(get_tenant_context),
                            audit: AuditLedger = Depends(get_audit_ledger)) -> dict[str, Any]:
    approval = await store.escalate(tenant, approval_id, body.note)
    await audit.append(tenant, tenant.user_id or "system", "approval.escalated",
                       {"approval_id": approval_id, "note": body.note})
    return _view(approval)
