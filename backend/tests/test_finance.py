"""Tests for the finance package: invoice lifecycle + immutability, payments,
expenses, ledger, reports, anomalies, collections, NEXORA-disabled honesty,
policy gates, and cross-tenant isolation.

> **Not a regulated accounting system.** These tests cover operational finance
> behavior only.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.contracts import DomainEvent, TenantContext
from app.finance import schemas as S
from app.finance.service import (
    FinanceError,
    FinanceNotFoundError,
    FinanceService,
    InvoiceStateError,
    PolicyDeniedError,
)
from tests.conftest import FakePolicyEngine


class _Bus:
    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    async def publish(self, event: DomainEvent) -> None:
        self.events.append(event)

    def subscribe(self, topic: str, handler):  # noqa: ANN001, ANN202
        return None


def _svc(policy=None, bus=None) -> FinanceService:  # noqa: ANN001
    return FinanceService(
        policy=policy if policy is not None else FakePolicyEngine(),
        events=bus if bus is not None else _Bus())


async def _customer(svc: FinanceService, tenant: TenantContext) -> S.Customer:
    return await svc.create_customer(tenant, S.CustomerCreate(
        name="Acme", email="billing@acme.test"))


def _lines() -> list[S.InvoiceLine]:
    return [S.InvoiceLine(description="Consulting", quantity=Decimal("2"),
                          unit_price=Decimal("150.00"))]


async def _draft(svc: FinanceService, tenant: TenantContext) -> S.Invoice:
    cust = await _customer(svc, tenant)
    return await svc.create_invoice(tenant, S.InvoiceCreate(
        customer_id=cust.id, lines=_lines(), currency="usd",
        due_at=datetime.now(UTC) + timedelta(days=30)))


# ------------------------------------------------------------ invoice lifecycle
async def test_invoice_draft_to_issued_to_paid(tenant: TenantContext) -> None:
    bus = _Bus()
    svc = _svc(bus=bus)
    inv = await _draft(svc, tenant)
    assert inv.status == S.InvoiceStatus.DRAFT
    assert inv.amount == Decimal("300.00")
    assert inv.currency == "USD"

    inv = await svc.issue_invoice(tenant, inv.id)
    assert inv.status == S.InvoiceStatus.ISSUED
    assert inv.issued_at is not None

    pay = await svc.record_payment(tenant, inv.id, S.PaymentCreate(
        amount=Decimal("300.00"), method="card"))
    assert pay.amount == Decimal("300.00")
    inv = await svc.get_invoice(tenant, inv.id)
    assert inv.status == S.InvoiceStatus.PAID
    assert inv.balance_due == Decimal("0")
    assert any(e.topic == "finance.invoice.issued" for e in bus.events)
    assert any(e.topic == "finance.payment.recorded" for e in bus.events)


async def test_issued_invoice_is_immutable(tenant: TenantContext) -> None:
    from app.finance.service import IssuedInvoiceImmutableError
    svc = _svc()
    inv = await _draft(svc, tenant)
    inv = await svc.issue_invoice(tenant, inv.id)
    with pytest.raises(IssuedInvoiceImmutableError):
        await svc.update_invoice(tenant, inv.id, S.InvoiceUpdate(due_at=None))
    with pytest.raises(InvoiceStateError):
        await svc.cancel_invoice(tenant, inv.id)  # only drafts cancel


async def test_void_issued_invoice(tenant: TenantContext) -> None:
    svc = _svc()
    inv = await _draft(svc, tenant)
    inv = await svc.issue_invoice(tenant, inv.id)
    inv = await svc.void_invoice(tenant, inv.id, S.InvoiceVoid(reason="duplicate"))
    assert inv.status == S.InvoiceStatus.VOIDED
    assert inv.void_reason == "duplicate"
    with pytest.raises(InvoiceStateError):
        await svc.record_payment(tenant, inv.id, S.PaymentCreate(
            amount=Decimal("1"), method="cash"))


async def test_invalid_transitions_rejected(tenant: TenantContext) -> None:
    svc = _svc()
    inv = await _draft(svc, tenant)
    with pytest.raises(InvoiceStateError):
        await svc.record_payment(tenant, inv.id, S.PaymentCreate(
            amount=Decimal("1"), method="cash"))  # draft cannot take payments
    with pytest.raises(InvoiceStateError):
        await svc.void_invoice(tenant, inv.id, S.InvoiceVoid(reason="x"))


# ------------------------------------------------------------------ payments
async def test_payment_idempotent(tenant: TenantContext) -> None:
    svc = _svc()
    inv = await _draft(svc, tenant)
    inv = await svc.issue_invoice(tenant, inv.id)
    data = S.PaymentCreate(amount=Decimal("100"), method="card",
                           idempotency_key="pay-1")
    p1 = await svc.record_payment(tenant, inv.id, data)
    p2 = await svc.record_payment(tenant, inv.id, data)
    assert p1.id == p2.id
    inv = await svc.get_invoice(tenant, inv.id)
    assert inv.balance_due == Decimal("200.00")


async def test_overpayment_rejected(tenant: TenantContext) -> None:
    svc = _svc()
    inv = await _draft(svc, tenant)
    inv = await svc.issue_invoice(tenant, inv.id)
    with pytest.raises(FinanceError, match="exceeds balance"):
        await svc.record_payment(tenant, inv.id, S.PaymentCreate(
            amount=Decimal("9999"), method="card"))


async def test_mark_overdue_sweep(tenant: TenantContext) -> None:
    svc = _svc()
    cust = await _customer(svc, tenant)
    inv = await svc.create_invoice(tenant, S.InvoiceCreate(
        customer_id=cust.id, lines=_lines(), currency="USD",
        due_at=datetime.now(UTC) - timedelta(days=1)))
    inv = await svc.issue_invoice(tenant, inv.id)
    count = await svc.mark_overdue(tenant)
    assert count == 1
    assert (await svc.get_invoice(tenant, inv.id)).status == S.InvoiceStatus.OVERDUE


# ------------------------------------------------------------------ expenses
async def test_expense_crud_and_suggestion(tenant: TenantContext) -> None:
    svc = _svc()
    exp = await svc.record_expense(tenant, S.ExpenseCreate(
        category="travel", amount=Decimal("42.50"), currency="USD",
        vendor="Delta Airlines", description="flight"))
    assert (await svc.get_expense(tenant, exp.id)).id == exp.id
    exp = await svc.update_expense(tenant, exp.id, S.ExpenseUpdate(
        description="flight to NYC"))
    assert exp.description == "flight to NYC"
    await svc.delete_expense(tenant, exp.id)
    with pytest.raises(FinanceNotFoundError):
        await svc.get_expense(tenant, exp.id)
    # keyword heuristic is explicit, not ML
    suggestion = await svc.suggest_category("Delta Airlines", None)
    assert suggestion.suggested_category == "travel"
    assert suggestion.confidence in ("high", "medium", "low")


# ------------------------------------------------------------- ledger/accounts
async def test_accounts_and_append_only_transactions(tenant: TenantContext) -> None:
    svc = _svc()
    acct = await svc.create_account(tenant, S.AccountCreate(
        code="1000", name="Operating", type="asset"))
    tx = await svc.record_transaction(tenant, S.TransactionCreate(
        account_id=acct.id, amount=Decimal("500"), kind="credit",
        memo="client payment"))
    assert tx.id
    txs = await svc.list_transactions(tenant, account_id=acct.id)
    assert len(txs) == 1
    # no update/delete API exists on transactions (append-only by design)
    assert not hasattr(svc, "update_transaction")
    assert not hasattr(svc, "delete_transaction")


# ------------------------------------------------------------------- reports
async def test_ar_aging_buckets(tenant: TenantContext) -> None:
    svc = _svc()
    cust = await _customer(svc, tenant)
    inv = await svc.create_invoice(tenant, S.InvoiceCreate(
        customer_id=cust.id, lines=_lines(), currency="USD",
        due_at=datetime.now(UTC) - timedelta(days=45)))
    await svc.issue_invoice(tenant, inv.id)
    await svc.mark_overdue(tenant)
    aging = await svc.ar_aging(tenant)
    assert aging.total_due == Decimal("300.00")
    bucket_31_60 = next(b for b in aging.buckets if b.bucket == "31-60")
    assert bucket_31_60.total_due == Decimal("300.00")
    assert aging.disclaimer, "finance reports must carry the non-accounting disclaimer"


async def test_cashflow_summary(tenant: TenantContext) -> None:
    svc = _svc()
    inv = await _draft(svc, tenant)
    await svc.issue_invoice(tenant, inv.id)
    await svc.record_payment(tenant, inv.id, S.PaymentCreate(
        amount=Decimal("300.00"), method="card"))
    await svc.record_expense(tenant, S.ExpenseCreate(
        category="software", amount=Decimal("50"), currency="USD"))
    start = datetime.now(UTC) - timedelta(days=1)
    end = datetime.now(UTC) + timedelta(days=1)
    cf = await svc.cashflow(tenant, start, end)
    assert cf.inflow == Decimal("300.00")
    assert cf.outflow == Decimal("50")
    assert cf.disclaimer, "finance reports must carry the non-accounting disclaimer"


async def test_anomaly_scan_flags_duplicates(tenant: TenantContext) -> None:
    svc = _svc()
    for _ in range(2):
        await svc.record_expense(tenant, S.ExpenseCreate(
            category="meals", amount=Decimal("87.40"), currency="USD",
            vendor="Same Cafe"))
    flags = await svc.scan_anomalies(tenant)
    assert flags, "expected at least one anomaly flag for duplicate expenses"
    assert all("fraud" not in f.type.lower() for f in flags), \
        "flags are heuristics, never fraud determinations"


# --------------------------------------------------------------- collections
async def test_collection_reminder_parked_not_sent(tenant: TenantContext) -> None:
    bus = _Bus()
    svc = _svc(bus=bus)
    cust = await _customer(svc, tenant)
    inv = await svc.create_invoice(tenant, S.InvoiceCreate(
        customer_id=cust.id, lines=_lines(), currency="USD",
        due_at=datetime.now(UTC) - timedelta(days=10)))
    await svc.issue_invoice(tenant, inv.id)
    await svc.mark_overdue(tenant)
    candidates = await svc.collection_candidates(tenant, min_days_overdue=1)
    assert candidates, "overdue invoice should be a collections candidate"
    assert candidates[0].invoice_id == inv.id
    reminder = await svc.generate_collection_reminder(tenant, inv.id)
    assert reminder.status == S.ReminderStatus.APPROVAL_PENDING
    assert "Acme" in reminder.body
    assert any(e.topic == "finance.collections.reminder.prepared" for e in bus.events)


async def test_collection_reminder_denied_by_policy(tenant: TenantContext) -> None:
    policy = FakePolicyEngine()
    policy.deny_actions.add("finance.collections.reminder.generate")
    svc = _svc(policy=policy)
    cust = await _customer(svc, tenant)
    inv = await svc.create_invoice(tenant, S.InvoiceCreate(
        customer_id=cust.id, lines=_lines(), currency="USD",
        due_at=datetime.now(UTC) - timedelta(days=10)))
    await svc.issue_invoice(tenant, inv.id)
    await svc.mark_overdue(tenant)
    with pytest.raises(PolicyDeniedError):
        await svc.generate_collection_reminder(tenant, inv.id)


# ---------------------------------------------------------------- NEXORA seam
async def test_nexora_disabled_reports_unsynced_honestly(tenant: TenantContext) -> None:
    svc = _svc()  # default seam is DisabledNexoraSeam
    inv = await _draft(svc, tenant)
    inv = await svc.issue_invoice(tenant, inv.id)
    status = await svc.nexora_status(tenant, "invoice", inv.id)
    assert status.synced is False
    assert status.nexora_ref is None
    assert "not configured" in (status.reason or "")


# ---------------------------------------------------------------- policy gate
async def test_issue_denied_by_policy(tenant: TenantContext) -> None:
    policy = FakePolicyEngine()
    policy.deny_actions.add("finance.invoice.issue")
    svc = _svc(policy=policy)
    inv = await _draft(svc, tenant)
    with pytest.raises(PolicyDeniedError):
        await svc.issue_invoice(tenant, inv.id)
    assert (await svc.get_invoice(tenant, inv.id)).status == S.InvoiceStatus.DRAFT


async def test_financial_mutation_fail_closed_without_policy(
        tenant: TenantContext) -> None:
    svc = FinanceService(policy=None, events=_Bus())
    inv = await _draft(svc, tenant)
    with pytest.raises(PolicyDeniedError, match="no policy engine"):
        await svc.issue_invoice(tenant, inv.id)


# ------------------------------------------------------- cross-tenant safety
async def test_cross_tenant_invisibility(tenant: TenantContext,
                                         tenant_b: TenantContext) -> None:
    svc = _svc()
    cust = await _customer(svc, tenant)
    inv = await svc.create_invoice(tenant, S.InvoiceCreate(
        customer_id=cust.id, lines=_lines(), currency="USD"))
    assert await svc.list_customers(tenant_b) == []
    assert await svc.list_invoices(tenant_b) == []
    assert await svc.list_expenses(tenant_b) == []
    assert await svc.list_accounts(tenant_b) == []
    with pytest.raises(FinanceNotFoundError):
        await svc.get_invoice(tenant_b, inv.id)
