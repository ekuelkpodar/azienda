"""Rule-based anomaly flagging (pure logic — heuristics, NOT fraud detection).

Rules (each documented with its rationale and limits):
1. ``expense_outlier`` — an expense > 3x the median of its category over the
   trailing 90 days (needs >= 3 prior data points; otherwise skipped — a
   single data point is not a baseline).
2. ``duplicate_expense`` — same vendor + same amount within 7 days (possible
   double-entry; excludes the record itself).
3. ``round_amount_payment`` — payment >= $1,000 with a round-hundred amount.
   Weak signal, info severity only; many legitimate payments are round.

Every flag carries an explicit note: rule-based heuristic, requires human
review. This module never blocks a transaction — it only flags.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import median

from .schemas import AnomalyFlag, Expense, Payment

_NOTE = ("Rule-based heuristic only — not fraud detection. Requires human review.")


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def scan_anomalies(expenses: list[Expense], payments: list[Payment],
                   now: datetime | None = None) -> list[AnomalyFlag]:
    now = _as_utc(now or datetime.now(UTC))
    flags: list[AnomalyFlag] = []
    flags.extend(_expense_outliers(expenses, now))
    flags.extend(_duplicate_expenses(expenses))
    flags.extend(_round_payments(payments))
    return flags


def _expense_outliers(expenses: list[Expense], now: datetime) -> list[AnomalyFlag]:
    flags: list[AnomalyFlag] = []
    cutoff = now - timedelta(days=90)
    by_category: dict[str, list[Decimal]] = {}
    for e in expenses:
        if _as_utc(e.incurred_at) >= cutoff:
            by_category.setdefault(e.category, []).append(e.amount)
    for e in expenses:
        baseline = [a for a in by_category.get(e.category, []) if a != e.amount]
        if len(baseline) < 3:
            continue
        med = median(baseline)
        if med > 0 and e.amount > 3 * med:
            flags.append(AnomalyFlag(
                type="expense_outlier", severity="warning",
                entity_type="expense", entity_id=e.id,
                reason=(f"amount {e.amount} is >3x the 90-day category median "
                        f"({med}) for '{e.category}' (n={len(baseline)})"),
                note=_NOTE))
    return flags


def _duplicate_expenses(expenses: list[Expense]) -> list[AnomalyFlag]:
    flags: list[AnomalyFlag] = []
    seen: dict[tuple[str, Decimal], Expense] = {}
    for e in sorted(expenses, key=lambda x: _as_utc(x.incurred_at)):
        if not e.vendor:
            continue
        key = (e.vendor.strip().lower(), e.amount)
        prior = seen.get(key)
        if prior and abs((_as_utc(e.incurred_at)
                          - _as_utc(prior.incurred_at)).days) <= 7:
            flags.append(AnomalyFlag(
                type="duplicate_expense", severity="warning",
                entity_type="expense", entity_id=e.id,
                reason=(f"same vendor '{e.vendor}' and amount {e.amount} as expense "
                        f"{prior.id} within 7 days — possible double entry"),
                note=_NOTE))
        else:
            seen[key] = e
    return flags


def _round_payments(payments: list[Payment]) -> list[AnomalyFlag]:
    flags: list[AnomalyFlag] = []
    for p in payments:
        if p.amount >= Decimal("1000") and p.amount % Decimal("100") == 0:
            flags.append(AnomalyFlag(
                type="round_amount_payment", severity="info",
                entity_type="payment", entity_id=p.id,
                reason=(f"payment of {p.amount} is a round hundred-dollar amount — "
                        f"weak signal, commonly legitimate"),
                note=_NOTE))
    return flags
