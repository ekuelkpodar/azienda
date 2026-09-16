"""Router: budgets. Contract: API.md §10.

Budgets are the P0 cost control: reserve-before-spend, exhaustion freezes new
work, kill switch freezes everything. All mutations are audited.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.deps import (
    get_audit_ledger,
    get_budget_enforcer,
    get_idempotency_store,
    get_tenant_context,
    idempotency_check,
    idempotency_store,
    replay_response,
    require_roles,
)
from app.api.schemas import PaginationParams, paginate
from app.core.contracts import AuditLedger, BudgetEnforcer, TenantContext
from app.core.idempotency import IdempotencyStore

router = APIRouter(prefix="/budgets", tags=["budgets"])


class BudgetIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    credit_limit: Decimal = Field(gt=0)
    period: str = Field(default="monthly", pattern="^(daily|weekly|monthly)$")
    scope: str = Field(default="tenant", pattern="^(tenant|workflow|agent)$")
    scope_ref: str | None = Field(default=None, max_length=255)


class KillSwitchIn(BaseModel):
    reason: str = Field(min_length=1, max_length=1024)


@router.get("")
async def list_budgets(enforcer: BudgetEnforcer = Depends(get_budget_enforcer),
                       tenant: TenantContext = Depends(get_tenant_context)) -> dict[str, Any]:
    return {"items": await enforcer.list_budgets(tenant),
            "spend_frozen": await enforcer.is_frozen(tenant)}


@router.post("", dependencies=[Depends(require_roles("owner", "admin"))],
            response_model=None)
async def create_budget(body: BudgetIn, request: Request,
                        idem: tuple[int, dict[str, Any]] | str | None
                        = Depends(idempotency_check),
                        enforcer: BudgetEnforcer = Depends(get_budget_enforcer),
                        tenant: TenantContext = Depends(get_tenant_context),
                        audit: AuditLedger = Depends(get_audit_ledger),
                        idem_store: IdempotencyStore = Depends(get_idempotency_store)
                        ) -> dict[str, Any] | JSONResponse:
    if isinstance(idem, tuple):
        return replay_response(idem)
    budget = await enforcer.create_budget(tenant, body.name, body.credit_limit,
                                          body.period, body.scope, body.scope_ref)
    await audit.append(tenant, tenant.user_id or "system", "budget.created",
                       {"budget_id": budget.id, "name": body.name,
                        "credit_limit": str(body.credit_limit)})
    out = {"id": budget.id, "name": budget.name, "scope": budget.scope,
           "credit_limit": str(budget.credit_limit), "period": budget.period,
           "is_active": budget.is_active}
    if isinstance(idem, str):
        await idempotency_store(request, idem, 200, out, tenant, idem_store)
    return out


@router.get("/{budget_id}/ledger")
async def budget_ledger(budget_id: str,
                        p: Annotated[PaginationParams, Depends()],
                        enforcer: BudgetEnforcer = Depends(get_budget_enforcer),
                        tenant: TenantContext = Depends(get_tenant_context)) -> dict[str, Any]:
    rows, total = await enforcer.cost_ledger_entries(tenant, limit=p.page_size,
                                                     offset=p.offset())
    items = [{"id": r.id, "task_id": r.task_id, "run_id": r.run_id,
              "model": r.model, "input_tokens": r.input_tokens,
              "output_tokens": r.output_tokens, "cost_usd": str(r.cost_usd),
              "credits_drawn": str(r.credits_drawn),
              "recorded_at": r.recorded_at} for r in rows]
    page = paginate(items, p.page_size, 0)
    return {"items": page.items, "next_page_token": page.next_page_token,
            "total": total, "budget_id": budget_id}


@router.post("/kill-switch", dependencies=[Depends(require_roles("owner", "admin"))])
async def kill_switch(body: KillSwitchIn,
                      enforcer: BudgetEnforcer = Depends(get_budget_enforcer),
                      tenant: TenantContext = Depends(get_tenant_context),
                      audit: AuditLedger = Depends(get_audit_ledger)) -> dict[str, Any]:
    await enforcer.kill_switch(tenant, body.reason)
    await audit.append(tenant, tenant.user_id or "system", "budget.kill_switch",
                       {"reason": body.reason})
    return {"ok": True, "spend_frozen": True}


@router.post("/kill-switch/release",
             dependencies=[Depends(require_roles("owner", "admin"))])
async def release_kill_switch(
        enforcer: BudgetEnforcer = Depends(get_budget_enforcer),
        tenant: TenantContext = Depends(get_tenant_context),
        audit: AuditLedger = Depends(get_audit_ledger)) -> dict[str, Any]:
    await enforcer.release_kill_switch(tenant)
    await audit.append(tenant, tenant.user_id or "system",
                       "budget.kill_switch_released", {})
    return {"ok": True, "spend_frozen": False}


@router.get("/alerts")
async def list_alerts(enforcer: BudgetEnforcer = Depends(get_budget_enforcer),
                      tenant: TenantContext = Depends(get_tenant_context)) -> dict[str, Any]:
    rows = await enforcer.list_alerts(tenant, limit=100)
    return {"items": [{"id": r.id, "budget_id": r.budget_id,
                       "threshold": str(r.threshold), "fired_at": r.fired_at,
                       "acknowledged_at": r.acknowledged_at} for r in rows]}
