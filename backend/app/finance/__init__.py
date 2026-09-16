"""Package: finance — FOUNDATIONAL operational finance (not an accounting system).

> **Disclaimer:** operational records for invoices/payments/expenses only.
> Financial truth lives in NEXORA ERP, reached through the ``NexoraSeam``
> interface. Do not use for tax filing, statutory reporting, or audited
> statements without qualified accounting review.

Public interface (other packages/agents import ONLY these):
  - ``FinanceService`` — customers, invoices, payments, expenses, accounts,
    transactions, reports, anomalies, collections.
  - ``FinanceRepository`` / ``InMemoryFinanceRepository`` — persistence seam.
  - ``NexoraSeam`` / ``DisabledNexoraSeam`` — NEXORA ERP integration contract.
  - ``ar_aging``, ``cashflow_summary`` (``reports.py``) — pure reporting logic.
  - ``scan_anomalies`` (``anomalies.py``) — rule-based heuristic flagging.
  - schemas (``Invoice``, ``Payment``, ``Expense``, ``Transaction``, ...).

See README.md for the boundary contract.
"""
from .anomalies import scan_anomalies
from .nexora_seam import DisabledNexoraSeam, NexoraSeam, NexoraSyncResult
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
from .service import (
    FinanceError,
    FinanceNotFoundError,
    FinanceService,
    InvoiceStateError,
    IssuedInvoiceImmutableError,
    PolicyDeniedError,
)

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
    "DisabledNexoraSeam",
    "Expense",
    "ExpenseCreate",
    "ExpenseUpdate",
    "FinanceError",
    "FinanceNotFoundError",
    "FinanceRepository",
    "FinanceService",
    "InMemoryFinanceRepository",
    "Invoice",
    "InvoiceCreate",
    "InvoiceStateError",
    "InvoiceStatus",
    "InvoiceUpdate",
    "InvoiceVoid",
    "IssuedInvoiceImmutableError",
    "NexoraSyncResult",
    "NexoraSyncStatus",
    "NexoraSeam",
    "Payment",
    "PaymentCreate",
    "PolicyDeniedError",
    "ReminderStatus",
    "Transaction",
    "TransactionCreate",
    "ar_aging",
    "cashflow_summary",
    "scan_anomalies",
]
