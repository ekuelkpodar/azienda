"""Router: billing (own SaaS). Contract: API.md §13.

Pricing is data: plans/dimensions come from the DB (seeded via CLI).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.deps import (
    get_audit_ledger,
    get_billing_service,
    get_idempotency_store,
    get_tenant_context,
    idempotency_check,
    idempotency_store,
    replay_response,
    require_roles,
)
from app.api.schemas import Page, PaginationParams, paginate
from app.billing.service import BillingService
from app.core.contracts import AuditLedger, TenantContext
from app.core.idempotency import IdempotencyStore

router = APIRouter(prefix="/billing", tags=["billing"])


class ChangePlanIn(BaseModel):
    plan_slug: str = Field(min_length=1, max_length=64)


class SpendCapIn(BaseModel):
    # null removes the cap
    spend_cap_usd: Decimal | None = Field(default=None, ge=0)


@router.get("/plans")
async def list_plans(service: BillingService = Depends(get_billing_service)) -> dict[str, Any]:
    return {"items": await service.list_plans()}


@router.get("/subscription")
async def get_subscription(tenant: TenantContext = Depends(get_tenant_context),
                           service: BillingService = Depends(get_billing_service),
                           ) -> dict[str, Any]:
    sub = await service.get_subscription(tenant)
    if sub is None:
        from app.core import errors
        raise errors.NotFoundError("no subscription for tenant")
    return sub


@router.post("/subscription", dependencies=[Depends(require_roles("owner", "admin"))],
              response_model=None)
async def change_subscription(body: ChangePlanIn, request: Request,
                              idem: tuple[int, dict[str, Any]] | str | None
                          = Depends(idempotency_check),
                              tenant: TenantContext = Depends(get_tenant_context),
                              service: BillingService = Depends(get_billing_service),
                              audit: AuditLedger = Depends(get_audit_ledger),
                              idem_store: IdempotencyStore = Depends(get_idempotency_store)
                          ) -> dict[str, Any] | JSONResponse:
    if isinstance(idem, tuple):
        return replay_response(idem)
    # subscribe() raises 409 if a subscription exists; change_plan() handles both.
    existing = await service.get_subscription(tenant)
    if existing is None:
        out = await service.subscribe(tenant, body.plan_slug)
        action = "billing.subscribed"
    else:
        out = await service.change_plan(tenant, body.plan_slug)
        action = "billing.plan_changed"
    await audit.append(tenant, tenant.user_id or "system", action,
                       {"plan_slug": body.plan_slug})
    if isinstance(idem, str):
        await idempotency_store(request, idem, 200, out, tenant, idem_store)
    return out


@router.get("/usage")
async def get_usage(start: date | None = None, end: date | None = None,
                    tenant: TenantContext = Depends(get_tenant_context),
                    service: BillingService = Depends(get_billing_service)) -> dict[str, Any]:
    today = date.today()
    start = start or today.replace(day=1)
    end = end or today
    return {"items": await service.get_usage(tenant, start, end),
            "overage": await service.compute_overage(tenant.tenant_id)}


@router.get("/invoices")
async def list_invoices(p: Annotated[PaginationParams, Depends()],
                        tenant: TenantContext = Depends(get_tenant_context),
                        service: BillingService = Depends(get_billing_service),
                        ) -> Page[dict[str, Any]]:
    invoices = await service.list_invoices(tenant, limit=p.page_size)
    return paginate(invoices, p.page_size, p.offset())


@router.post("/invoices/draft",
             dependencies=[Depends(require_roles("owner", "admin"))])
async def draft_invoice(tenant: TenantContext = Depends(get_tenant_context),
                        service: BillingService = Depends(get_billing_service),
                        audit: AuditLedger = Depends(get_audit_ledger)) -> dict[str, Any]:
    out = await service.create_invoice(tenant)
    await audit.append(tenant, tenant.user_id or "system", "billing.invoice_drafted",
                       {"invoice_id": out["id"], "total_usd": out["total_usd"]})
    return out


@router.post("/spend-caps", dependencies=[Depends(require_roles("owner", "admin"))])
async def set_spend_cap(body: SpendCapIn,
                        tenant: TenantContext = Depends(get_tenant_context),
                        service: BillingService = Depends(get_billing_service),
                        audit: AuditLedger = Depends(get_audit_ledger)) -> dict[str, Any]:
    out = await service.set_spend_cap(tenant, body.spend_cap_usd)
    await audit.append(tenant, tenant.user_id or "system", "billing.spend_cap_set",
                       {"spend_cap_usd": str(body.spend_cap_usd)})
    return out
