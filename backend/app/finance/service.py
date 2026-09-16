"""FinanceService — foundational operational finance.

> **Not a regulated accounting system.** This service records invoices,
> payments, expenses, accounts, and money-movement transactions for
> day-to-day operations. NEXORA ERP (via ``NexoraSeam``) is the system of
> record for accounting truth. No journal posting, revenue recognition, or
> ledger-balancing logic lives here — that would reimplement the ERP.

Invoice lifecycle (binding):
  DRAFT --issue--> ISSUED --record_payment--> PAID | (due passed) OVERDUE --record_payment--> PAID
  DRAFT --cancel--> CANCELLED
  ISSUED/OVERDUE --void(reason)--> VOIDED        # policy-gated, reversible=False

Issued records are immutable: update only in DRAFT. Voiding keeps the row
(and posts a correcting intent to NEXORA) — nothing financial is deleted.

Consequential actions pass the policy engine BEFORE execution and fail closed:
- ``issue_invoice``, ``void_invoice``, ``cancel_invoice``
- ``record_payment`` (money movement)
- ``generate_collection_reminder`` (+ sending requires approval)
- ``record_transaction``

Expenses and customers are ordinary CRUD; transaction records are
append-only (immutable, ``reverses_id`` for corrections).

Sync intent: after each write, the service calls ``NexoraSeam`` and stores
``nexora_ref`` when acknowledged. ``DisabledNexoraSeam`` returns
``synced=False`` — local record stays authoritative-operational and honest.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.core.contracts import (
    ActionRequest,
    DomainEvent,
    EventBus,
    PolicyDecision,
    PolicyEffect,
    PolicyEngine,
    TenantContext,
)

from .anomalies import scan_anomalies
from .nexora_seam import DisabledNexoraSeam, NexoraSeam
from .reports import ar_aging, cashflow_summary
from .repository import FinanceRepository, InMemoryFinanceRepository
from .schemas import (
    Account,
    AccountCreate,
    AccountType,
    AnomalyFlag,
    ARAgingReport,
    CashflowSummary,
    CategorySuggestion,
    CollectionCandidate,
    CollectionReminder,
    Customer,
    CustomerCreate,
    Expense,
    ExpenseCreate,
    ExpenseUpdate,
    Invoice,
    InvoiceCreate,
    InvoiceStatus,
    InvoiceUpdate,
    InvoiceVoid,
    NexoraSyncStatus,
    Payment,
    PaymentCreate,
    ReminderStatus,
    Transaction,
    TransactionCreate,
)


# ------------------------------------------------------------------ errors
class FinanceError(Exception):
    """Base for finance domain errors."""


class FinanceNotFoundError(FinanceError):
    pass


class InvoiceStateError(FinanceError):
    """Illegal transition for the invoice's status (409)."""


class IssuedInvoiceImmutableError(FinanceError):
    """Attempt to mutate an issued (or later) invoice record."""


class PolicyDeniedError(FinanceError):
    def __init__(self, reasons: tuple[str, ...]):
        self.reasons = reasons
        super().__init__(f"finance action denied by policy: {'; '.join(reasons) or 'no reason'}")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# Heuristic keyword map for expense categorization suggestions. No magic
# model — documented keyword rules; "low" confidence when nothing matches.
_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "travel": ("airline", "flight", "hotel", "uber", "lyft", "taxi", "airbnb"),
    "meals": ("restaurant", "coffee", "lunch", "dinner", "catering", "doordash"),
    "software": ("saas", "subscription", "license", "github", "aws", "openai"),
    "office": ("supplies", "furniture", "printer", "paper", "desk"),
    "utilities": ("electric", "water", "internet", "phone", "gas"),
}


