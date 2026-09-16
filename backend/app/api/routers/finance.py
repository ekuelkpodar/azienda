"""Router: finance. Thin HTTP layer — all business logic lives in FinanceService.

> **Not a regulated accounting system.** These endpoints record operational
> finance data; NEXORA ERP is the system of record for accounting truth.

Auto-discovery in ``main.py`` mounts this module's ``router`` under ``/api/v1``.
Composition (``BizAppServices``), error mapping, and serialization live in
``.comms`` — the shared router seam for the five business-app routers.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Header
from fastapi.responses import Response

from app.core.contracts import TenantContext
from app.finance import schemas as S

from ._common import TenantDep
from .comms import BizAppDep, BizAppServices, _handle

router = APIRouter(prefix="/finance", tags=["finance"])


# ------------------------------------------------------------------ customers
@router.post("/customers", status_code=201)
async def create_customer(data: S.CustomerCreate, tenant: TenantContext = TenantDep,
                          svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.finance.create_customer(tenant, data), 201)


@router.get("/customers")
async def list_customers(tenant: TenantContext = TenantDep,
                         svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.finance.list_customers(tenant))


@router.get("/customers/{customer_id}")
async def get_customer(customer_id: str, tenant: TenantContext = TenantDep,
                       svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.finance.get_customer(tenant, customer_id))


# ------------------------------------------------------------------ invoices
@router.post("/invoices", status_code=201)
async def create_invoice(data: S.InvoiceCreate, tenant: TenantContext = TenantDep,
                         svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.finance.create_invoice(tenant, data), 201)


@router.get("/invoices")
async def list_invoices(status: S.InvoiceStatus | None = None,
                        customer_id: str | None = None,
                        tenant: TenantContext = TenantDep,
                        svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.finance.list_invoices(
        tenant, status.value if status else None, customer_id))


@router.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: str, tenant: TenantContext = TenantDep,
                      svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.finance.get_invoice(tenant, invoice_id))


@router.patch("/invoices/{invoice_id}")
async def update_invoice(invoice_id: str, data: S.InvoiceUpdate,
                         tenant: TenantContext = TenantDep,
                         svc: BizAppServices = BizAppDep) -> Response:
    # Draft-only — issued records are immutable (409).
    return await _handle(svc.finance.update_invoice(tenant, invoice_id, data))


@router.post("/invoices/{invoice_id}/issue")
async def issue_invoice(invoice_id: str, tenant: TenantContext = TenantDep,
                        svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.finance.issue_invoice(tenant, invoice_id))


@router.post("/invoices/{invoice_id}/cancel")
async def cancel_invoice(invoice_id: str, tenant: TenantContext = TenantDep,
                         svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.finance.cancel_invoice(tenant, invoice_id))


@router.post("/invoices/{invoice_id}/void")
async def void_invoice(invoice_id: str, data: S.InvoiceVoid,
                       tenant: TenantContext = TenantDep,
                       svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.finance.void_invoice(tenant, invoice_id, data))


@router.get("/invoices/{invoice_id}/nexora")
async def nexora_status(invoice_id: str, tenant: TenantContext = TenantDep,
                        svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.finance.nexora_status(tenant, "invoice", invoice_id))


@router.post("/invoices/{invoice_id}/payments", status_code=201)
async def record_payment(
    invoice_id: str, data: S.PaymentCreate, tenant: TenantContext = TenantDep,
    svc: BizAppServices = BizAppDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Response:
    if idempotency_key and not data.idempotency_key:
        data = data.model_copy(update={"idempotency_key": idempotency_key})
    return await _handle(svc.finance.record_payment(tenant, invoice_id, data), 201)


@router.get("/invoices/{invoice_id}/payments")
async def list_invoice_payments(invoice_id: str, tenant: TenantContext = TenantDep,
                                svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.finance.list_payments(tenant, invoice_id=invoice_id))


@router.post("/sweep/overdue")
async def mark_overdue(tenant: TenantContext = TenantDep,
                       svc: BizAppServices = BizAppDep) -> Response:
    """Worker endpoint: mark past-due issued invoices overdue."""
    return await _handle(svc.finance.mark_overdue(tenant))


# ------------------------------------------------------------------ expenses
@router.post("/expenses", status_code=201)
async def record_expense(data: S.ExpenseCreate, tenant: TenantContext = TenantDep,
                         svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.finance.record_expense(tenant, data), 201)


@router.get("/expenses")
async def list_expenses(tenant: TenantContext = TenantDep,
                        svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.finance.list_expenses(tenant))


@router.get("/expenses/{expense_id}")
async def get_expense(expense_id: str, tenant: TenantContext = TenantDep,
                      svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.finance.get_expense(tenant, expense_id))


@router.patch("/expenses/{expense_id}")
async def update_expense(expense_id: str, data: S.ExpenseUpdate,
                         tenant: TenantContext = TenantDep,
                         svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.finance.update_expense(tenant, expense_id, data))


@router.delete("/expenses/{expense_id}", status_code=204)
async def delete_expense(expense_id: str, tenant: TenantContext = TenantDep,
                         svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.finance.delete_expense(tenant, expense_id), 204)


@router.get("/expenses/suggest-category")
async def suggest_category(vendor: str | None = None,
                           description: str | None = None,
                           svc: BizAppServices = BizAppDep) -> Response:
    """Keyword-heuristic category suggestion — explicitly not ML."""
    return await _handle(svc.finance.suggest_category(vendor, description))


# ------------------------------------------------------- accounts / transactions
@router.post("/accounts", status_code=201)
async def create_account(data: S.AccountCreate, tenant: TenantContext = TenantDep,
                         svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.finance.create_account(tenant, data), 201)


@router.get("/accounts")
async def list_accounts(tenant: TenantContext = TenantDep,
                        svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.finance.list_accounts(tenant))


@router.post("/transactions", status_code=201)
async def record_transaction(data: S.TransactionCreate,
                             tenant: TenantContext = TenantDep,
                             svc: BizAppServices = BizAppDep) -> Response:
    # Append-only, policy-gated.
    return await _handle(svc.finance.record_transaction(tenant, data), 201)


@router.get("/transactions")
async def list_transactions(account_id: str | None = None,
                            tenant: TenantContext = TenantDep,
                            svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.finance.list_transactions(tenant, account_id=account_id))


# ------------------------------------------------------------------ reports
@router.get("/reports/ar-aging")
async def ar_aging(tenant: TenantContext = TenantDep,
                   svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.finance.ar_aging(tenant))


@router.get("/reports/cashflow")
async def cashflow(period_start: datetime, period_end: datetime,
                   tenant: TenantContext = TenantDep,
                   svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.finance.cashflow(tenant, period_start, period_end))


@router.get("/reports/anomalies")
async def anomalies(tenant: TenantContext = TenantDep,
                    svc: BizAppServices = BizAppDep) -> Response:
    # Rule-based heuristics — flagged only, never blocking; not fraud detection.
    return await _handle(svc.finance.scan_anomalies(tenant))


# ------------------------------------------------------------------ collections
@router.get("/collections/candidates")
async def collection_candidates(min_days_overdue: int = 1,
                                tenant: TenantContext = TenantDep,
                                svc: BizAppServices = BizAppDep) -> Response:
    return await _handle(svc.finance.collection_candidates(
        tenant, min_days_overdue=min_days_overdue))


@router.post("/collections/reminders", status_code=201)
async def generate_collection_reminder(invoice_id: str,
                                       tenant: TenantContext = TenantDep,
                                       svc: BizAppServices = BizAppDep) -> Response:
    # Policy-gated; parked as approval_pending — never sent by this endpoint.
    return await _handle(svc.finance.generate_collection_reminder(tenant, invoice_id), 201)
