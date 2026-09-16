"""Pure reporting functions for finance (no I/O — fully unit-testable).

AR aging and cash-flow summaries are computed from record lists supplied by
the service. Buckets follow the conventional 30-day aging bands.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from .schemas import (
    ARAgingBucket,
    ARAgingReport,
    CashflowSummary,
    Expense,
    Invoice,
    InvoiceStatus,
    Payment,
)

_UNPAID = {InvoiceStatus.ISSUED, InvoiceStatus.OVERDUE}


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def ar_aging(tenant_id: str, invoices: list[Invoice], as_of: datetime) -> ARAgingReport:
    """Age unpaid invoices into 30-day buckets by days past due."""
    as_of = _as_utc(as_of)
    counts: dict[str, int] = {"current": 0, "1-30": 0, "31-60": 0, "61-90": 0,
                              "90+": 0}
    totals: dict[str, Decimal] = {"current": Decimal("0"), "1-30": Decimal("0"),
                                  "31-60": Decimal("0"), "61-90": Decimal("0"),
                                  "90+": Decimal("0")}
    for invoice in invoices:
        if invoice.status not in _UNPAID or invoice.balance_due <= 0:
            continue
        due = _as_utc(invoice.due_at) if invoice.due_at else _as_utc(invoice.issued_at or as_of)
        days_past_due = (as_of - due).days
        if days_past_due <= 0:
            band = "current"
        elif days_past_due <= 30:
            band = "1-30"
        elif days_past_due <= 60:
            band = "31-60"
        elif days_past_due <= 90:
            band = "61-90"
        else:
            band = "90+"
        counts[band] += 1
        totals[band] += invoice.balance_due

    result_buckets = [ARAgingBucket(bucket=name, invoice_count=counts[name],
                                   total_due=totals[name]) for name in counts]
    total = sum(totals.values(), Decimal("0"))
    return ARAgingReport(tenant_id=tenant_id, as_of=as_of, buckets=result_buckets,
                         total_due=total)


def cashflow_summary(tenant_id: str, payments: list[Payment], expenses: list[Expense],
                     period_start: datetime, period_end: datetime) -> CashflowSummary:
    """Cash-basis inflow (payments received) vs outflow (expenses incurred)."""
    start, end = _as_utc(period_start), _as_utc(period_end)
    inflow = sum((p.amount for p in payments
                  if start <= _as_utc(p.received_at) <= end), Decimal("0"))
    outflow = sum((e.amount for e in expenses
                   if start <= _as_utc(e.incurred_at) <= end), Decimal("0"))
    return CashflowSummary(tenant_id=tenant_id, period_start=start, period_end=end,
                           inflow=inflow, outflow=outflow, net=inflow - outflow)
