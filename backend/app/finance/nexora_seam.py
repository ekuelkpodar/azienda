"""NexoraSeam — the integration contract with NEXORA ERP.

ARCHITECTURE.md §3 (binding): **financial truth lives in NEXORA ERP**
(github.com/ekuelkpodar/nexora-erp). Azienda's ``finance/`` module is
FOUNDATIONAL — an operational cache for invoices/payments/expenses — and must
NEVER reimplement ERP accounting logic (immutable posted journals, multi-book
accounting, revenue recognition, bank reconciliation, etc.).

This file defines the seam as a protocol. The real adapter (HTTP client
against the NEXORA API, or an MCP tool call) is future work; until it exists,
``DisabledNexoraSeam`` is injected, which records the *intent* to sync and
returns ``synced=False`` with an explicit reason. Local records remain the
operational source; nothing pretends to have posted to NEXORA.

Design notes for the future adapter:
- All calls are idempotent (pass the Azienda record id as the idempotency key).
- Consequential financial actions route through NEXORA's approval path —
  the seam methods that post return an approval reference when NEXORA parks
  the action for human review.
- Sync failures must never lose local data: the local record is written
  first, the sync is retried by a worker, and ``nexora_ref`` stays null until
  NEXORA acknowledges.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from app.core.contracts import TenantContext

from .schemas import Expense, Invoice, Payment


@dataclass(frozen=True)
class NexoraSyncResult:
    synced: bool
    nexora_ref: str | None = None
    reason: str | None = None


@runtime_checkable
class NexoraSeam(Protocol):
    """Future integration contract with the NEXORA ERP product.

    Implementations translate Azienda's operational records into NEXORA API
    calls. Every method is idempotent on the Azienda record id.
    """

    async def push_invoice(self, tenant: TenantContext, invoice: Invoice) -> NexoraSyncResult:
        """Upsert the invoice in NEXORA (AR). Returns the NEXORA reference."""
        ...

    async def push_payment(self, tenant: TenantContext, payment: Payment) -> NexoraSyncResult:
        """Record the payment against the invoice in NEXORA."""
        ...

    async def push_expense(self, tenant: TenantContext, expense: Expense) -> NexoraSyncResult:
        """Record the expense in NEXORA."""
        ...

    async def void_invoice(self, tenant: TenantContext, invoice_id: str,
                           reason: str) -> NexoraSyncResult:
        """Void the invoice in NEXORA (keeps the audit trail on both sides)."""
        ...

    async def customer_balance(self, tenant: TenantContext,
                               customer_id: str) -> Decimal | None:
        """Authoritative AR balance from NEXORA. None when unavailable."""
        ...


class DisabledNexoraSeam:
    """Default seam: NEXORA sync is not configured.

    Honest no-op — records the sync intent in the result instead of pretending
    to post. The finance service treats ``synced=False`` as 'local record only'
    and keeps ``nexora_ref`` null.
    """

    async def push_invoice(self, tenant: TenantContext, invoice: Invoice) -> NexoraSyncResult:
        return NexoraSyncResult(
            synced=False,
            reason="nexora adapter not configured — invoice kept as local operational record")

    async def push_payment(self, tenant: TenantContext, payment: Payment) -> NexoraSyncResult:
        return NexoraSyncResult(
            synced=False,
            reason="nexora adapter not configured — payment kept as local operational record")

    async def push_expense(self, tenant: TenantContext, expense: Expense) -> NexoraSyncResult:
        return NexoraSyncResult(
            synced=False,
            reason="nexora adapter not configured — expense kept as local operational record")

    async def void_invoice(self, tenant: TenantContext, invoice_id: str,
                           reason: str) -> NexoraSyncResult:
        return NexoraSyncResult(
            synced=False,
            reason="nexora adapter not configured — void recorded locally only")

    async def customer_balance(self, tenant: TenantContext,
                               customer_id: str) -> Decimal | None:
        return None
