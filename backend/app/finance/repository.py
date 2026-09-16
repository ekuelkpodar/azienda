"""Repository protocols + in-memory implementations for finance.

Persistence seam: ``FinanceRepository``. ``InMemoryFinanceRepository`` backs
tests and local dev; Postgres SQLAlchemy implementation (``models.py``) is
future work pending ``core/db``.

The invoice-number sequence is process-local here; the Postgres implementation
must use a per-tenant sequence (or advisory lock) to avoid duplicates.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .schemas import Account, CollectionReminder, Customer, Expense, Invoice, Payment, Transaction


@runtime_checkable
class FinanceRepository(Protocol):
    # customers
    async def add_customer(self, customer: Customer) -> Customer: ...
    async def get_customer(self, tenant_id: str, customer_id: str) -> Customer | None: ...
    async def list_customers(self, tenant_id: str) -> list[Customer]: ...
    # invoices
    async def add_invoice(self, invoice: Invoice) -> Invoice: ...
    async def get_invoice(self, tenant_id: str, invoice_id: str) -> Invoice | None: ...
    async def update_invoice(self, invoice: Invoice) -> Invoice: ...
    async def list_invoices(self, tenant_id: str, status: str | None = None,
                            customer_id: str | None = None) -> list[Invoice]: ...
    async def next_invoice_number(self, tenant_id: str, year: int) -> str: ...
    # payments
    async def add_payment(self, payment: Payment) -> Payment: ...
    async def get_payment_by_idempotency(self, tenant_id: str, key: str) -> Payment | None: ...
    async def register_payment_idempotency(self, tenant_id: str, key: str,
                                           payment_id: str) -> None: ...
    async def list_payments(self, tenant_id: str,
                            invoice_id: str | None = None) -> list[Payment]: ...
    # expenses
    async def add_expense(self, expense: Expense) -> Expense: ...
    async def get_expense(self, tenant_id: str, expense_id: str) -> Expense | None: ...
    async def update_expense(self, expense: Expense) -> Expense: ...
    async def delete_expense(self, tenant_id: str, expense_id: str) -> None: ...
    async def list_expenses(self, tenant_id: str) -> list[Expense]: ...
    # accounts / transactions
    async def add_account(self, account: Account) -> Account: ...
    async def get_account(self, tenant_id: str, account_id: str) -> Account | None: ...
    async def list_accounts(self, tenant_id: str) -> list[Account]: ...
    async def add_transaction(self, txn: Transaction) -> Transaction: ...
    async def list_transactions(self, tenant_id: str,
                                account_id: str | None = None) -> list[Transaction]: ...
    # collection reminders
    async def add_reminder(self, reminder: CollectionReminder) -> CollectionReminder: ...
    async def get_reminder(self, tenant_id: str, reminder_id: str) -> CollectionReminder | None: ...
    async def update_reminder(self, reminder: CollectionReminder) -> CollectionReminder: ...
    async def list_reminders(self, tenant_id: str,
                             invoice_id: str | None = None) -> list[CollectionReminder]: ...


class InMemoryFinanceRepository:
    """Process-local repository. Tenant isolation enforced on every access."""

    def __init__(self) -> None:
        self._customers: dict[tuple[str, str], Customer] = {}
        self._invoices: dict[tuple[str, str], Invoice] = {}
        self._payments: dict[tuple[str, str], Payment] = {}
        self._payment_idem: dict[tuple[str, str], str] = {}
        self._expenses: dict[tuple[str, str], Expense] = {}
        self._accounts: dict[tuple[str, str], Account] = {}
        self._transactions: dict[tuple[str, str], Transaction] = {}
        self._reminders: dict[tuple[str, str], CollectionReminder] = {}
        self._invoice_seq: dict[tuple[str, int], int] = {}

    async def add_customer(self, customer: Customer) -> Customer:
        self._customers[(customer.tenant_id, customer.id)] = customer
        return customer

    async def get_customer(self, tenant_id: str, customer_id: str) -> Customer | None:
        return self._customers.get((tenant_id, customer_id))

    async def list_customers(self, tenant_id: str) -> list[Customer]:
        return [c for (t, _), c in self._customers.items() if t == tenant_id]

    async def add_invoice(self, invoice: Invoice) -> Invoice:
        self._invoices[(invoice.tenant_id, invoice.id)] = invoice
        return invoice

    async def get_invoice(self, tenant_id: str, invoice_id: str) -> Invoice | None:
        return self._invoices.get((tenant_id, invoice_id))

    async def update_invoice(self, invoice: Invoice) -> Invoice:
        self._invoices[(invoice.tenant_id, invoice.id)] = invoice
        return invoice

    async def list_invoices(self, tenant_id: str, status: str | None = None,
                            customer_id: str | None = None) -> list[Invoice]:
        return [
            i for (t, _), i in self._invoices.items()
            if t == tenant_id
            and (status is None or i.status.value == status)
            and (customer_id is None or i.customer_id == customer_id)
        ]

    async def next_invoice_number(self, tenant_id: str, year: int) -> str:
        seq = self._invoice_seq.get((tenant_id, year), 0) + 1
        self._invoice_seq[(tenant_id, year)] = seq
        return f"INV-{year}-{seq:04d}"

    async def add_payment(self, payment: Payment) -> Payment:
        self._payments[(payment.tenant_id, payment.id)] = payment
        return payment

    async def get_payment_by_idempotency(self, tenant_id: str, key: str) -> Payment | None:
        payment_id = self._payment_idem.get((tenant_id, key))
        return self._payments.get((tenant_id, payment_id)) if payment_id else None

    async def register_payment_idempotency(self, tenant_id: str, key: str,
                                           payment_id: str) -> None:
        self._payment_idem[(tenant_id, key)] = payment_id

    async def list_payments(self, tenant_id: str,
                            invoice_id: str | None = None) -> list[Payment]:
        return [p for (t, _), p in self._payments.items()
                if t == tenant_id and (invoice_id is None or p.invoice_id == invoice_id)]

    async def add_expense(self, expense: Expense) -> Expense:
        self._expenses[(expense.tenant_id, expense.id)] = expense
        return expense

    async def get_expense(self, tenant_id: str, expense_id: str) -> Expense | None:
        return self._expenses.get((tenant_id, expense_id))

    async def update_expense(self, expense: Expense) -> Expense:
        self._expenses[(expense.tenant_id, expense.id)] = expense
        return expense

    async def delete_expense(self, tenant_id: str, expense_id: str) -> None:
        self._expenses.pop((tenant_id, expense_id), None)

    async def list_expenses(self, tenant_id: str) -> list[Expense]:
        return [e for (t, _), e in self._expenses.items() if t == tenant_id]

    async def add_account(self, account: Account) -> Account:
        self._accounts[(account.tenant_id, account.id)] = account
        return account

    async def get_account(self, tenant_id: str, account_id: str) -> Account | None:
        return self._accounts.get((tenant_id, account_id))

    async def list_accounts(self, tenant_id: str) -> list[Account]:
        return [a for (t, _), a in self._accounts.items() if t == tenant_id]

    async def add_transaction(self, txn: Transaction) -> Transaction:
        self._transactions[(txn.tenant_id, txn.id)] = txn
        return txn

    async def list_transactions(self, tenant_id: str,
                                account_id: str | None = None) -> list[Transaction]:
        return [t for (ten, _), t in self._transactions.items()
                if ten == tenant_id and (account_id is None or t.account_id == account_id)]

    async def add_reminder(self, reminder: CollectionReminder) -> CollectionReminder:
        self._reminders[(reminder.tenant_id, reminder.id)] = reminder
        return reminder

    async def get_reminder(self, tenant_id: str, reminder_id: str) -> CollectionReminder | None:
        return self._reminders.get((tenant_id, reminder_id))

    async def update_reminder(self, reminder: CollectionReminder) -> CollectionReminder:
        self._reminders[(reminder.tenant_id, reminder.id)] = reminder
        return reminder

    async def list_reminders(self, tenant_id: str,
                             invoice_id: str | None = None) -> list[CollectionReminder]:
        return [r for (t, _), r in self._reminders.items()
                if t == tenant_id and (invoice_id is None or r.invoice_id == invoice_id)]
