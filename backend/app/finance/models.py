"""SQLAlchemy ORM models for finance (FOUNDATIONAL). Mirrors DATABASE.md §11.

Tables: finance_customers, invoices, payments, finance_expenses,
finance_accounts, finance_transactions, collection_reminders.

> Not a regulated accounting system — see schemas.py disclaimer. NEXORA ERP
> (via NexoraSeam) is the system of record.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.models import GUID


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class FinanceCustomerModel(Base):
    __tablename__ = "finance_customers"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(sa.String(320), nullable=True)
    org_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    nexora_ref: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_utcnow)


class InvoiceModel(Base):
    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    number: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    customer_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    lines: Mapped[list[dict[str, Any]]] = mapped_column(sa.JSON, nullable=False, default=list)
    amount: Mapped[float] = mapped_column(sa.Numeric(19, 4), nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="USD")
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="draft",
                                        index=True)
    issued_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True),
                                                       nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    balance_due: Mapped[float] = mapped_column(sa.Numeric(19, 4), nullable=False)
    nexora_ref: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    void_reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_utcnow)

    __table_args__ = (sa.UniqueConstraint("tenant_id", "number", name="uq_invoice_number"),)


class PaymentModel(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    invoice_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    amount: Mapped[float] = mapped_column(sa.Numeric(19, 4), nullable=False)
    method: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    reference: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    received_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    nexora_ref: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    created_by: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_utcnow)


class FinanceExpenseModel(Base):
    __tablename__ = "finance_expenses"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    category: Mapped[str] = mapped_column(sa.String(100), nullable=False, index=True)
    amount: Mapped[float] = mapped_column(sa.Numeric(19, 4), nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="USD")
    vendor: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    incurred_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    receipt_ref: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    nexora_ref: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    created_by: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_utcnow)


class FinanceAccountModel(Base):
    __tablename__ = "finance_accounts"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    code: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    type: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_utcnow)

    __table_args__ = (sa.UniqueConstraint("tenant_id", "code", name="uq_account_code"),)


class FinanceTransactionModel(Base):
    __tablename__ = "finance_transactions"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    amount: Mapped[float] = mapped_column(sa.Numeric(19, 4), nullable=False)
    kind: Mapped[str] = mapped_column(sa.String(8), nullable=False)
    memo: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    ref_type: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)
    ref_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    reverses_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    posted_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    created_by: Mapped[str | None] = mapped_column(GUID(), nullable=True)


class CollectionReminderModel(Base):
    __tablename__ = "collection_reminders"

    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    invoice_id: Mapped[str] = mapped_column(GUID(), nullable=False, index=True)
    body: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(24), nullable=False,
                                        default="approval_pending")
    approval_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_utcnow)
