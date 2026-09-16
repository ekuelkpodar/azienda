"""Router: audit ledger. Contract: API.md §11.

Append-only: there is no POST/PUT/DELETE here by design. Verification
recomputes the hash chain and reports the first broken link (if any).
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_audit_ledger, get_tenant_context, require_roles
from app.api.schemas import PaginationParams, paginate
from app.core.contracts import AuditEntry, AuditLedger, TenantContext

router = APIRouter(prefix="/audit", tags=["audit"])


class VerifyIn(BaseModel):
    from_seq: int = Field(default=1, ge=1)


def _view(entry: AuditEntry) -> dict[str, Any]:
    return {"seq": entry.seq, "actor": entry.actor, "action": entry.action,
            "payload": entry.payload, "prev_hash": entry.prev_hash,
            "hash": entry.hash, "occurred_at": entry.occurred_at}


@router.get("/entries", dependencies=[Depends(require_roles("owner", "admin"))])
async def list_entries(p: Annotated[PaginationParams, Depends()],
                       action: str | None = None,
                       actor: str | None = None,
                       ledger: AuditLedger = Depends(get_audit_ledger),
                       tenant: TenantContext = Depends(get_tenant_context)) -> dict[str, Any]:
    rows, total = await ledger.list_entries(tenant, limit=p.page_size,
                                            offset=p.offset(), action=action,
                                            actor=actor)
    page = paginate([_view(r) for r in rows], p.page_size, 0)
    return {"items": page.items, "next_page_token": page.next_page_token,
            "total": total}


@router.get("/entries/{seq}", dependencies=[Depends(require_roles("owner", "admin"))])
async def get_entry(seq: int,
                    ledger: AuditLedger = Depends(get_audit_ledger),
                    tenant: TenantContext = Depends(get_tenant_context)) -> dict[str, Any]:
    row = await ledger.get_by_seq(tenant, seq)
    if row is None:
        from app.core import errors
        raise errors.NotFoundError("audit entry not found")
    return _view(row)


@router.post("/verify", dependencies=[Depends(require_roles("owner", "admin"))])
async def verify_chain(body: VerifyIn,
                       ledger: AuditLedger = Depends(get_audit_ledger),
                       tenant: TenantContext = Depends(get_tenant_context)) -> dict[str, Any]:
    ok = await ledger.verify_chain(tenant, from_seq=body.from_seq)
    return {"ok": ok, "from_seq": body.from_seq,
            "detail": "chain intact" if ok else "CHAIN BROKEN: tampering detected"}