class FinanceService:
    def __init__(
        self,
        repo: FinanceRepository | None = None,
        policy: PolicyEngine | None = None,
        events: EventBus | None = None,
        nexora: NexoraSeam | None = None,
    ) -> None:
        self._repo = repo or InMemoryFinanceRepository()
        self._policy = policy
        self._events = events
        self._nexora = nexora or DisabledNexoraSeam()

    # ---------------------------------------------------------------- customers
    async def create_customer(self, tenant: TenantContext, data: CustomerCreate) -> Customer:
        customer = Customer(id=_new_id(), tenant_id=tenant.tenant_id, name=data.name,
                            email=data.email, org_id=data.org_id, created_at=_utcnow())
        return await self._repo.add_customer(customer)

    async def get_customer(self, tenant: TenantContext, customer_id: str) -> Customer:
        customer = await self._repo.get_customer(tenant.tenant_id, customer_id)
        if customer is None:
            raise FinanceNotFoundError(f"customer {customer_id} not found")
        return customer

    async def list_customers(self, tenant: TenantContext) -> list[Customer]:
        return await self._repo.list_customers(tenant.tenant_id)

    # ---------------------------------------------------------------- invoices
    @staticmethod
    def _invoice_amount(lines: list[Any]) -> Decimal:
        return sum((line.quantity * line.unit_price for line in lines), Decimal("0"))

    async def create_invoice(self, tenant: TenantContext, data: InvoiceCreate) -> Invoice:
        await self.get_customer(tenant, data.customer_id)
        now = _utcnow()
        invoice = Invoice(
            id=_new_id(), tenant_id=tenant.tenant_id,
            number=await self._repo.next_invoice_number(tenant.tenant_id, now.year),
            customer_id=data.customer_id, lines=list(data.lines),
            amount=self._invoice_amount(data.lines), currency=data.currency.upper(),
            status=InvoiceStatus.DRAFT, due_at=data.due_at,
            balance_due=self._invoice_amount(data.lines),
            created_by=tenant.user_id, created_at=now, updated_at=now)
        invoice = await self._repo.add_invoice(invoice)
        await self._emit(tenant, "finance.invoice.created", invoice.id,
                         {"invoice_id": invoice.id, "number": invoice.number,
                          "amount": str(invoice.amount)})
        return invoice

    async def get_invoice(self, tenant: TenantContext, invoice_id: str) -> Invoice:
        invoice = await self._repo.get_invoice(tenant.tenant_id, invoice_id)
        if invoice is None:
            raise FinanceNotFoundError(f"invoice {invoice_id} not found")
        return invoice

    async def list_invoices(self, tenant: TenantContext, status: str | None = None,
                            customer_id: str | None = None) -> list[Invoice]:
        return await self._repo.list_invoices(tenant.tenant_id, status, customer_id)

    async def update_invoice(
        self, tenant: TenantContext, invoice_id: str, data: InvoiceUpdate
    ) -> Invoice:
        invoice = await self.get_invoice(tenant, invoice_id)
        if invoice.status != InvoiceStatus.DRAFT:
            raise IssuedInvoiceImmutableError(
                f"invoice {invoice.number} is {invoice.status.value} — "
                "issued records are immutable; void and reissue instead")
        patch: dict[str, Any] = {}
        if data.lines is not None:
            patch["lines"] = list(data.lines)
            patch["amount"] = self._invoice_amount(data.lines)
            patch["balance_due"] = self._invoice_amount(data.lines)
        if data.due_at is not None:
            patch["due_at"] = data.due_at
        patch["updated_at"] = _utcnow()
        return await self._repo.update_invoice(invoice.model_copy(update=patch))

    async def issue_invoice(self, tenant: TenantContext, invoice_id: str) -> Invoice:
        invoice = await self.get_invoice(tenant, invoice_id)
        if invoice.status != InvoiceStatus.DRAFT:
            raise InvoiceStateError(f"only drafts can be issued (now {invoice.status.value})")
        await self._enforce_policy(
            tenant, action="finance.invoice.issue", resource=f"invoice:{invoice_id}",
            args={"invoice_id": invoice_id, "amount": str(invoice.amount)},
            risk_context={"financial": True, "reversible": False})
        invoice = await self._repo.update_invoice(invoice.model_copy(update={
            "status": InvoiceStatus.ISSUED, "issued_at": _utcnow(), "updated_at": _utcnow()}))
        sync = await self._nexora.push_invoice(tenant, invoice)
        if sync.synced:
            invoice = await self._repo.update_invoice(
                invoice.model_copy(update={"nexora_ref": sync.nexora_ref}))
        await self._emit(tenant, "finance.invoice.issued", invoice.id, {
            "invoice_id": invoice.id, "number": invoice.number,
            "amount": str(invoice.amount), "nexora_ref": sync.nexora_ref})
        return invoice

    async def cancel_invoice(self, tenant: TenantContext, invoice_id: str) -> Invoice:
        invoice = await self.get_invoice(tenant, invoice_id)
        if invoice.status != InvoiceStatus.DRAFT:
            raise InvoiceStateError(f"only drafts can be cancelled (now {invoice.status.value})")
        await self._enforce_policy(
            tenant, action="finance.invoice.cancel", resource=f"invoice:{invoice_id}",
            args={"invoice_id": invoice_id}, risk_context={"reversible": False})
        invoice = await self._repo.update_invoice(invoice.model_copy(update={
            "status": InvoiceStatus.CANCELLED, "updated_at": _utcnow()}))
        await self._emit(tenant, "finance.invoice.cancelled", invoice.id,
                         {"invoice_id": invoice.id})
        return invoice

    async def void_invoice(
        self, tenant: TenantContext, invoice_id: str, data: InvoiceVoid
    ) -> Invoice:
        invoice = await self.get_invoice(tenant, invoice_id)
        if invoice.status not in (InvoiceStatus.ISSUED, InvoiceStatus.OVERDUE):
            raise InvoiceStateError(
                f"only issued/overdue invoices can be voided (now {invoice.status.value})")
        await self._enforce_policy(
            tenant, action="finance.invoice.void", resource=f"invoice:{invoice_id}",
            args={"invoice_id": invoice_id, "reason": data.reason},
            risk_context={"financial": True, "reversible": False, "destructive": True})
        invoice = await self._repo.update_invoice(invoice.model_copy(update={
            "status": InvoiceStatus.VOIDED, "void_reason": data.reason,
            "balance_due": Decimal("0"), "updated_at": _utcnow()}))
        await self._nexora.void_invoice(tenant, invoice.id, data.reason)
        await self._emit(tenant, "finance.invoice.voided", invoice.id, {
            "invoice_id": invoice.id, "reason": data.reason})
        return invoice

    async def mark_overdue(self, tenant: TenantContext, now: datetime | None = None) -> int:
        """Sweep unpaid issued invoices past their due date to OVERDUE. For a worker."""
        now = _as_utc(now or _utcnow())
        count = 0
        for invoice in await self._repo.list_invoices(tenant.tenant_id):
            if (invoice.status == InvoiceStatus.ISSUED and invoice.due_at
                    and _as_utc(invoice.due_at) < now and invoice.balance_due > 0):
                await self._repo.update_invoice(invoice.model_copy(update={
                    "status": InvoiceStatus.OVERDUE, "updated_at": _utcnow()}))
                count += 1
                await self._emit(tenant, "finance.invoice.overdue", invoice.id,
                                 {"invoice_id": invoice.id, "number": invoice.number,
                                  "balance_due": str(invoice.balance_due)})
        return count

    # ---------------------------------------------------------------- payments
    async def record_payment(
        self, tenant: TenantContext, invoice_id: str, data: PaymentCreate
    ) -> Payment:
        invoice = await self.get_invoice(tenant, invoice_id)
        if invoice.status not in (InvoiceStatus.ISSUED, InvoiceStatus.OVERDUE):
            raise InvoiceStateError(
                f"payments require an issued invoice (now {invoice.status.value})")
        if data.idempotency_key:
            existing = await self._repo.get_payment_by_idempotency(
                tenant.tenant_id, data.idempotency_key)
            if existing is not None:
                return existing
        if data.amount > invoice.balance_due:
            raise FinanceError(
                f"payment {data.amount} exceeds balance due {invoice.balance_due} — "
                "overpayments require an explicit credit memo (not implemented)")

        await self._enforce_policy(
            tenant, action="finance.payment.record", resource=f"invoice:{invoice_id}",
            args={"invoice_id": invoice_id, "amount": str(data.amount),
                  "method": data.method},
            risk_context={"financial": True, "reversible": False})

        payment = Payment(
            id=_new_id(), tenant_id=tenant.tenant_id, invoice_id=invoice_id,
            amount=data.amount, method=data.method, reference=data.reference,
            received_at=_as_utc(data.received_at) if data.received_at else _utcnow(),
            created_by=tenant.user_id, created_at=_utcnow())
        payment = await self._repo.add_payment(payment)
        if data.idempotency_key:
            await self._repo.register_payment_idempotency(
                tenant.tenant_id, data.idempotency_key, payment.id)

        new_balance = invoice.balance_due - data.amount
        new_status = (InvoiceStatus.PAID if new_balance <= 0
                      else invoice.status)
        patch: dict[str, Any] = {"balance_due": new_balance, "updated_at": _utcnow()}
        if new_status == InvoiceStatus.PAID:
            patch["status"] = InvoiceStatus.PAID
            patch["paid_at"] = _utcnow()
        invoice = await self._repo.update_invoice(invoice.model_copy(update=patch))

        sync = await self._nexora.push_payment(tenant, payment)
        if sync.synced:
            payment = payment.model_copy(update={"nexora_ref": sync.nexora_ref})

        await self._emit(tenant, "finance.payment.recorded", payment.id, {
            "payment_id": payment.id, "invoice_id": invoice_id,
            "amount": str(payment.amount), "balance_due": str(new_balance),
            "invoice_status": invoice.status.value})
        return payment

    async def list_payments(self, tenant: TenantContext,
                            invoice_id: str | None = None) -> list[Payment]:
        return await self._repo.list_payments(tenant.tenant_id, invoice_id)

    # ---------------------------------------------------------------- expenses
    async def record_expense(self, tenant: TenantContext, data: ExpenseCreate) -> Expense:
        expense = Expense(
            id=_new_id(), tenant_id=tenant.tenant_id, category=data.category,
            amount=data.amount, currency=data.currency.upper(), vendor=data.vendor,
            description=data.description,
            incurred_at=_as_utc(data.incurred_at) if data.incurred_at else _utcnow(),
            receipt_ref=data.receipt_ref, created_by=tenant.user_id, created_at=_utcnow())
        expense = await self._repo.add_expense(expense)
        await self._nexora.push_expense(tenant, expense)
        await self._emit(tenant, "finance.expense.recorded", expense.id, {
            "expense_id": expense.id, "amount": str(expense.amount),
            "category": expense.category})
        return expense

    async def get_expense(self, tenant: TenantContext, expense_id: str) -> Expense:
        expense = await self._repo.get_expense(tenant.tenant_id, expense_id)
        if expense is None:
            raise FinanceNotFoundError(f"expense {expense_id} not found")
        return expense

    async def update_expense(
        self, tenant: TenantContext, expense_id: str, data: ExpenseUpdate
    ) -> Expense:
        expense = await self.get_expense(tenant, expense_id)
        patch = {k: v for k, v in data.model_dump(exclude_unset=True).items()
                 if v is not None}
        return await self._repo.update_expense(expense.model_copy(update=patch))

    async def delete_expense(self, tenant: TenantContext, expense_id: str) -> None:
        # Expenses are local operational records (pre-NEXORA-posting); deletion
        # is allowed with a policy check. Once synced (nexora_ref set), they
        # must be corrected via a reversing entry instead — the seam owns truth.
        expense = await self.get_expense(tenant, expense_id)
        if expense.nexora_ref:
            raise FinanceError(
                "expense already synced to NEXORA — correct via a reversing entry, "
                "do not delete")
        await self._enforce_policy(
            tenant, action="finance.expense.delete", resource=f"expense:{expense_id}",
            args={"expense_id": expense_id},
            risk_context={"financial": True, "reversible": False})
        await self._repo.delete_expense(tenant.tenant_id, expense_id)
        await self._emit(tenant, "finance.expense.deleted", expense_id,
                         {"expense_id": expense_id})

    async def list_expenses(self, tenant: TenantContext) -> list[Expense]:
        return await self._repo.list_expenses(tenant.tenant_id)

    async def suggest_category(self, vendor: str | None, description: str | None
                               ) -> CategorySuggestion:
        """Keyword-based category suggestion — heuristic, not ML. Explicit about it."""
        text = f"{vendor or ''} {description or ''}".lower()
        for category, keywords in _CATEGORY_KEYWORDS.items():
            for kw in keywords:
                if kw in text:
                    return CategorySuggestion(suggested_category=category,
                                              confidence="medium",
                                              matched_keyword=kw)
        return CategorySuggestion(suggested_category="other", confidence="low")

    # ---------------------------------------------------------------- accounts / transactions
    async def create_account(self, tenant: TenantContext, data: AccountCreate) -> Account:
        account = Account(id=_new_id(), tenant_id=tenant.tenant_id, code=data.code,
                          name=data.name, type=data.type, created_at=_utcnow())
        return await self._repo.add_account(account)

    async def list_accounts(self, tenant: TenantContext) -> list[Account]:
        return await self._repo.list_accounts(tenant.tenant_id)

    async def record_transaction(
        self, tenant: TenantContext, data: TransactionCreate
    ) -> Transaction:
        """Append an immutable money-movement record. No update/delete path."""
        account = await self._repo.get_account(tenant.tenant_id, data.account_id)
        if account is None:
            raise FinanceNotFoundError(f"account {data.account_id} not found")
        await self._enforce_policy(
            tenant, action="finance.transaction.record",
            resource=f"account:{data.account_id}",
            args={"account_id": data.account_id, "amount": str(data.amount),
                  "kind": data.kind},
            risk_context={"financial": True, "reversible": False})
        txn = Transaction(
            id=_new_id(), tenant_id=tenant.tenant_id, account_id=data.account_id,
            amount=data.amount, kind=data.kind, memo=data.memo, ref_type=data.ref_type,
            ref_id=data.ref_id, reverses_id=data.reverses_id,
            posted_at=_utcnow(), created_by=tenant.user_id)
        txn = await self._repo.add_transaction(txn)
        await self._emit(tenant, "finance.transaction.recorded", txn.id, {
            "transaction_id": txn.id, "account_id": txn.account_id,
            "amount": str(txn.amount), "kind": txn.kind})
        return txn

    async def list_transactions(self, tenant: TenantContext,
                                account_id: str | None = None) -> list[Transaction]:
        return await self._repo.list_transactions(tenant.tenant_id, account_id)

    # ---------------------------------------------------------------- reports
    async def ar_aging(self, tenant: TenantContext,
                       as_of: datetime | None = None) -> ARAgingReport:
        invoices = await self._repo.list_invoices(tenant.tenant_id)
        return ar_aging(tenant.tenant_id, invoices, as_of or _utcnow())

    async def cashflow(self, tenant: TenantContext, period_start: datetime,
                       period_end: datetime) -> CashflowSummary:
        payments = await self._repo.list_payments(tenant.tenant_id)
        expenses = await self._repo.list_expenses(tenant.tenant_id)
        return cashflow_summary(tenant.tenant_id, payments, expenses, period_start,
                                period_end)

    async def scan_anomalies(self, tenant: TenantContext,
                             now: datetime | None = None) -> list[AnomalyFlag]:
        return scan_anomalies(await self._repo.list_expenses(tenant.tenant_id),
                              await self._repo.list_payments(tenant.tenant_id), now)

    # ---------------------------------------------------------------- collections
    async def collection_candidates(
        self, tenant: TenantContext, min_days_overdue: int = 1,
        now: datetime | None = None,
    ) -> list[CollectionCandidate]:
        now = _as_utc(now or _utcnow())
        candidates: list[CollectionCandidate] = []
        customers = {c.id: c for c in await self._repo.list_customers(tenant.tenant_id)}
        for invoice in await self._repo.list_invoices(tenant.tenant_id):
            if invoice.status != InvoiceStatus.OVERDUE or invoice.balance_due <= 0:
                continue
            due = _as_utc(invoice.due_at) if invoice.due_at else now
            days = (now - due).days
            if days >= min_days_overdue:
                customer = customers.get(invoice.customer_id)
                candidates.append(CollectionCandidate(
                    invoice_id=invoice.id, invoice_number=invoice.number,
                    customer_id=invoice.customer_id,
                    customer_name=customer.name if customer else None,
                    balance_due=invoice.balance_due, days_overdue=days))
        return sorted(candidates, key=lambda c: c.days_overdue, reverse=True)

    async def generate_collection_reminder(
        self, tenant: TenantContext, invoice_id: str
    ) -> CollectionReminder:
        """Draft a collections reminder — policy-gated and approval-required.

        FAIL-CLOSED: if the policy engine requires approval (or is absent), the
        reminder is parked as APPROVAL_PENDING and is never sent by this call.
        Sending happens via the comms package after human approval lands.
        """
        invoice = await self.get_invoice(tenant, invoice_id)
        if invoice.status != InvoiceStatus.OVERDUE or invoice.balance_due <= 0:
            raise InvoiceStateError("collections reminders require an overdue invoice "
                                    "with a positive balance")
        customer = await self.get_customer(tenant, invoice.customer_id)

        await self._enforce_policy(
            tenant, action="finance.collections.reminder.generate",
            resource=f"invoice:{invoice_id}",
            args={"invoice_id": invoice_id, "amount": str(invoice.balance_due)},
            risk_context={"financial": True, "externally_visible": True,
                          "reversible": False})

        body = (
            f"Dear {customer.name},\n\nThis is a friendly reminder that invoice "
            f"{invoice.number} for {invoice.amount} {invoice.currency} has an "
            f"outstanding balance of {invoice.balance_due} {invoice.currency}. "
            "Please let us know if you need a copy of the invoice or wish to "
            "arrange payment.\n\nThank you.")
        reminder = CollectionReminder(
            id=_new_id(), tenant_id=tenant.tenant_id, invoice_id=invoice_id,
            body=body, status=ReminderStatus.APPROVAL_PENDING,
            created_by=tenant.user_id, created_at=_utcnow())
        reminder = await self._repo.add_reminder(reminder)
        await self._emit(tenant, "finance.collections.reminder.prepared", reminder.id, {
            "reminder_id": reminder.id, "invoice_id": invoice_id,
            "status": reminder.status.value})
        return reminder

    async def nexora_status(self, tenant: TenantContext,
                            record_type: str, record_id: str) -> NexoraSyncStatus:
        record: Invoice | Expense | None = None
        if record_type == "invoice":
            record = await self.get_invoice(tenant, record_id)
        elif record_type == "expense":
            record = await self.get_expense(tenant, record_id)
        if record is None:
            raise FinanceNotFoundError(f"{record_type} {record_id} not found")
        ref = getattr(record, "nexora_ref", None)
        return NexoraSyncStatus(
            synced=bool(ref), nexora_ref=ref,
            reason=None if ref else "not yet synced to NEXORA (adapter not configured)")

    # ---------------------------------------------------------------- internals
    async def _enforce_policy(self, tenant: TenantContext, *, action: str,
                              resource: str | None, args: dict[str, Any],
                              risk_context: dict[str, Any]) -> PolicyDecision:
        if self._policy is None:
            raise PolicyDeniedError(("no policy engine configured — refusing financial mutation",))
        try:
            decision = await self._policy.evaluate(ActionRequest(
                tenant=tenant, action=action, resource=resource, args=args,
                risk_context=risk_context))
        except Exception as exc:
            raise PolicyDeniedError((f"policy evaluation failed: {exc}",)) from exc
        if decision.effect == PolicyEffect.DENY:
            raise PolicyDeniedError(decision.reasons)
        if decision.effect == PolicyEffect.REQUIRE_APPROVAL:
            raise PolicyDeniedError(
                ("human approval required for this financial action",))
        return decision

    async def _emit(self, tenant: TenantContext, topic: str, aggregate_id: str,
                    payload: dict[str, Any]) -> None:
        if self._events is None:
            return
        await self._events.publish(DomainEvent(
            topic=topic, tenant_id=tenant.tenant_id, aggregate_id=aggregate_id,
            payload=payload, event_id=_new_id(), occurred_at=_utcnow()))


# Re-export for agents that only know the package: AccountType is part of
# the public schema surface but convenient here too.
__all__ = [
    "Account",
    "AccountCreate",
    "AccountType",
    "AnomalyFlag",
    "ARAgingReport",
    "CashflowSummary",
    "CategorySuggestion",
    "CollectionCandidate",
    "CollectionReminder",
    "Customer",
    "CustomerCreate",
    "Expense",
    "ExpenseCreate",
    "ExpenseUpdate",
    "FinanceError",
    "FinanceNotFoundError",
    "FinanceService",
    "Invoice",
    "InvoiceCreate",
    "InvoiceStateError",
    "InvoiceStatus",
    "InvoiceUpdate",
    "InvoiceVoid",
    "IssuedInvoiceImmutableError",
    "NexoraSyncStatus",
    "Payment",
    "PaymentCreate",
    "PolicyDeniedError",
    "ReminderStatus",
    "Transaction",
    "TransactionCreate",
]
