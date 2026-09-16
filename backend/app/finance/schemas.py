# ruff: noqa: UP042 -- keep (str, Enum) for consistency across all bizapp schemas
"""Pydantic schemas for the finance package (FOUNDATIONAL).

> **Disclaimer:** this module is an operational finance cache, NOT a regulated
> accounting system. It records invoices, payments, expenses, and simple
> money-movement records for day-to-day business operations. The system of
> record for accounting truth is NEXORA ERP, reached through the
> ``NexoraSeam`` interface (``nexora_seam.py``). Do not use this module for
> tax filing, statutory reporting, or audited financial statements without
> qualified accounting review.

Money is Decimal; datetimes UTC; every row carries tenant_id.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field


class InvoiceStatus(str, Enum):
    DRAFT = "draft"
    ISSUED = "issued"
    PAID = "paid"
    OVERDUE = "overdue"
    VOIDED = "voided"
    CANCELLED = "cancelled"


class AccountType(str, Enum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"


class ReminderStatus(str, Enum):
    PREPARED = "prepared"      # drafted, not sent
    SENT = "sent"
    APPROVAL_PENDING = "approval_pending"


# ------------------------------------------------------------------ customers
class Customer(BaseModel):
    id: str
    tenant_id: str
    name: str
    email: str | None = None
    org_id: str | None = None            # crm organization link (future)
    nexora_ref: str | None = None        # NEXORA ERP customer reference
    created_at: datetime


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    org_id: str | None = None


# ------------------------------------------------------------------ invoices
class InvoiceLine(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    quantity: Decimal = Field(default=Decimal("1"), gt=0)
    unit_price: Decimal = Field(ge=0)


class Invoice(BaseModel):
    id: str
    tenant_id: str
    number: str                          # unique per tenant, e.g. INV-2026-0001
    customer_id: str
    lines: list[InvoiceLine] = Field(default_factory=list)
    amount: Decimal                      # sum(lines)
    currency: str = "USD"
    status: InvoiceStatus
    issued_at: datetime | None = None
    due_at: datetime | None = None
    paid_at: datetime | None = None
    balance_due: Decimal = Decimal("0")
    nexora_ref: str | None = None
    void_reason: str | None = None
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


class InvoiceCreate(BaseModel):
    customer_id: str
    lines: list[InvoiceLine] = Field(min_length=1)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    due_at: datetime | None = None


class InvoiceUpdate(BaseModel):
    """Only valid while the invoice is a DRAFT — issued records are immutable."""
    lines: list[InvoiceLine] | None = Field(default=None, min_length=1)
    due_at: datetime | None = None


class InvoiceVoid(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


# ------------------------------------------------------------------ payments
class Payment(BaseModel):
    id: str
    tenant_id: str
    invoice_id: str
    amount: Decimal
    method: str                          # "card" | "ach" | "cash" | "check" | ...
    reference: str | None = None
    received_at: datetime
    nexora_ref: str | None = None
    created_by: str | None = None
    created_at: datetime


class PaymentCreate(BaseModel):
    amount: Decimal = Field(gt=0)
    method: str = Field(min_length=1, max_length=50)
    reference: str | None = Field(default=None, max_length=200)
    received_at: datetime | None = None
    idempotency_key: str | None = None


# ------------------------------------------------------------------ expenses
class Expense(BaseModel):
    id: str
    tenant_id: str
    category: str
    amount: Decimal
    currency: str = "USD"
    vendor: str | None = None
    description: str | None = None
    incurred_at: datetime
    receipt_ref: str | None = None
    nexora_ref: str | None = None
    created_by: str | None = None
    created_at: datetime


class ExpenseCreate(BaseModel):
    category: str = Field(min_length=1, max_length=100)
    amount: Decimal = Field(gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    vendor: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    incurred_at: datetime | None = None
    receipt_ref: str | None = None


class ExpenseUpdate(BaseModel):
    category: str | None = Field(default=None, min_length=1, max_length=100)
    amount: Decimal | None = Field(default=None, gt=0)
    vendor: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    incurred_at: datetime | None = None


class CategorySuggestion(BaseModel):
    suggested_category: str
    confidence: str                       # "high" | "medium" | "low" — heuristic
    matched_keyword: str | None = None


# ------------------------------------------------------------------ accounts / transactions
class Account(BaseModel):
    id: str
    tenant_id: str
    code: str                            # unique per tenant, e.g. "1000"
    name: str
    type: AccountType
    is_active: bool = True
    created_at: datetime


class AccountCreate(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=200)
    type: AccountType


class Transaction(BaseModel):
    """A money-movement record. Immutable once posted (no update/delete).

    This is an operational audit trail, NOT a double-entry ledger — the real
    ledger lives in NEXORA ERP. ``reverses_id`` links a correcting entry to the
    original instead of editing it.
    """
    id: str
    tenant_id: str
    account_id: str
    amount: Decimal                      # always positive; direction in `kind`
    kind: str                            # "debit" | "credit"
    memo: str | None = None
    ref_type: str | None = None          # "invoice" | "payment" | "expense" | ...
    ref_id: str | None = None
    reverses_id: str | None = None
    posted_at: datetime
    created_by: str | None = None


class TransactionCreate(BaseModel):
    account_id: str
    amount: Decimal = Field(gt=0)
    kind: str = Field(pattern="^(debit|credit)$")
    memo: str | None = Field(default=None, max_length=500)
    ref_type: str | None = Field(default=None, max_length=50)
    ref_id: str | None = None
    reverses_id: str | None = None


# ------------------------------------------------------------------ reports
class ARAgingBucket(BaseModel):
    bucket: str                          # "current" | "1-30" | "31-60" | "61-90" | "90+"
    invoice_count: int
    total_due: Decimal


class ARAgingReport(BaseModel):
    tenant_id: str
    as_of: datetime
    buckets: list[ARAgingBucket]
    total_due: Decimal
    disclaimer: str = ("Foundational AR aging from operational records only — "
                       "not audited financials. NEXORA ERP is the system of record.")


class CashflowSummary(BaseModel):
    tenant_id: str
    period_start: datetime
    period_end: datetime
    inflow: Decimal
    outflow: Decimal
    net: Decimal
    disclaimer: str = ("Cash-basis summary from recorded payments/expenses — "
                       "not audited financials. NEXORA ERP is the system of record.")


class AnomalyFlag(BaseModel):
    type: str                            # "expense_outlier" | "duplicate_expense" | ...
    severity: str                        # "info" | "warning"
    entity_type: str
    entity_id: str
    reason: str
    note: str = ("Rule-based heuristic only — not fraud detection. "
                 "Requires human review.")


# ------------------------------------------------------------------ collections
class CollectionCandidate(BaseModel):
    invoice_id: str
    invoice_number: str
    customer_id: str
    customer_name: str | None
    balance_due: Decimal
    days_overdue: int


class CollectionReminder(BaseModel):
    id: str
    tenant_id: str
    invoice_id: str
    body: str
    status: ReminderStatus
    approval_id: str | None = None
    sent_at: datetime | None = None
    created_by: str | None = None
    created_at: datetime


class NexoraSyncStatus(BaseModel):
    synced: bool
    nexora_ref: str | None = None
    reason: str | None = None
